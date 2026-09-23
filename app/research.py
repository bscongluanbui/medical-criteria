"""Bounded source-first research pipeline. A failed evidence check never publishes."""
import hashlib
import json
import re
from pydantic import ValidationError
from app.research_errors import SCHEMA_RULES
from sqlalchemy import select
from app.database import Topic, Audit, CardHead, Document, ResearchJob, Revision
from app.literature import Literature, SourceError, parse_pdf, retain_pdf, retain_web
from app.library import Library
from app.schemas import Card, SourceVersion
from app.service import KnowledgeService


class NeedsReview(ValueError):
    pass


def normalized_quote(value):
    return ' '.join(value.split())


def locate_quote(evidence, pages):
    quote = normalized_quote(evidence.quote)
    matches = [i+1 for i, page in enumerate(pages)
               if len(quote) >= 20 and quote in normalized_quote(page['text'])]
    detail = {'evidence_id': evidence.id, 'document_version_id': evidence.document_version_id,
              'cited_pdf_page': evidence.pdf_page, 'matching_pdf_pages': matches,
              'quote_length': len(quote), 'quote_sha256': hashlib.sha256(quote.encode()).hexdigest()}
    if evidence.pdf_page in matches:
        return True, None
    # Never move table/cell/figure coordinates or footnotes based only on text matching.
    has_locator = any(v is not None for v in (evidence.bbox, evidence.table, evidence.table_cell, evidence.footnote))
    if len(matches) == 1 and not has_locator:
        detail['code'] = 'EXACT_QUOTE_PAGE_RELOCATED'
        evidence.pdf_page = matches[0]
        evidence.printed_page = None
        return True, detail
    detail['code'] = ('QUOTE_TOO_SHORT' if len(quote) < 20 else
                      'QUOTE_NOT_IN_PARSED_DOCUMENT' if not matches else
                      'QUOTE_PAGE_AMBIGUOUS' if len(matches) > 1 else 'STRUCTURED_LOCATOR_REQUIRES_REVIEW')
    return False, detail


def extraction_schema(sources, route=None):
    schema = Card.model_json_schema()
    if route:
        schema['properties']['type'] = {'type':'string', 'const':route['intent']}
        if route.get('modality'):
            schema['properties']['modality'] = {'type':'string', 'const':route['modality']}
    evidence = schema['$defs']['Evidence']['properties']
    evidence['document_version_id']['enum'] = [source.id for source in sources]
    evidence['document_sha256']['enum'] = list(dict.fromkeys(source.sha256 for source in sources))
    return schema


def bind_evidence_source(evidence, sources):
    # Only accept a registered internal ID or an exact external PMCID with its exact hash.
    # Never choose a source by similarity, order, or because only one source exists.
    internal = [s for s in sources if s.id == evidence.document_version_id]
    if internal:
        return internal[0]
    candidates = [s for s in sources if s.source_id == evidence.document_version_id
                  and s.sha256 == evidence.document_sha256]
    if len(candidates) == 1:
        evidence.document_version_id = candidates[0].id
        return candidates[0]
    return None


