"""Synthetic fixtures only: source ordering and archive integrity, not clinical facts."""
import hashlib
import json
from pathlib import Path
import pytest
from sqlalchemy import select
from app.database import AuditPackage, Base, Document, ResearchJob, connect
from app.drive_sync import DriveSync
from app.library import Library, parse_mineru
from app.literature import SourceError, parse_full_text_xml
from app.research import ResearchPipeline
from tests.fixtures import card
from tests.test_automation import queue, update


def directories(root):
    for name in ('parse_pdf', 'source_pdf', 'source_web', 'audit_packages', 'audit_results'):
        (root / name).mkdir(parents=True)


def draft_for(sha, source_id):
    draft = card()
    draft['origin'] = 'ai_extracted'
    draft['evidence'][0].update(document_version_id=source_id, document_sha256=sha,
                                quote='SYNTHETIC length >= 5.0 mm')
    return draft


def test_inventory_reads_mineru_not_original_pdfs(queue, tmp_path):
    parsed = tmp_path / 'parse_pdf'
    parsed.mkdir()
    (parsed / 'unrelated.pdf').write_bytes(b'%PDF-1.7\nsynthetic diagnostic measurement')
    path = parsed / 'opaque.md'
    path.write_text('# synthetic diagnostic measurement\nSYNTHETIC length >= 5.0 mm', encoding='utf-8')
    library = Library(queue.sessions, parsed)
    assert library.scan() == {'seen': 1, 'changed': 1}
    assert library.scan() == {'seen': 1, 'changed': 0}
    hit = library.search('synthetic diagnostic measurement')[0]
    segments, sha = library.retrieve(hit)
    assert sha == hashlib.sha256(path.read_bytes()).hexdigest()
    assert 'SYNTHETIC length >= 5.0 mm' in segments[0]
    path.write_text('changed', encoding='utf-8')
    try:
        library.retrieve(hit)
        assert False, 'changed source must be rejected'
    except SourceError as exc:
        assert str(exc) == 'LIBRARY_FILE_CHANGED'


def test_content_list_and_jats_license():
    data = json.dumps([{'type': 'title', 'text': 'Synthetic title'},
                       {'type': 'text', 'text': 'SYNTHETIC length >= 5.0 mm'},
                       {'type': 'image', 'text': 'ignored'}]).encode()
    assert 'SYNTHETIC' in parse_mineru(data, '.json')[0]
    xml = b'''<article><front><article-meta><permissions><license
      xmlns:xlink="http://www.w3.org/1999/xlink"
      xlink:href="https://creativecommons.org/licenses/by/4.0/"/></permissions></article-meta></front>
      <body><sec><p>SYNTHETIC length >= 5.0 mm; no medical meaning.</p></sec></body></article>'''
    segments, license_note = parse_full_text_xml(xml)
    assert license_note == 'CC BY' and 'SYNTHETIC' in segments[0]
    try:
        parse_full_text_xml(xml.replace(b'creativecommons.org/licenses/by/4.0/', b'example.org/license/'))
        assert False, 'unlicensed content must be rejected'
    except SourceError as exc:
        assert str(exc) == 'WEB_ARTICLE_LICENSE_NOT_RETAINABLE'


def test_parsed_library_first_publishes_and_exports_audit(queue, tmp_path):
    root = tmp_path / 'drive'
    directories(root)
    parsed = root / 'parse_pdf' / 'synthetic-criteria.md'
    parsed.write_text('# synthetic diagnostic measurement\nSYNTHETIC length >= 5.0 mm', encoding='utf-8')
    library = Library(queue.sessions, root / 'parse_pdf')
    library.scan()
    sha = hashlib.sha256(parsed.read_bytes()).hexdigest()
    draft = draft_for(sha, 'parsed-'+sha[:40])
    class AI:
        model = 'configured-gemini-test'
        calls = []
        def __init__(self): self.index = 0
        def ask(self, task, data):
            values = [{'eligible': True, 'search_terms': 'synthetic diagnostic measurement'},
                      {'suitable': True}, {'card': draft},
                      {'context_supported': True, 'claims': [{'id': 'c1', 'supported': True}], 'notes': 'synthetic'}]
            result = values[self.index]
            self.index += 1
            return result
    class NoWeb:
        def search(self, terms): raise AssertionError('online search ran before suitable parsed library')
    queue.receive(update(1))
    pipeline = ResearchPipeline(queue.sessions, AI(), root / 'source_pdf', NoWeb(),
                                library_root=root / 'parse_pdf', source_web_root=root / 'source_web')
    queue.process(pipeline)
    with queue.sessions() as db:
        job = db.scalar(select(ResearchJob))
        assert job.status == 'completed', job.error_code
        package = db.scalar(select(AuditPackage))
        source = db.get(Document, 'parsed-'+sha[:40])
        assert package and source.payload['source_format'] == 'parsed_text'
        assert source.payload['archive_reference'] == 'parse_pdf/synthetic-criteria.md'
    assert DriveSync(queue.sessions, root).check_sources(package.payload)[0]['status'] == 'HASH_MATCH'
    assert queue.service.get_published(job.card_id)['verification']['gemini_model'] == 'configured-gemini-test'


@pytest.mark.parametrize('late_fallback', [False, True])
def test_unavailable_library_uses_licensed_web_source(queue, tmp_path, late_fallback):
    root = tmp_path / 'drive'
    directories(root)
    (root / 'parse_pdf' / 'synthetic-criteria.md').write_text(
        '# synthetic diagnostic measurement\nThis document discusses history only, not criteria.', encoding='utf-8')
    Library(queue.sessions, root / 'parse_pdf').scan()
    segments = ['SYNTHETIC length >= 5.0 mm; synthetic diagnostic measurement.']
    content = ('[SEGMENT 1]\n' + segments[0] + '\n').encode()
    sha = hashlib.sha256(content).hexdigest()
    draft = draft_for(sha, 'web-'+sha[:40])
    class AI:
        model = 'configured-gemini-test'
        calls = []
        def __init__(self): self.index = 0
        def ask(self, task, data):
            values = [{'eligible': True, 'search_terms': 'synthetic diagnostic measurement'},
                      {'suitable': late_fallback}]
            if late_fallback:
                values.append({'card': None})
            values += [{'pmcids': ['PMC123']}, {'card': draft},
                      {'context_supported': True, 'claims': [{'id': 'c1', 'supported': True}], 'notes': 'synthetic'}]
            result = values[self.index]
            self.index += 1
            return result
    class Web:
        def search(self, terms):
            return [{'pmcid': 'PMC123', 'title': 'Synthetic', 'authors': 'TEST', 'year': '2026', 'doi': None}]
        def retrieve(self, candidate): raise SourceError('NO_RETAINABLE_PDF')
        def retrieve_web(self, candidate): return segments, 'CC BY'
    queue.receive(update(1))
    pipeline = ResearchPipeline(queue.sessions, AI(), root / 'source_pdf', Web(),
                                library_root=root / 'parse_pdf', source_web_root=root / 'source_web')
    queue.process(pipeline)
    with queue.sessions() as db:
        job = db.scalar(select(ResearchJob))
        assert job.status == 'completed', job.error_code
        source = db.get(Document, 'web-'+sha[:40])
        package = db.scalar(select(AuditPackage))
        assert source.payload['source_format'] == 'web_text'
    assert DriveSync(queue.sessions, root).check_sources(package.payload)[0]['status'] == 'HASH_MATCH'
    assert (root / 'source_web' / (sha+'.txt')).read_bytes() == content
