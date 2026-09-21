"""Same-origin public catalogue and authenticated editorial dashboard on port 3500."""
import os
import secrets
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from app.auth import Auth
from app.database import Audit, CardHead, Document, PublicSlot, Revision, connect
from app.schemas import Identifier, RevisionRequest, ReviewRequest, SourceVersion, StrictModel, WithdrawRequest
from app.service import Conflict, KnowledgeService, NotFound
from app.schemas import PreliminaryRequest, AuditImport
from app.database import ResearchJob


class Credentials(StrictModel):
    # Password spaces are significant; do not use inherited string stripping here.
    model_config = {"extra": "forbid", "str_strip_whitespace": False}
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class Feature(StrictModel):
    slot: int = Field(ge=1, le=6)
    card_id: Identifier | None = None
    revision: int | None = Field(default=None, ge=1)


def create_dashboard(database_url=None, origin=None, secure_cookies=True):
    url = database_url or os.environ["DATABASE_URL"]
    if database_url is None and not url.startswith("postgresql+psycopg://"):
        raise ValueError("production requires PostgreSQL")
    origin = (origin or os.getenv("DASHBOARD_ORIGIN", "https://criteria.bcanatomy.site")).rstrip("/")
    if not urlparse(origin).netloc or (secure_cookies and not origin.startswith("https://")):
        raise ValueError("DASHBOARD_ORIGIN must be an HTTPS origin")
    engine, sessions = connect(url)
    service, auth = KnowledgeService(sessions), Auth(sessions)
    cookie = "__Host-criteria_session" if secure_cookies else "criteria_dev_session"
    static = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.engine, app.state.sessions = engine, sessions
    app.state.service, app.state.auth = service, auth

    @app.middleware("http")
    async def headers(request, call_next):
        # No state changes from foreign origins. No CORS grants are emitted.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("origin") != origin:
                return JSONResponse({"detail": "Origin không hợp lệ."}, status_code=403)
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Content-Length không hợp lệ."}, status_code=400)
            if size > 512_000:
                return JSONResponse({"detail": "Nội dung quá lớn."}, status_code=413)
            total = 0
            chunks = []
            async for chunk in request.stream():
                total += len(chunk)
                if total > 512_000:
                    return JSONResponse({"detail": "Nội dung quá lớn."}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def user(request: Request):
        return auth.resolve(request.cookies.get(cookie))

    def write(request: Request, account=Depends(user)):
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), account["csrf"]):
            raise HTTPException(403, "Phiên xác nhận không hợp lệ. Vui lòng tải lại trang.")
        return account

    def admin(account=Depends(write)):
        if account["role"] != "admin":
            raise HTTPException(403, "Chỉ quản trị viên được thực hiện thao tác này.")
        return account

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(NotFound)
    async def missing(request, exc):
        return JSONResponse({"detail": "Không tìm thấy tiêu chuẩn."}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def duplicate(request, exc):
        return JSONResponse({"detail": "Dữ liệu đã thay đổi hoặc bị trùng. Vui lòng tải lại."}, status_code=409)

    @app.get("/health/live")
    def health():
        with engine.connect() as db:
            db.execute(text("SELECT 1 FROM dashboard_accounts LIMIT 1"))
        return {"status": "ok"}

    @app.get("/")
    def home():
        return FileResponse(static / "index.html")

    @app.get("/login")
    def login_page():
        return FileResponse(static / "login.html")

    @app.get("/dashboard")
    def dashboard_page(request: Request):
        try:
            user(request)
        except HTTPException:
            return RedirectResponse("/login", status_code=303)
        return FileResponse(static / "dashboard.html")

    @app.post("/web/login")
    def login(body: Credentials, request: Request, response: Response):
        # JSON + same-origin middleware blocks login CSRF; rotate any existing session.
        token = auth.login(body.email.strip().lower(), body.password)
        old = request.cookies.get(cookie)
        if old:
            auth.logout(old)
        response.set_cookie(cookie, token, max_age=8 * 3600, httponly=True, secure=secure_cookies, samesite="lax", path="/")
        return {"ok": True}

    @app.get("/web/session")
    def session(account=Depends(user)):
        return account

    @app.post("/web/logout")
    def logout(request: Request, response: Response, account=Depends(write)):
        auth.logout(request.cookies[cookie])
        response.delete_cookie(cookie, path="/", secure=secure_cookies, httponly=True, samesite="lax")
        return {"ok": True}

    @app.get("/public/cards")
    def public_cards():
        with sessions() as db:
            rows = db.execute(select(PublicSlot, Revision).join(CardHead, CardHead.id == PublicSlot.card_id).join(Revision, (Revision.card_id == CardHead.id) & (Revision.number == PublicSlot.revision)).where(CardHead.published == PublicSlot.revision).order_by(PublicSlot.slot).limit(6)).all()
            results = []
            for slot, revision in rows:
                c = revision.payload
                citations = []
                for e in c["evidence"]:
                    source = db.get(Document, e["document_version_id"])
                    if source:
                        citations.append({"title": source.payload["title"], "url": source.payload["official_url"], "page": e["pdf_page"]})
                # Explicit whitelist: no raw quotes, local archive paths, drafts or audit identities.
                results.append({"id": revision.card_id, "revision": revision.number, "name_vi": c["name_vi"], "name_en": c["name_en"], "modality": c["modality"], "version": c["guideline_version"], "claims": [{"text": claim["text_vi"], "threshold": claim.get("threshold")} for claim in c["claims"]], "applicability": c["applicability"], "measurement": c.get("measurement"), "logic": c["logic"], "sources": citations, "verification": service.verification(db, revision.card_id, revision.number)})
            return {"results": results}

    @app.get("/web/cards")
    def cards(account=Depends(user)):
        with sessions() as db:
            results = []
            rows = db.execute(select(CardHead, Revision).join(Revision, (Revision.card_id == CardHead.id) & (Revision.number == CardHead.latest)).order_by(CardHead.id).limit(500)).all()
            for head, revision in rows:
                withdrawn = db.scalar(select(Audit.id).where(Audit.card_id == head.id, Audit.revision == head.latest, Audit.action == "withdrawn"))
                results.append({"id": head.id, "latest": head.latest, "published": head.published, "status": "withdrawn" if withdrawn else ("published" if head.published == head.latest else "pending"), "card": revision.payload, "verification": service.verification(db, head.id, head.latest)})
            return {"results": results}

    @app.get("/web/sources")
    def sources(account=Depends(user)):
        with sessions() as db:
            return {"results": [d.payload for d in db.scalars(select(Document).order_by(Document.id).limit(500)).all()]}

    @app.get("/web/research-jobs")
    def research_jobs(account=Depends(user)):
        with sessions() as db:
            return {"results": [{"id": j.id, "query": j.query, "status": j.status,
                                 "attempts": j.attempts, "card_id": j.card_id,
                                 "error_code": j.error_code, "created_at": j.created_at,
                                 "provenance": j.provenance}
                                for j in db.scalars(select(ResearchJob).order_by(ResearchJob.created_at.desc()).limit(100))]}

    @app.post("/web/sources", status_code=201)
    def register(body: SourceVersion, account=Depends(admin)):
        return service.register_source(body)

    @app.post("/web/cards/{card_id}/revisions", status_code=201)
    def edit(card_id: Identifier, body: RevisionRequest, account=Depends(write)):
        return service.add_revision(card_id, body.expected_revision, body.card, account["email"])

    @app.post("/web/cards/{card_id}/publish")
    def publish(card_id: Identifier, body: ReviewRequest, account=Depends(write)):
        return service.publish(card_id, body.expected_revision, account["email"], body.reason, body.evidence_checked, body.applicability_checked)

    @app.post("/web/cards/{card_id}/publish-preliminary")
    def preliminary(card_id: Identifier, body: PreliminaryRequest, account=Depends(write)):
        return service.publish_preliminary(card_id, body.expected_revision, account["email"], body.reason, body.model)

    @app.post("/web/cards/{card_id}/audit-package")
    def audit_package(card_id: Identifier, body: WithdrawRequest, account=Depends(write)):
        return service.prepare_audit(card_id, body.expected_revision, account["email"])

    @app.post("/web/audit-results")
    def audit_import(body: AuditImport, account=Depends(write)):
        return service.import_audit(body, account["email"])

    @app.post("/web/cards/{card_id}/withdraw")
    def withdraw(card_id: Identifier, body: WithdrawRequest, account=Depends(write)):
        return service.withdraw(card_id, body.expected_revision, account["email"], body.reason)

    @app.get("/web/cards/{card_id}/history")
    def history(card_id: Identifier, account=Depends(user)):
        return service.history(card_id)

    @app.get("/web/featured")
    def featured(account=Depends(user)):
        with sessions() as db:
            return {"results": [{"slot": s.slot, "card_id": s.card_id, "revision": s.revision} for s in db.scalars(select(PublicSlot).order_by(PublicSlot.slot)).all()]}

    @app.post("/web/featured")
    def feature(body: Feature, account=Depends(admin)):
        with sessions.begin() as db:
            if body.card_id:
                head = db.scalar(select(CardHead).where(CardHead.id == body.card_id).with_for_update())
                if not head or head.published is None or head.published != body.revision:
                    raise Conflict("Chỉ chọn đúng revision đã xuất bản để công khai.")
            slot = db.get(PublicSlot, body.slot)
            if slot is None:
                slot = PublicSlot(slot=body.slot)
                db.add(slot)
            old_id, old_revision = slot.card_id, slot.revision
            slot.card_id, slot.revision = body.card_id, body.revision if body.card_id else None
            if old_id:
                db.add(Audit(card_id=old_id, revision=old_revision, action="public_removed", actor=account["email"], reason=f"public slot {body.slot}"))
            if body.card_id:
                db.add(Audit(card_id=body.card_id, revision=body.revision, action="public_selected", actor=account["email"], reason=f"public slot {body.slot}"))
        return {"ok": True}

    # Keep protected HTML out of the public static mount.
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")
    return app
