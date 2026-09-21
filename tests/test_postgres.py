"""Real PostgreSQL tests only against an explicitly named disposable *_test DB."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from app.database import Audit, Base, Revision, connect
from app.migrate import migrate
from app.schemas import Card, SourceVersion
from app.service import Conflict, KnowledgeService
from tests.fixtures import card, source


@pytest.fixture
def pg():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL absent; use Docker test profile for PostgreSQL")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg" or not (parsed.database or "").endswith("_test"):
        pytest.fail("refusing destructive setup outside explicit PostgreSQL *_test database")
    engine, sessions = connect(url)
    Base.metadata.drop_all(engine)
    migrate(engine)
    service = KnowledgeService(sessions)
    service.register_source(SourceVersion.model_validate(source()))
    try:
        yield engine, sessions, service
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_postgres_migration_is_repeatable_and_publication_persists(pg):
    engine, sessions, service = pg
    migrate(engine)
    service.add_revision("test", 0, Card.model_validate(card()), "reviewer")
    service.publish("test", 1, "reviewer", "test", True, True)
    # Separate service/session reads the persisted publication.
    assert KnowledgeService(sessions).get_published("test")["revision"] == 1


def test_postgres_public_bot_pipeline_and_notifications(pg, tmp_path, monkeypatch):
    from app.bot import BotQueue
    from app.database import ResearchJob, AuditPackage, BotUpdate
    from tests.test_automation import pipeline_fixture, update
    engine, sessions, service = pg
    queue = BotQueue(sessions, cooldown=0)
    queue.receive(update(500, chat=987654321))
    pipeline = pipeline_fixture(queue, tmp_path, monkeypatch)
    assert queue.process(pipeline)
    with sessions() as db:
        job = db.scalar(select(ResearchJob))
        assert job.status == 'completed', job.error_code
        assert db.scalar(select(AuditPackage)) is not None
    sent = []
    class Telegram:
        def send(self, chat, message):
            sent.append(message)
    queue.deliver(Telegram())
    assert len(sent) == 1 and 'GPT_UNVERIFIED' in sent[0]
    with sessions() as db:
        assert db.get(BotUpdate, 500).delivered


@pytest.mark.parametrize("table", ["document_versions", "card_revisions", "audit_events"])
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_postgres_raw_sql_cannot_mutate_immutable_rows(pg, table, verb):
    engine, sessions, service = pg
    service.add_revision("test", 0, Card.model_validate(card()), "reviewer")
    with pytest.raises(DBAPIError, match="immutable record"):
        with engine.begin() as db:
            statement = f"UPDATE {table} SET id = id" if verb == "UPDATE" else f"DELETE FROM {table}"
            db.execute(text(statement))


def test_postgres_concurrent_edit_has_one_winner(pg):
    engine, sessions, service = pg
    service.add_revision("test", 0, Card.model_validate(card()), "reviewer")
    barrier = Barrier(2)

    def edit():
        barrier.wait(timeout=10)
        try:
            service.add_revision("test", 1, Card.model_validate(card()), "reviewer")
            return "created"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: edit(), range(2))) == ["conflict", "created"]
    with sessions() as db:
        assert len(db.scalars(select(Revision)).all()) == 2
        assert len(db.scalars(select(Audit)).all()) == 2


def test_postgres_concurrent_publish_has_one_winner(pg):
    engine, sessions, service = pg
    service.add_revision("test", 0, Card.model_validate(card()), "reviewer")
    barrier = Barrier(2)

    def publish():
        barrier.wait(timeout=10)
        try:
            service.publish("test", 1, "reviewer", "test", True, True)
            return "published"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: publish(), range(2))) == ["conflict", "published"]
    assert [e["action"] for e in service.history("test")] == ["draft_created", "published"]

def test_postgres_canonical_routing_and_dedup(pg):
    from app.query_router import QueryRouter
    from app.bot import BotQueue
    from app.database import ResearchJob
    engine,sessions,_=pg
    router=QueryRouter(sessions);router.bootstrap();router.bootstrap()
    queue=BotQueue(sessions,router=router,cooldown=0)
    for i,q in enumerate(['tiêu chuẩn siêu âm hẹp van 2 lá','tieu chuan ultrasound mitral stenosis'],1):
        queue.receive({'update_id':i,'message':{'chat':{'id':i},'text':q}})
    with sessions() as db:
        jobs=list(db.scalars(select(ResearchJob)))
        assert len(jobs)==1
        assert jobs[0].provenance['route']['modality']=='ultrasound'
