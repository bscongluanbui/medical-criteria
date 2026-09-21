"""Bounded source-first research pipeline. A failed evidence check never publishes."""
import hashlib
import json
import re
from pydantic import ValidationError
from sqlalchemy import select
from app.database import Topic, Audit, CardHead, Document, ResearchJob, Revision
from app.literature import Literature, SourceError, parse_pdf, retain_pdf
from app.schemas import Card, SourceVersion
from app.service import KnowledgeService


class NeedsReview(ValueError):
    pass


def normalized_quote(value):
    return ' '.join(value.split())


class ResearchPipeline:
    def __init__(self, sessions, ai, source_root, literature=None):
        self.sessions, self.ai, self.source_root = sessions, ai, source_root
        self.literature = literature or Literature()
        self.service = KnowledgeService(sessions)

    def progress(self, job, stage, **details):
        with self.sessions.begin() as db:
            record = db.get(ResearchJob, job.id)
            if record:
                record.provenance = {**(record.provenance or {}), 'stage': stage, **details}

    def run(self, job):
        card_id = 'ai-'+job.id[:32]
        # Resume after a crash without generating/overwriting another revision.
        with self.sessions() as db:
            head = db.get(CardHead, card_id)
            if head:
                return self.resume(card_id)
        self.ai.calls = []
        with self.sessions.begin() as db:
            record = db.get(ResearchJob, job.id)
            if record:
                record.provenance = {**(record.provenance or {}), 'configured_model': self.ai.model}
        self.progress(job, 'search_plan')
        plan = self.ai.ask('Classify a general radiology knowledge topic, NOT a patient case. Return {"eligible":boolean,"search_terms":string}. Use concise English scientific keywords for diagnostic criteria, imaging measurement, classification or guidelines. If it contains identifiable patient information, is a patient case, or is unrelated, eligible=false.', {'topic': job.query})
        if plan.get('eligible') is not True:
            raise NeedsReview('GENERAL_KNOWLEDGE_TOPIC_REQUIRED')
        terms = plan.get('search_terms')
        if not isinstance(terms, str) or not 1 <= len(terms) <= 250:
            raise NeedsReview('INVALID_SEARCH_PLAN')
        self.progress(job, 'source_search', search_terms=terms)
        candidates = self.literature.search(terms)
        if not candidates:
            raise NeedsReview('NO_OPEN_ACCESS_SOURCE')
        self.progress(job, 'source_selection', candidate_ids=[c['pmcid'] for c in candidates])
        selection = self.ai.ask('Choose up to 3 candidate PMC IDs that directly document the requested criteria. Prefer primary guideline/consensus/validation publications over reviews. Return {"pmcids":[string]}. Empty list if no suitable evidence. Do not invent IDs.', {'topic': job.query, 'candidates': candidates})
        ids = selection.get('pmcids', [])
        if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or any(not isinstance(i, str) for i in ids):
            raise NeedsReview('NO_SUITABLE_SOURCE')
        by_id = {c['pmcid']: c for c in candidates}
        if not set(ids) <= by_id.keys():
            raise NeedsReview('SOURCE_ID_NOT_IN_SEARCH')
        sources, documents, source_failures = [], [], []
        self.progress(job, 'source_download')
        for pmcid in dict.fromkeys(ids):
            candidate = by_id[pmcid]
            try:
                data, license_note = self.literature.retrieve(candidate)
                pages = parse_pdf(data)
                sha = retain_pdf(self.source_root, data)
            except (SourceError, OSError) as exc:
                from app.research_errors import error_details
                source_failures.append({'pmcid':pmcid, **error_details(exc)})
                self.progress(job, 'source_download', source_failures=source_failures)
                continue
            source = SourceVersion(id='pdf-'+sha[:40], source_id=pmcid,
                title=candidate['title'], organization=candidate['authors'][:2000] or 'PMC indexed publication',
                version=candidate['year'] or 'undated', doi=candidate.get('doi'),
                official_url='https://pmc.ncbi.nlm.nih.gov/articles/'+pmcid+'/', sha256=sha,
                pdf_pages=len(pages), license_note=license_note,
                archive_reference='source_pdf/'+sha+'.pdf', retention='retained_source')
            sources.append(source)
            documents.append({'source': source.model_dump(), 'pages': [{'pdf_page': i+1, 'text': text} for i, text in enumerate(pages)]})
        if not sources:
            raise NeedsReview('NO_READABLE_RETAINED_PDF')
        self.progress(job, 'card_extraction')
        raw = self.ai.ask('Extract a Vietnamese knowledge card matching the supplied JSON schema. Return {"card":object} or {"card":null} if evidence is insufficient. Use only supplied pages, exact verbatim quotes and actual page numbers. origin=ai_extracted. Identify the specific source version, population, measurement protocol, thresholds, units, exceptions and limitations. Do not claim latest/current unless documented. Do not fill gaps from memory. A scanned/table/figure-dependent criterion with unreadable layout must return card=null. Include at most 15 claims, all supported by retained source evidence.',
                           {'topic': job.query, 'requested_route': (job.provenance or {}).get('route'), 'card_schema': Card.model_json_schema(), 'documents': documents})
        if not raw.get('card'):
            raise NeedsReview('INSUFFICIENT_EXTRACTABLE_EVIDENCE')
        try:
            card = Card.model_validate(raw['card'])
        except ValidationError as exc:
            from app.research_errors import error_details
            details = error_details(exc)
            self.progress(job, 'card_schema_repair', schema_errors=details['schema_errors'])
            # Exactly one repair using the same retained sources; never silently relax schema.
            repaired = self.ai.ask('Repair the JSON card to match the schema. Return {"card":object} or {"card":null} if source evidence is insufficient. Correct structure only using supplied source pages. Do not invent evidence, change thresholds from memory, or remove applicability/limitations to make validation pass. Preserve the requested topic/type/modality. Null is preferable to unsupported content.',
                {'draft':raw['card'], 'validation_errors':details['schema_errors'],
                 'card_schema':Card.model_json_schema(), 'requested_route':(job.provenance or {}).get('route'), 'documents':documents})
            if not repaired.get('card'):
                raise NeedsReview('INSUFFICIENT_EXTRACTABLE_EVIDENCE')
            try:
                card = Card.model_validate(repaired['card'])
            except ValidationError as final_error:
                self.progress(job, 'card_schema_failed', schema_errors=error_details(final_error)['schema_errors'])
                raise NeedsReview('CARD_SCHEMA_INVALID_AFTER_REPAIR') from None
        if card.origin != 'ai_extracted':
            raise NeedsReview('INVALID_ORIGIN')
        route = (job.provenance or {}).get('route')
        if route:
            if card.type != route['intent'] or (route.get('modality') and card.modality != route['modality']):
                raise NeedsReview('EXTRACTED_CARD_ROUTE_MISMATCH')
            card.topic_id = route['topic_id']
            with self.sessions() as db:
                topic = db.get(Topic, card.topic_id)
                if topic:
                    card.name_vi, card.name_en = topic.canonical_name_vi, topic.canonical_name_en
        else:
            card.topic_id = card_id
        # Keep exact request as an alias for deterministic cache hits.
        if not route and job.query not in card.aliases:
            card.aliases = card.aliases[:29]+[job.query]
        available = {s.id: (s, d['pages']) for s, d in zip(sources, documents)}
        for evidence in card.evidence:
            if evidence.document_version_id not in available:
                raise NeedsReview('UNRETAINED_EVIDENCE')
            source, pages = available[evidence.document_version_id]
            if evidence.document_sha256 != source.sha256 or evidence.pdf_page > len(pages):
                raise NeedsReview('EVIDENCE_HASH_OR_PAGE_MISMATCH')
            quote = normalized_quote(evidence.quote)
            if len(quote) < 20 or quote not in normalized_quote(pages[evidence.pdf_page-1]['text']):
                raise NeedsReview('QUOTE_NOT_ON_CITED_PAGE')
            evidence.parser_version = 'pypdf-6.19.0-layout'
        self.progress(job, 'evidence_self_check')
        check = self.ai.ask('Independently re-check this draft against supplied source pages. Return {"context_supported":boolean,"claims":[{"id":string,"supported":boolean}],"notes":string}. Check every claim, numeric operator/value/unit, measurement, population, logic, source version, exceptions and suitability for the requested topic. Supported must be false for ambiguous tables, absent footnotes, or unsupported details. Never equate source availability with truth.',
                            {'topic': job.query, 'card': card.model_dump(mode='json'), 'documents': documents})
        claims = check.get('claims')
        if (check.get('context_supported') is not True or not isinstance(claims, list)
            or len(claims) != len(card.claims)
            or {c.get('id') for c in claims if isinstance(c, dict)} != {c.id for c in card.claims}
            or any(not isinstance(c, dict) or c.get('supported') is not True for c in claims)):
            raise NeedsReview('AI_EVIDENCE_CHECK_FAILED')
        # Persist sources + revision + model provenance atomically before publication.
        self.progress(job, 'persist_card')
        payload = card.model_dump(mode='json')
        from app.service import digest
        with self.sessions.begin() as db:
            for source in sources:
                existing = db.get(Document, source.id)
                if existing is None:
                    db.add(Document(id=source.id, payload=source.model_dump(mode='json')))
            db.flush()
            self.service.validate_evidence(db, card)
            db.add(CardHead(id=card_id, latest=1))
            db.flush()
            db.add(Revision(card_id=card_id, number=1, payload=payload, content_sha256=digest(payload), created_by='ai-worker'))
            db.add(Audit(card_id=card_id, revision=1, action='draft_created', actor='ai-worker', reason='source-first automated extraction'))
            db.add(Audit(card_id=card_id, revision=1, action='ai_self_checked', actor='ai-worker', reason=json.dumps({'model': self.ai.model, 'prompt_version': 'research-v1', 'notes': str(check.get('notes', ''))[:2000]})))
            record = db.get(ResearchJob, job.id)
            if record:
                record.card_id = card_id
                record.provenance = {**(record.provenance or {}), 'configured_model': self.ai.model, 'search_provider': 'EuropePMC/PMC-OA', 'search_terms': terms,
                                     'sources': [s.id for s in sources], 'calls': self.ai.calls}
        return self.resume(card_id)

    def resume(self, card_id):
        with self.sessions() as db:
            head = db.get(CardHead, card_id)
            if head is None:
                raise NeedsReview('MISSING_GENERATED_CARD')
            blocked = db.scalar(select(Audit.id).where(Audit.card_id == card_id, Audit.revision == head.latest, Audit.action.in_(['withdrawn', 'audit_blocked'])))
            if blocked:
                raise NeedsReview('GENERATED_REVISION_BLOCKED')
            reviewed = db.scalar(select(Audit).where(Audit.card_id == card_id, Audit.revision == head.latest, Audit.action == 'ai_self_checked'))
            if not reviewed:
                raise NeedsReview('REVISION_REQUIRES_REVIEW')
            original_model = json.loads(reviewed.reason)['model']
            number, published = head.latest, head.published
        if published is None:
            self.service.publish_preliminary(card_id, number, 'ai-worker', 'Source-first extraction; model self-check only, not independent audit', original_model)
        self.service.prepare_audit(card_id, number, 'ai-worker', reuse=True)
        return card_id
