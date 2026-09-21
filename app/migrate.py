"""Initial schema installer. Future schema changes require numbered migrations."""
import os
from sqlalchemy import text
from app.database import Base, connect


def migrate(engine):
    if engine.dialect.name != "postgresql":
        raise ValueError("deployment migration requires PostgreSQL")
    with engine.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(2026092001)"))
        Base.metadata.create_all(db)
        db.execute(text("""
            CREATE OR REPLACE FUNCTION reject_immutable_change() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              RAISE EXCEPTION 'immutable record: append a new revision/event';
            END $$
        """))
        for table in ("document_versions", "card_revisions", "audit_events", "gpt_audit_packages", "gpt_audit_results"):
            db.execute(text(f"DROP TRIGGER IF EXISTS immutable_row ON {table}"))
            db.execute(text(f"CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()"))


if __name__ == "__main__":
    engine, _ = connect(os.environ["DATABASE_URL"])
    try:
        migrate(engine)
        print("MIGRATION_001_OK")
    finally:
        engine.dispose()
