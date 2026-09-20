import os
import secrets
from contextlib import asynccontextmanager
from typing import Annotated
from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.database import connect
from app.schemas import RevisionRequest, ReviewRequest, SourceVersion, WithdrawRequest
from app.service import Conflict, KnowledgeService, NotFound

CardID = Annotated[str, Path(pattern=r"^[a-zA-Z0-9_-]{1,100}$")]


def create_app(database_url=None, tokens=None):
    url = database_url or os.environ["DATABASE_URL"]
    if database_url is None and not url.startswith("postgresql+psycopg://"):
        raise ValueError("production requires PostgreSQL through psycopg")
    tokens = tokens or {role: os.environ[f"{role.upper()}_TOKEN"] for role in ("reader", "reviewer", "admin")}
    if set(tokens) != {"reader", "reviewer", "admin"} or any(len(t) < 32 for t in tokens.values()) or len(set(tokens.values())) != 3:
        raise ValueError("three distinct tokens of at least 32 characters required")
    engine, sessions = connect(url)
    service = KnowledgeService(sessions)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="Medical Criteria — P0", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine
    app.state.service = service
    bearer = HTTPBearer(auto_error=False)

    def require(*roles):
        def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
            if credentials is None:
                raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
            matched = next((role for role, token in tokens.items() if secrets.compare_digest(credentials.credentials, token)), None)
            if matched is None:
                raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})
            if matched not in roles:
                raise HTTPException(403, "role not allowed")
            return matched
        return authenticate

    read = require("reader", "reviewer", "admin")
    review = require("reviewer", "admin")
    admin = require("admin")

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(NotFound)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": "published card not found"})

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(IntegrityError)
    async def integrity(request, exc):
        return JSONResponse(status_code=409, content={"detail": "concurrent or duplicate write; reload and retry"})

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", dependencies=[Depends(read)])
    def ready():
        with engine.connect() as db:
            db.execute(text("SELECT 1 FROM card_heads LIMIT 1"))
        return {"status": "ready"}

    @app.get("/topics/search", dependencies=[Depends(read)])
    def search(q: str = Query(min_length=1, max_length=200)):
        return {"results": service.search(q)}

    @app.get("/cards/{card_id}", dependencies=[Depends(read)])
    def card(card_id: CardID):
        return service.get_published(card_id)

    @app.get("/review/pending", dependencies=[Depends(review)])
    def pending():
        return {"results": service.pending()}

    @app.get("/review/{card_id}/history", dependencies=[Depends(review)])
    def history(card_id: CardID):
        return service.history(card_id)

    @app.post("/sources", status_code=201, dependencies=[Depends(admin)])
    def source(body: SourceVersion):
        return service.register_source(body)

    @app.post("/cards/{card_id}/revisions", status_code=201)
    def revision(card_id: CardID, body: RevisionRequest, actor=Depends(review)):
        return service.add_revision(card_id, body.expected_revision, body.card, actor)

    @app.post("/review/{card_id}/publish")
    def publish(card_id: CardID, body: ReviewRequest, actor=Depends(review)):
        return service.publish(card_id, body.expected_revision, actor, body.reason, body.evidence_checked, body.applicability_checked)

    @app.post("/review/{card_id}/withdraw")
    def withdraw(card_id: CardID, body: WithdrawRequest, actor=Depends(review)):
        return service.withdraw(card_id, body.expected_revision, actor, body.reason)

    return app
