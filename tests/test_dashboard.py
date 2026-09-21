import time
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.auth import hash_password, verify_password, token_hash
from app.dashboard import create_dashboard
from app.database import Account, Base, WebSession
from app.schemas import Card, SourceVersion
from tests.fixtures import card, source

ORIGIN = "https://criteria.bcanatomy.site"
PASSWORD = "SyntheticTestPassword123!"
HASH = hash_password(PASSWORD)


@pytest.fixture
def client(tmp_path):
    app = create_dashboard(f"sqlite:///{tmp_path / 'web.db'}", origin=ORIGIN)
    Base.metadata.create_all(app.state.engine)
    with app.state.sessions.begin() as db:
        db.add(Account(email="admin@example.com", password_hash=HASH, role="admin", active=True))
        db.add(Account(email="reviewer@example.com", password_hash=HASH, role="reviewer", active=True))
    app.state.service.register_source(SourceVersion.model_validate(source()))
    app.state.service.add_revision("test", 0, Card.model_validate(card()), "fixture-author")
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


def login(client, role="admin"):
    response = client.post("/web/login", json={"email": f"{role}@example.com", "password": PASSWORD}, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    session = client.get("/web/session").json()
    return {"Origin": ORIGIN, "X-CSRF-Token": session["csrf"]}


def publish(client, headers, revision=1):
    return client.post("/web/cards/test/publish", json={"expected_revision": revision, "reason": "fixture review only", "evidence_checked": True, "applicability_checked": True}, headers=headers)


def test_public_home_login_and_private_page(client):
    assert client.get("/").status_code == 200
    assert "Đăng nhập" in client.get("/login").text
    assert client.get("/dashboard", follow_redirects=False).status_code == 303
    assert client.get("/public/cards").json() == {"results": []}
    assert client.get("/assets/dashboard.html").status_code == 404


@pytest.mark.parametrize("endpoint", ["/web/cards", "/web/sources", "/web/featured", "/web/session", "/web/cards/test/history", "/web/research-jobs"])
def test_private_reads_require_login(client, endpoint):
    assert client.get(endpoint).status_code == 401


def test_login_cookie_and_session_hash(client):
    response = client.post("/web/login", json={"email":"admin@example.com","password":PASSWORD}, headers={"Origin":ORIGIN})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Domain=" not in cookie
    assert client.get("/dashboard").status_code == 200
    raw = client.cookies.get("__Host-criteria_session")
    with client.app.state.sessions() as db:
        session = db.scalar(select(WebSession))
        assert session.token_hash == token_hash(raw) and raw not in session.token_hash


def test_login_requires_same_origin_and_correct_password(client):
    data={"email":"admin@example.com","password":PASSWORD}
    assert client.post("/web/login",json=data).status_code == 403
    assert client.post("/web/login",json=data,headers={"Origin":"https://evil.example"}).status_code == 403
    data["password"]="wrong"
    assert client.post("/web/login",json=data,headers={"Origin":ORIGIN}).status_code == 401


def test_csrf_required_for_edits_and_logout(client):
    headers=login(client)
    assert client.post("/web/logout",json={},headers={"Origin":ORIGIN}).status_code == 403
    assert client.post("/web/cards/test/revisions",json={"expected_revision":1,"card":card()},headers={"Origin":ORIGIN}).status_code == 403
    assert client.post("/web/logout",json={},headers=headers).status_code == 200
    assert client.get("/web/session").status_code == 401


def test_reviewer_cannot_select_public_content_or_register_sources(client):
    headers=login(client,"reviewer")
    assert publish(client,headers).status_code == 200
    assert client.post("/web/featured",json={"slot":1,"card_id":"test","revision":1},headers=headers).status_code == 403
    assert client.post("/web/sources",json=source(),headers=headers).status_code == 403


def test_public_requires_explicit_selection_and_redacts_internal_evidence(client):
    headers=login(client)
    assert client.post("/web/featured",json={"slot":1,"card_id":"test","revision":1},headers=headers).status_code == 409
    assert publish(client,headers).status_code == 200
    assert client.get("/public/cards").json()["results"] == []
    assert client.post("/web/featured",json={"slot":1,"card_id":"test","revision":1},headers=headers).status_code == 200
    result=client.get("/public/cards")
    assert len(result.json()["results"]) == 1
    assert "archive_reference" not in result.text and "document_sha256" not in result.text and "SYNTHETIC length" not in result.text
    assert result.json()["results"][0]["applicability"]["exclusions"] == ["all patients"]
    assert result.headers["cache-control"] == "no-store"


def test_public_revision_is_pinned_and_withdrawal_hides_it(client):
    headers=login(client);publish(client,headers)
    client.post("/web/featured",json={"slot":1,"card_id":"test","revision":1},headers=headers)
    updated=card();updated["name_vi"]="NEW DRAFT"
    assert client.post("/web/cards/test/revisions",json={"expected_revision":1,"card":updated},headers=headers).status_code == 201
    assert "NEW DRAFT" not in client.get("/public/cards").text
    assert publish(client,headers,2).status_code == 200
    assert client.get("/public/cards").json()["results"] == []
    client.post("/web/featured",json={"slot":1,"card_id":"test","revision":2},headers=headers)
    assert len(client.get("/public/cards").json()["results"]) == 1
    assert client.post("/web/cards/test/withdraw",json={"expected_revision":2,"reason":"test"},headers=headers).status_code == 200
    assert client.get("/public/cards").json()["results"] == []


def test_max_six_public_slots(client):
    headers=login(client);publish(client,headers)
    assert client.post("/web/featured",json={"slot":7,"card_id":"test","revision":1},headers=headers).status_code == 422


def test_expired_or_disabled_sessions_rejected(client):
    login(client)
    with client.app.state.sessions.begin() as db:
        session=db.scalar(select(WebSession));session.expires=int(time.time())-1
    assert client.get("/web/cards").status_code == 401
    login(client)
    with client.app.state.sessions.begin() as db:
        db.get(Account,"admin@example.com").active=False
    assert client.get("/web/cards").status_code == 401


def test_rate_limit(client):
    data={"email":"unknown@example.com","password":"incorrect"}
    for _ in range(5):
        assert client.post("/web/login",json=data,headers={"Origin":ORIGIN}).status_code == 401
    assert client.post("/web/login",json=data,headers={"Origin":ORIGIN}).status_code == 429


def test_password_hashes_salted_and_no_plaintext():
    other=hash_password(PASSWORD)
    assert other != HASH and PASSWORD not in HASH
    assert verify_password(PASSWORD,HASH) and not verify_password("incorrect",HASH)


def test_audit_tracks_individual_reviewer(client):
    headers=login(client,"reviewer");publish(client,headers)
    assert client.get("/web/cards/test/history").json()[-1]["actor"] == "reviewer@example.com"


def test_security_headers(client):
    response=client.get("/")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "unsafe-inline" not in response.headers["content-security-policy"]
