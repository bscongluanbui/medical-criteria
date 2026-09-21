"""Revision workflow. Every write is committed with its audit event."""
import hashlib
import json
import unicodedata
from sqlalchemy import select, update
from app.database import Audit, CardHead, Document, Revision
from app.schemas import Card, SourceVersion
from app.verification import VerificationWorkflow


class Conflict(ValueError):
    pass


class NotFound(LookupError):
    pass


def normalize(text):
    text = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class KnowledgeService(VerificationWorkflow):
    def __init__(self, sessions):
        self.sessions = sessions

    def register_source(self, source: SourceVersion):
        with self.sessions.begin() as db:
            if db.get(Document, source.id):
                raise Conflict("source version already exists; register a new version")
            db.add(Document(id=source.id, payload=source.model_dump(mode="json")))
        return {"id": source.id}

    @staticmethod
    def validate_evidence(db, card):
        for evidence in card.evidence:
            source = db.get(Document, evidence.document_version_id)
            if source is None:
                raise ValueError("unregistered source version")
            if source.payload["sha256"] != evidence.document_sha256:
                raise ValueError("evidence hash differs from retained source")
            if evidence.pdf_page > source.payload["pdf_pages"]:
                raise ValueError("evidence page exceeds source page count")

    def add_revision(self, card_id, expected, card: Card, actor):
        payload = card.model_dump(mode="json")
        with self.sessions.begin() as db:
            self.validate_evidence(db, card)
            head = db.get(CardHead, card_id)
            if head is None:
                if expected != 0:
                    raise Conflict("new card requires expected_revision=0")
                db.add(CardHead(id=card_id, latest=0))
                db.flush()
            changed = db.execute(update(CardHead).where(CardHead.id == card_id, CardHead.latest == expected).values(latest=expected + 1))
            if changed.rowcount != 1:
                raise Conflict("stale revision; reload before editing")
            revision = expected + 1
            db.add(Revision(card_id=card_id, number=revision, payload=payload, content_sha256=digest(payload), created_by=actor))
            db.add(Audit(card_id=card_id, revision=revision, action="draft_created", actor=actor, reason="new immutable revision"))
        return {"card_id": card_id, "revision": revision, "review_status": "pending", "content_sha256": digest(payload)}

    def publish(self, card_id, expected, actor, reason, evidence_checked, applicability_checked):
        if not evidence_checked or not applicability_checked:
            raise ValueError("both evidence and applicability must be reviewed")
        with self.sessions.begin() as db:
            head = db.scalar(select(CardHead).where(CardHead.id == card_id).with_for_update())
            if head is None:
                raise NotFound(card_id)
            if head.latest != expected or (head.published == expected and self.verification(db, card_id, expected)["doctor"] == "DOCTOR_VERIFIED"):
                raise Conflict("stale or already published revision")
            # Withdrawal is terminal for that revision: corrected content needs a new revision.
            withdrawn = db.scalar(select(Audit.id).where(Audit.card_id == card_id, Audit.revision == expected, Audit.action.in_(["withdrawn", "audit_blocked"])))
            if withdrawn:
                raise Conflict("withdrawn revision requires a new revision")
            revision = db.scalar(select(Revision).where(Revision.card_id == card_id, Revision.number == expected))
            self.validate_evidence(db, Card.model_validate(revision.payload))
            head.published = expected
            db.add(Audit(card_id=card_id, revision=expected, action="published", actor=actor, reason=reason))
        return {"card_id": card_id, "revision": expected, "review_status": "verified"}

    def withdraw(self, card_id, expected, actor, reason):
        with self.sessions.begin() as db:
            head = db.scalar(select(CardHead).where(CardHead.id == card_id).with_for_update())
            if head is None:
                raise NotFound(card_id)
            if head.published != expected:
                raise Conflict("published revision has changed")
            head.published = None
            db.add(Audit(card_id=card_id, revision=expected, action="withdrawn", actor=actor, reason=reason))
        return {"card_id": card_id, "revision": expected, "lifecycle_status": "withdrawn"}

    def get_published(self, card_id):
        with self.sessions() as db:
            revision = db.scalar(select(Revision).join(CardHead, CardHead.id == Revision.card_id).where(CardHead.id == card_id, Revision.number == CardHead.published))
            if revision is None:
                raise NotFound(card_id)
            event = db.scalar(select(Audit).where(Audit.card_id == card_id, Audit.revision == revision.number, Audit.action == "published").order_by(Audit.id.desc()))
            verification = self.verification(db, card_id, revision.number)
            return {"card_id": card_id, "revision": revision.number, "content_sha256": revision.content_sha256, "review_status": "verified" if verification["doctor"] == "DOCTOR_VERIFIED" else "ai_preliminary", "reviewed_at": event.created_at.isoformat() if event else None, "verification": verification, "card": revision.payload}

    def search(self, query):
        # Exact normalized names and aliases only in P0; ambiguous results stay a list.
        query = normalize(query)
        with self.sessions() as db:
            revisions = db.scalars(select(Revision).join(CardHead, CardHead.id == Revision.card_id).where(Revision.number == CardHead.published)).all()
            results = []
            for row in revisions:
                card = row.payload
                terms = [row.card_id, card["topic_id"], card["name_vi"], card["name_en"], *card["aliases"]]
                if query in map(normalize, terms):
                    results.append({"card_id": row.card_id, "revision": row.number, "name_vi": card["name_vi"], "modality": card["modality"], "guideline_version": card["guideline_version"], "verification": self.verification(db, row.card_id, row.number)})
            return results

    def pending(self):
        with self.sessions() as db:
            withdrawn = select(Audit.id).where(Audit.card_id == Revision.card_id, Audit.revision == Revision.number, Audit.action == "withdrawn").exists()
            return [{"card_id": r.card_id, "revision": r.number, "card": r.payload} for r in db.scalars(select(Revision).join(CardHead, CardHead.id == Revision.card_id).where(Revision.number == CardHead.latest, (CardHead.published.is_(None)) | (CardHead.latest != CardHead.published), ~withdrawn)).all()]

    def history(self, card_id):
        with self.sessions() as db:
            return [{"revision": e.revision, "action": e.action, "actor": e.actor, "reason": e.reason, "at": e.created_at.isoformat()} for e in db.scalars(select(Audit).where(Audit.card_id == card_id).order_by(Audit.id)).all()]
