"""PostgreSQL production models; SQLite is used only in isolated tests."""
from datetime import datetime, timezone
from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "document_versions"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class LibraryFile(Base):
    """Incremental inventory only; source PDFs are parsed on selection."""
    __tablename__ = "library_files"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    relative_path: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    mtime_ns: Mapped[int] = mapped_column(BigInteger)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


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


class AuditPackage(Base):
    __tablename__ = "gpt_audit_packages"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("card_heads.id"))
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class AuditResult(Base):
    __tablename__ = "gpt_audit_results"
    package_id: Mapped[str] = mapped_column(ForeignKey("gpt_audit_packages.id"), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    imported_by: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ResearchJob(Base):
    __tablename__ = "research_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    query: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[int] = mapped_column(Integer, default=0)
    card_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(Integer)


class BotUpdate(Base):
    __tablename__ = "telegram_updates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    chat_id: Mapped[str] = mapped_column(String(40))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("research_jobs.id"), nullable=True)
    reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_send_at: Mapped[int] = mapped_column(Integer, default=0)


class BotState(Base):
    __tablename__ = "telegram_state"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    offset: Mapped[int] = mapped_column(Integer, default=0)


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    canonical_name_vi: Mapped[str] = mapped_column(String(500))
    canonical_name_en: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    specialty: Mapped[str] = mapped_column(String(100), default="radiology")


class TopicCardBinding(Base):
    __tablename__ = "topic_card_bindings"
    card_id: Mapped[str] = mapped_column(ForeignKey("card_heads.id"), primary_key=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id"))


class TopicAlias(Base):
    __tablename__ = "topic_aliases"
    __table_args__ = (UniqueConstraint("topic_id", "normalized_alias"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id"))
    alias: Mapped[str] = mapped_column(String(500))
    normalized_alias: Mapped[str] = mapped_column(String(500), index=True)
    language: Mapped[str] = mapped_column(String(10), default="und")
    alias_type: Mapped[str] = mapped_column(String(30), default="common_name")
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AliasCandidate(Base):
    __tablename__ = "alias_candidates"
    __table_args__ = (UniqueConstraint("normalized_alias", "suggested_topic_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias: Mapped[str] = mapped_column(String(500))
    normalized_alias: Mapped[str] = mapped_column(String(500))
    suggested_topic_id: Mapped[str] = mapped_column(ForeignKey("topics.id"))
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    router_model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    reviewed_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BotMenu(Base):
    __tablename__ = "telegram_menus"
    update_id: Mapped[int] = mapped_column(ForeignKey("telegram_updates.id"), primary_key=True)
    buttons: Mapped[list] = mapped_column(JSON)


class QueryEvent(Base):
    __tablename__ = "query_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    normalized_query: Mapped[str] = mapped_column(String(500))
    route: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def immutable(mapper, connection, target):
    raise ValueError("immutable record: create a new revision/event instead")


for model in (Document, Revision, Audit, AuditPackage, AuditResult):
    event.listen(model, "before_update", immutable)
    event.listen(model, "before_delete", immutable)


def connect(url: str):
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, connection_record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
    return engine, sessionmaker(engine, expire_on_commit=False)
