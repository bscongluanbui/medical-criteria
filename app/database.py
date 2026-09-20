"""PostgreSQL production models; SQLite is used only in isolated tests."""
from datetime import datetime, timezone
from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "document_versions"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class CardHead(Base):
    __tablename__ = "card_heads"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    latest: Mapped[int] = mapped_column(Integer, default=0)
    published: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Revision(Base):
    __tablename__ = "card_revisions"
    __table_args__ = (UniqueConstraint("card_id", "number"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("card_heads.id"))
    number: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("card_heads.id"))
    revision: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Account(Base):
    __tablename__ = "dashboard_accounts"
    __table_args__ = (CheckConstraint("role IN ('admin', 'reviewer')"),)
    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WebSession(Base):
    __tablename__ = "dashboard_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(ForeignKey("dashboard_accounts.email"))
    csrf: Mapped[str] = mapped_column(String(64))
    expires: Mapped[int] = mapped_column(Integer, index=True)


class LoginAttempt(Base):
    __tablename__ = "dashboard_login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity: Mapped[str] = mapped_column(String(64), index=True)
    timestamp: Mapped[int] = mapped_column(Integer, index=True)


class PublicSlot(Base):
    __tablename__ = "dashboard_public_slots"
    __table_args__ = (CheckConstraint("slot >= 1 AND slot <= 6"),)
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[str | None] = mapped_column(ForeignKey("card_heads.id"), nullable=True)
    revision: Mapped[int | None] = mapped_column(Integer, nullable=True)


def immutable(mapper, connection, target):
    raise ValueError("immutable record: create a new revision/event instead")


for model in (Document, Revision, Audit):
    event.listen(model, "before_update", immutable)
    event.listen(model, "before_delete", immutable)


def connect(url: str):
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, connection_record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
    return engine, sessionmaker(engine, expire_on_commit=False)