class ResearchPipeline:
    def __init__(self, sessions, ai, source_root, literature=None, library_root=None, source_web_root=None):
        self.sessions, self.ai, self.source_root = sessions, ai, source_root
        self.source_web_root = source_web_root
        self.library = Library(sessions, library_root) if library_root else None
        self.literature = literature or Literature()
        self.service = KnowledgeService(sessions)

    def online_sources(self, job, terms, source_failures):
        """Provider-verified PMC results, then retained PDF or licensed JATS text."""
        self.progress(job, 'source_search', search_terms=terms, source_failures=source_failures)
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
        sources, documents = [], []
        self.progress(job, 'source_download')
        for pmcid in dict.fromkeys(ids):
            candidate = by_id[pmcid]
            try:
                data, license_note = self.literature.retrieve(candidate)
                pages = parse_pdf(data)
                sha = retain_pdf(self.source_root, data)
                source_format = 'pdf'
            except (SourceError, OSError) as exc:
                from app.research_errors import error_details
                source_failures.append({'pmcid':pmcid, **error_details(exc)})
                if not self.source_web_root or not hasattr(self.literature, 'retrieve_web'):
                    continue
                try:
                    pages, license_note = self.literature.retrieve_web(candidate)
                    sha = retain_web(self.source_web_root, pages)
                    source_format = 'web_text'
                except (SourceError, OSError) as web_exc:
                    source_failures.append({'pmcid':pmcid, 'code':str(web_exc)[:100]})
                    continue
            source = SourceVersion(id=('web-' if source_format == 'web_text' else 'pdf-')+sha[:40], source_id=pmcid,
                title=candidate.get('title') or pmcid,
                organization=(candidate.get('authors') or '')[:2000] or 'PMC indexed publication',
                version=str(candidate.get('year') or 'undated'), doi=candidate.get('doi'), source_format=source_format,
                official_url='https://pmc.ncbi.nlm.nih.gov/articles/'+pmcid+'/', sha256=sha,
                pdf_pages=len(pages), license_note=license_note,
                archive_reference=('source_web/'+sha+'.txt' if source_format == 'web_text' else 'source_pdf/'+sha+'.pdf'), retention='retained_source')
            sources.append(source)
            documents.append({'source': source.model_dump(), 'pages': [{'pdf_page': i+1, 'text': text} for i, text in enumerate(pages)]})
        if not sources:
            self.progress(job, 'source_unavailable', source_failures=source_failures)
            raise NeedsReview('NO_READABLE_RETAINED_SOURCE')
        return sources, documents

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
                record.provenance = {**(record.provenance or {}), 'configured_model': self.ai.model,
                                     'schema_errors': [], 'source_failures': [], 'evidence_errors': [], 'retained_sources': [], 'evidence_corrections': [], 'route_errors': []}
        self.progress(job, 'search_plan')
        plan = self.ai.ask('Classify a general radiology knowledge topic, NOT a patient case. Return {"eligible":boolean,"search_terms":string}. Use concise English scientific keywords for diagnostic criteria, imaging measurement, classification or guidelines. If it contains identifiable patient information, is a patient case, or is unrelated, eligible=false.', {'topic': job.query})
        if plan.get('eligible') is not True:
            raise NeedsReview('GENERAL_KNOWLEDGE_TOPIC_REQUIRED')
        terms = plan.get('search_terms')
        if not isinstance(terms, str) or not 1 <= len(terms) <= 250:
            raise NeedsReview('INVALID_SEARCH_PLAN')
        sources, documents, source_failures = [], [], []
        route = (job.provenance or {}).get('route')
        if self.library:
            with self.sessions() as db:
                topic = db.get(Topic, route['topic_id']) if route else None
            lookup = ' '.join(filter(None, [topic.canonical_name_en if topic else '', terms]))
            self.progress(job, 'library_search', search_terms=lookup)
            library_candidates = self.library.search(lookup)
            if library_candidates:
                for candidate in library_candidates[:3]:
                    key = candidate['id']
                    try:
                        pages, sha = self.library.retrieve(candidate)
                        source = SourceVersion(id='parsed-'+sha[:40], source_id='mineru-'+key[:40],
                            title=candidate['title'], organization='MinerU-parsed library', version='parsed library file',
                            official_url=None,
                            sha256=sha, source_format='parsed_text', pdf_pages=len(pages),
                            library_relative_path=candidate['relative_path'],
                            license_note='User-provided MinerU parse; verify original PDF during audit',
                            archive_reference='parse_pdf/'+candidate['relative_path'], retention='retained_source')
                        sources.append(source)
                        documents.append({'source': source.model_dump(), 'pages': [{'pdf_page': i+1, 'text': text} for i, text in enumerate(pages)]})
                    except (SourceError, OSError) as exc:
                        source_failures.append({'library_id':key, 'code':str(exc)[:100]})
            if sources:
                screen = self.ai.ask('Determine whether these MinerU-parsed source segments directly contain usable evidence for the requested criteria. Return {"suitable":boolean}. Do not infer from titles or memory; unsupported or unreadable segments are unsuitable.',
                    {'topic': job.query, 'documents': documents})
                if screen.get('suitable') is not True:
                    sources, documents = [], []
        if not sources:
            sources, documents = self.online_sources(job, terms, source_failures)
        source_bindings = [{'document_version_id':s.id, 'source_id':s.source_id, 'document_sha256':s.sha256,
                            'source_format':s.source_format, 'segment_count':s.pdf_pages} for s in sources]
        schema = extraction_schema(sources, (job.provenance or {}).get('route'))
        self.progress(job, 'card_extraction', retained_sources=source_bindings)
        prompt = 'Extract a Vietnamese knowledge card matching the supplied JSON schema. Return {"card":object} or {"card":null} if evidence is insufficient. Use only supplied source segments and exact verbatim quotes. Quote a contiguous verbatim span from a single supplied segment; never translate, paraphrase, combine fragments or insert ellipses in evidence.quote. For PDF, pdf_page is the supplied 1-based physical PDF index; for web_text or parsed_text, the same field is the 1-based text segment number, not a PDF page. Copy document_version_id and document_sha256 exactly from the same entry in source_bindings; source_id/PMCID is NOT document_version_id. origin=ai_extracted. Match the requested type and modality exactly. If documents only support another type, return card=null. Identify source version, population, measurement protocol, thresholds, units, exceptions and limitations. Do not claim latest/current unless documented. Do not fill gaps from memory. A scanned/table/figure-dependent criterion with unreadable layout must return card=null. Include at most 15 claims, all supported by retained source evidence.'
        raw = self.ai.ask(prompt,
                           {'topic': job.query, 'requested_route': (job.provenance or {}).get('route'), 'card_schema': schema, 'source_bindings': source_bindings, 'cross_field_rules': SCHEMA_RULES, 'documents': documents})
        if not raw.get('card') and all(source.source_format == 'parsed_text' for source in sources):
            # A title/content screen can be optimistic; no usable card means try verified web sources.
            sources, documents = self.online_sources(job, terms, source_failures)
            source_bindings = [{'document_version_id':s.id, 'source_id':s.source_id,
                                'document_sha256':s.sha256, 'source_format':s.source_format,
                                'segment_count':s.pdf_pages} for s in sources]
            schema = extraction_schema(sources, (job.provenance or {}).get('route'))
            self.progress(job, 'card_extraction', retained_sources=source_bindings)
            raw = self.ai.ask(prompt, {'topic':job.query, 'requested_route':(job.provenance or {}).get('route'),
                                       'card_schema':schema, 'source_bindings':source_bindings,
                                       'cross_field_rules':SCHEMA_RULES, 'documents':documents})
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
                 'card_schema':schema, 'source_bindings':source_bindings, 'cross_field_rules': SCHEMA_RULES, 'requested_route':(job.provenance or {}).get('route'), 'documents':documents})
            if not repaired.get('card'):
                raise NeedsReview('INSUFFICIENT_EXTRACTABLE_EVIDENCE')
            try:
                card = Card.model_validate(repaired['card'])
            except ValidationError as final_error:
                self.progress(job, 'card_schema_failed', schema_errors=error_details(final_error)['schema_errors'])
                raise NeedsReview('CARD_SCHEMA_INVALID_AFTER_REPAIR') from None
        self.progress(job, 'card_schema_valid', schema_errors=[])
        if card.origin != 'ai_extracted':
            raise NeedsReview('INVALID_ORIGIN')
        route = (job.provenance or {}).get('route')
        if route:
            def mismatch(candidate):
                return candidate.type != route['intent'] or (route.get('modality') and candidate.modality != route['modality'])
            if mismatch(card):
                route_error = {'expected': {'type':route['intent'], 'modality':route.get('modality')},
                               'actual': {'type':card.type, 'modality':card.modality}}
                self.progress(job, 'card_route_repair', route_errors=[route_error])
                repaired = self.ai.ask('Resolve this requested-route mismatch using only supplied documents. Return {"card":object} satisfying the constrained schema or {"card":null} when the requested content is not supported. Do NOT merely relabel severity grading, management, or supportive features as formal diagnostic criteria. Preserve citations and applicability; never invent content. This is the only route repair attempt.',
                    {'draft':card.model_dump(mode='json'), 'route_error':route_error,
                     'requested_route':route, 'card_schema':schema, 'source_bindings':source_bindings,
                     'cross_field_rules':SCHEMA_RULES, 'documents':documents})
                if not repaired.get('card'):
                    raise NeedsReview('REQUESTED_ROUTE_NOT_SUPPORTED_BY_SOURCES')
                try:
                    card = Card.model_validate(repaired['card'])
                except ValidationError as exc:
                    from app.research_errors import error_details
                    self.progress(job, 'card_route_repair_schema_failed', schema_errors=error_details(exc)['schema_errors'])
                    raise NeedsReview('CARD_SCHEMA_INVALID_AFTER_ROUTE_REPAIR') from None
                if mismatch(card):
                    self.progress(job, 'card_route_failed', route_errors=[{**route_error,'actual':{'type':card.type,'modality':card.modality}}])
                    raise NeedsReview('EXTRACTED_CARD_ROUTE_MISMATCH')
                if card.origin != 'ai_extracted':
                    raise NeedsReview('INVALID_ORIGIN')
                self.progress(job, 'card_route_valid', route_errors=[])

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
        self.progress(job, 'evidence_binding')
        evidence_errors, evidence_corrections = [], []
        for evidence in card.evidence:
            if bind_evidence_source(evidence, sources) is None:
                self.progress(job, 'evidence_binding_failed', evidence_errors=[{'evidence_id': evidence.id, 'document_version_id': evidence.document_version_id, 'code': 'UNRETAINED_EVIDENCE'}])
                raise NeedsReview('UNRETAINED_EVIDENCE')
            source, pages = available[evidence.document_version_id]
            if evidence.document_sha256 != source.sha256 or evidence.pdf_page > len(pages):
                raise NeedsReview('EVIDENCE_HASH_OR_PAGE_MISMATCH')
            valid, detail = locate_quote(evidence, pages)
            if not valid:
                evidence_errors.append(detail)
            elif detail:
                evidence_corrections.append(detail)
            evidence.parser_version = ('mineru-parsed-segments-v1' if source.source_format == 'parsed_text' else
                                       'jats-text-segments-v1' if source.source_format == 'web_text' else 'pypdf-6.19.0-layout')
        self.progress(job, 'evidence_quote_validation', evidence_errors=evidence_errors, evidence_corrections=evidence_corrections)
        if evidence_errors:
            raise NeedsReview('QUOTE_NOT_ON_CITED_PAGE')
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
                record.provenance = {**(record.provenance or {}), 'configured_model': self.ai.model,
                                     'search_provider': 'library' if any(s.library_relative_path for s in sources) else 'EuropePMC/PMC-OA',
                                     'search_terms': terms, 'source_failures': source_failures,
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
