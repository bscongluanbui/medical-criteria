import os
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from app.api import create_app
from app.database import Base, Revision
from app.schemas import Card
from tests.fixtures import card, source

TOKENS = {"reader": "r" * 40, "reviewer": "v" * 40, "admin": "a" * 40}


def auth(role):
    return {"Authorization": "Bearer " + TOKENS[role]}


@pytest.fixture
def client(tmp_path):
    application = create_app(f"sqlite:///{tmp_path / 'test.db'}", TOKENS)
    Base.metadata.create_all(application.state.engine)
    with TestClient(application) as client:
        assert client.post("/sources", json=source(), headers=auth("admin")).status_code == 201
        yield client


def draft(client, expected=0, payload=None):
    return client.post("/cards/test/revisions", json={"expected_revision": expected, "card": payload or card()}, headers=auth("reviewer"))


def publish(client, revision=1):
    return client.post("/review/test/publish", json={"expected_revision": revision, "reason": "synthetic review", "evidence_checked": True, "applicability_checked": True}, headers=auth("reviewer"))


def test_draft_is_hidden_then_published(client):
    assert draft(client).status_code == 201
    assert client.get("/cards/test", headers=auth("reader")).status_code == 404
    assert publish(client).status_code == 200
    result = client.get("/cards/test", headers=auth("reader")).json()
    assert result["revision"] == 1 and result["review_status"] == "verified"
    assert result["card"]["claims"][0]["threshold"]["operator"] == ">="
    assert result["reviewed_at"]


def test_edit_preserves_published_revision_and_stale_approval_rejected(client):
    draft(client); publish(client)
    updated = card(); updated["name_vi"] = "Bản sửa"
    assert draft(client, 1, updated).status_code == 201
    assert publish(client, 1).status_code == 409
    assert client.get("/cards/test", headers=auth("reader")).json()["card"]["name_vi"] == "Đo thử nghiệm"
    assert publish(client, 2).status_code == 200
    assert client.get("/cards/test", headers=auth("reader")).json()["revision"] == 2


def test_stale_edit_and_duplicate_approval(client):
    draft(client)
    assert draft(client).status_code == 409
    assert publish(client).status_code == 200
    assert publish(client).status_code == 409


def test_withdrawal_hides_card_and_cannot_republish_same_revision(client):
    draft(client); publish(client)
    r = client.post("/review/test/withdraw", json={"expected_revision": 1, "reason": "test correction"}, headers=auth("reviewer"))
    assert r.status_code == 200
    assert client.get("/cards/test", headers=auth("reader")).status_code == 404
    assert publish(client).status_code == 409
    assert client.get("/review/pending", headers=auth("reviewer")).json()["results"] == []
    assert draft(client, 1).status_code == 201
    assert publish(client, 2).status_code == 200


@pytest.mark.parametrize("endpoint", ["/review/pending", "/review/test/history"])
def test_reader_cannot_review(client, endpoint):
    assert client.get(endpoint, headers=auth("reader")).status_code == 403


def test_authentication_and_reader_cannot_write(client):
    assert client.get("/topics/search?q=test").status_code == 401
    assert client.post("/cards/test/revisions", json={"expected_revision": 0, "card": card()}, headers=auth("reader")).status_code == 403
    assert client.post("/sources", json=source(), headers=auth("reviewer")).status_code == 403


@pytest.mark.parametrize("mutation", ["hash", "page", "unknown_source", "unknown_evidence", "no_evidence", "no_protocol", "bad_logic", "bad_bbox", "nan", "unknown_field"])
def test_invalid_cards(client, mutation):
    c = card()
    if mutation == "hash": c["evidence"][0]["document_sha256"] = "b" * 64
    if mutation == "page": c["evidence"][0]["pdf_page"] = 99
    if mutation == "unknown_source": c["evidence"][0]["document_version_id"] = "missing"
    if mutation == "unknown_evidence": c["claims"][0]["evidence_ids"] = ["missing"]
    if mutation == "no_evidence": c["claims"][0]["evidence_ids"] = []
    if mutation == "no_protocol": c.pop("measurement")
    if mutation == "bad_logic": c["logic"]["kind"] = "at_least"; c["logic"]["minimum"] = 2
    if mutation == "bad_bbox": c["evidence"][0]["bbox"] = [0, 0, 2, 1]
    if mutation == "nan": c["claims"][0]["threshold"]["value"] = "NaN"
    if mutation == "unknown_field": c["verified"] = True
    assert draft(client, payload=c).status_code == 422
    assert client.get("/review/pending", headers=auth("reviewer")).json()["results"] == []


def test_both_attestations_required(client):
    draft(client)
    r = client.post("/review/test/publish", json={"expected_revision": 1, "reason": "test", "evidence_checked": True, "applicability_checked": False}, headers=auth("reviewer"))
    assert r.status_code == 422
    assert client.get("/cards/test", headers=auth("reader")).status_code == 404


def test_search_diacritics_and_audit(client):
    draft(client); publish(client)
    result = client.get("/topics/search", params={"q": "do thu nghiem"}, headers=auth("reader")).json()
    assert result["results"][0]["card_id"] == "test"
    events = client.get("/review/test/history", headers=auth("reviewer")).json()
    assert [e["action"] for e in events] == ["draft_created", "published"]


def test_revision_orm_is_immutable(client):
    draft(client)
    with pytest.raises(ValueError, match="immutable"):
        with client.app.state.service.sessions.begin() as db:
            revision = db.scalar(select(Revision))
            revision.payload = {"changed": True}


def test_tokens_must_be_distinct_and_long(tmp_path):
    with pytest.raises(ValueError):
        create_app(f"sqlite:///{tmp_path / 'bad.db'}", {role: "same" for role in TOKENS})


def test_schema_requires_context():
    payload = card(); payload.pop("applicability")
    with pytest.raises(ValidationError): Card.model_validate(payload)


def test_ambiguous_alias_returns_all_published_candidates(client):
    draft(client); publish(client)
    payload = card(); payload["modality"] = "SECOND_TEST"; payload["measurement"]["modality"] = "SECOND_TEST"
    assert client.post("/cards/second/revisions", json={"expected_revision": 0, "card": payload}, headers=auth("reviewer")).status_code == 201
    assert client.post("/review/second/publish", json={"expected_revision": 1, "reason": "test", "evidence_checked": True, "applicability_checked": True}, headers=auth("reviewer")).status_code == 200
    result = client.get("/topics/search?q=TEST", headers=auth("reader")).json()["results"]
    assert {x["card_id"] for x in result} == {"test", "second"}


def test_source_version_cannot_be_overwritten(client):
    assert client.post("/sources", json=source(), headers=auth("admin")).status_code == 409


def test_failed_revision_write_leaves_no_audit(client):
    draft(client)
    assert draft(client).status_code == 409
    history = client.get("/review/test/history", headers=auth("reviewer")).json()
    assert len(history) == 1
