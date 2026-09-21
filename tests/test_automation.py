import copy
import hashlib
import io
import json
import tarfile
import time
from pathlib import Path
import httpx
import pytest
from sqlalchemy import select, func
from app.ai_client import AIError, CompatibleAI
from app.bot import BotQueue, answer
from app.database import Base, BotUpdate, CardHead, ResearchJob, AuditPackage, connect
from app.literature import SourceError, fetch, pdf_from_archive, retain_pdf, Literature
from app.research import ResearchPipeline, NeedsReview
from tests.fixtures import card


def update(number, topic='Synthetic diagnostic measurement', chat=123):
    return {'update_id': number, 'message': {'chat': {'id': chat}, 'text': topic, 'from': {'is_bot': False}}}


@pytest.fixture
def queue(tmp_path):
    engine, sessions = connect(f"sqlite:///{tmp_path/'bot.db'}")
    Base.metadata.create_all(engine)
    yield BotQueue(sessions, cooldown=0)
    engine.dispose()


def test_public_bot_arbitrary_ids_and_update_dedup(queue):
    queue.receive(update(1, chat=123))
    queue.receive(update(1, chat=123))
    queue.receive(update(2, chat=999999))
    assert queue.offset() == 3
    with queue.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchJob)) == 1
        assert db.scalar(select(func.count()).select_from(BotUpdate)) == 2
    assert queue.claim().attempts == 1
    assert queue.claim() is None


def test_help_status_and_limits(queue):
    queue.daily_limit = 1
    queue.receive(update(1, '/start'))
    queue.receive(update(2))
    queue.receive(update(3, 'Different topic'))
    queue.receive(update(4, '/status'))
    with queue.sessions() as db:
        assert 'kiến thức' in db.get(BotUpdate, 1).reply
        assert 'giới hạn' in db.get(BotUpdate, 3).reply
        assert 'queued' in db.get(BotUpdate, 4).reply


def test_transient_retry_bounded(queue):
    class Broken:
        def run(self, job):
            raise RuntimeError('secret must not reach the user')
    queue.receive(update(1))
    queue.process(Broken())
    with queue.sessions.begin() as db:
        job = db.scalar(select(ResearchJob))
        assert job.status == 'retry'
        assert job.error_code == 'RuntimeError'
        job.available_at = 0
    queue.process(Broken())
    with queue.sessions() as db:
        assert db.scalar(select(ResearchJob)).status == 'failed'


def test_needs_review_stops_without_publishing(queue):
    class Insufficient:
        def run(self, job):
            raise NeedsReview('NO_OPEN_ACCESS_SOURCE')
    queue.receive(update(1))
    queue.process(Insufficient())
    with queue.sessions() as db:
        assert db.scalar(select(ResearchJob)).status == 'needs_review'
        assert db.scalar(select(CardHead)) is None


def test_ack_and_failed_delivery_backoff(queue):
    sent = []
    class FakeTelegram:
        def send(self, chat, text):
            sent.append((chat, text))
    queue.receive(update(1))
    queue.deliver(FakeTelegram())
    queue.deliver(FakeTelegram())
    assert len(sent) == 1
    class Blocked:
        def send(self, chat, text):
            raise RuntimeError('403')
    queue.receive(update(2, '/start', chat=88))
    queue.deliver(Blocked())
    with queue.sessions() as db:
        row = db.get(BotUpdate, 2)
        assert row.send_attempts == 1 and row.next_send_at > time.time()


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_ai_wire_contract(scheme):
    def handle(request):
        assert str(request.url) == f'{scheme}://gateway.example/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-key'
        payload = json.loads(request.content)
        assert payload['model'] == 'user-gemini-model'
        assert payload['response_format'] == {'type': 'json_object'}
        return httpx.Response(200, json={'model': 'reported-model', 'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]})
    ai = CompatibleAI(f'{scheme}://gateway.example/v1/', 'test-key', 'user-gemini-model', transport=httpx.MockTransport(handle))
    assert ai.ask('Return JSON', {}) == {'ok': True}
    assert ai.calls[0]['reported_model'] == 'reported-model'


@pytest.mark.parametrize('mode', ['truncated', 'invalid', 'http'])
def test_ai_errors_do_not_leak_secret(mode):
    def handle(request):
        if mode == 'http':
            return httpx.Response(401, text='private-key')
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length' if mode == 'truncated' else 'stop', 'message': {'content': 'bad-json private-key'}}]})
    ai = CompatibleAI('https://gateway.example/v1', 'private-key', 'gemini', transport=httpx.MockTransport(handle))
    with pytest.raises(AIError) as error:
        ai.ask('JSON', {})
    assert 'private-key' not in str(error.value)


@pytest.mark.parametrize('url', ['ftp://example.org/v1', 'http:///v1', 'http://user:password@example.org/v1', 'http://example.org/v1#fragment', 'https://user:password@example.org/v1', 'https://example.org/v1?key=secret'])
def test_ai_requires_clean_http_or_https_base(url):
    with pytest.raises(ValueError):
        CompatibleAI(url, 'key', 'model')


def test_download_rejects_arbitrary_hosts_and_private_addresses():
    for url in ['http://127.0.0.1/a.pdf', 'https://169.254.169.254/', 'https://example.com/p.pdf', 'https://www.ncbi.nlm.nih.gov:8443/']:
        with pytest.raises(SourceError):
            fetch(url)


def test_archive_and_retention(tmp_path):
    buffer = io.BytesIO()
    pdf = b'%PDF-1.7\nsynthetic'
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        member = tarfile.TarInfo('../../ignored-path.pdf')
        member.size = len(pdf)
        archive.addfile(member, io.BytesIO(pdf))
    assert pdf_from_archive(buffer.getvalue()) == pdf
    sha = retain_pdf(tmp_path, pdf)
    assert retain_pdf(tmp_path, pdf) == sha
    assert list(tmp_path.iterdir()) == [tmp_path/(sha+'.pdf')]
    (tmp_path/(sha+'.pdf')).write_bytes(b'changed')
    with pytest.raises(SourceError):
        retain_pdf(tmp_path, pdf)


def test_real_pdf_parser_subprocess(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    from app.literature import parse_pdf
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 700 Td ('+b'SYNTHETIC SOFTWARE TEST. '*20+b') Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    target = io.BytesIO()
    writer.write(target)
    assert 'SYNTHETIC' in parse_pdf(target.getvalue())[0]


def pipeline_fixture(queue, tmp_path, monkeypatch, bad_quote=False, failed_check=False):
    pdf = b'%PDF-1.7\nsynthetic content'
    sha = hashlib.sha256(pdf).hexdigest()
    draft = card()
    draft['origin'] = 'ai_extracted'
    draft['evidence'][0].update(document_version_id='pdf-'+sha[:40], document_sha256=sha,
                                quote='bad quote that is not present' if bad_quote else 'SYNTHETIC length >= 5.0 mm')
    class AI:
        model = 'gemini-test'
        calls = []
        def __init__(self): self.index = 0
        def ask(self, task, data):
            values = [{'eligible': True, 'search_terms': 'synthetic diagnostic criteria'},
                      {'pmcids': ['PMC123']}, {'card': draft},
                      {'context_supported': not failed_check, 'claims': [{'id': 'c1', 'supported': True}], 'notes': 'software test'}]
            value = values[self.index]
            self.index += 1
            return value
    class Papers:
        def search(self, terms):
            return [{'pmcid': 'PMC123', 'title': 'Synthetic only', 'authors': 'TEST', 'year': '2026', 'doi': None}]
        def retrieve(self, candidate): return pdf, 'CC BY'
    monkeypatch.setattr('app.research.parse_pdf', lambda data: ['SYNTHETIC length >= 5.0 mm'])
    return ResearchPipeline(queue.sessions, AI(), tmp_path, Papers())


def test_end_to_end_pipeline_auto_publishes_and_prepares_audit(queue, tmp_path, monkeypatch):
    queue.receive(update(1))
    pipeline = pipeline_fixture(queue, tmp_path, monkeypatch)
    queue.process(pipeline)
    with queue.sessions() as db:
        job = db.scalar(select(ResearchJob))
        assert job.status == 'completed', job.error_code
        assert db.scalar(select(AuditPackage)) is not None
        cid = job.card_id
    text = answer(queue.service, cid)
    assert 'AI SƠ BỘ' in text and 'GPT_UNVERIFIED' in text and 'DOCTOR_UNVERIFIED' in text
    assert queue.service.search('Synthetic diagnostic measurement')[0]['card_id'] == cid
    assert pipeline.run(job) == cid  # resume without additional model calls
    queue.receive(update(2))
    with queue.sessions() as db:
        assert db.get(BotUpdate, 2).reply


@pytest.mark.parametrize('failure', ['quote', 'check'])
def test_pipeline_stops_on_unsupported_claim(queue, tmp_path, monkeypatch, failure):
    queue.receive(update(1))
    queue.process(pipeline_fixture(queue, tmp_path, monkeypatch, bad_quote=failure=='quote', failed_check=failure=='check'))
    with queue.sessions() as db:
        assert db.scalar(select(ResearchJob)).status == 'needs_review'
        assert db.scalar(select(CardHead)) is None


def test_pmc_cloud_metadata_pdf_checksum(monkeypatch):
    pdf = b'%PDF-1.7\nsynthetic-only'
    md5 = hashlib.md5(pdf, usedforsecurity=False).hexdigest()
    calls = []
    def fake(url, params=None, limit=None):
        calls.append(url)
        if params:
            assert params['prefix'] == 'PMC123.'
            return b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><CommonPrefixes><Prefix>PMC123.2/</Prefix></CommonPrefixes></ListBucketResult>'
        if url.endswith('.json'):
            return json.dumps({'pmcid': 'PMC123', 'version': 2, 'is_retracted': False,
                               'is_pmc_openaccess': True, 'is_manuscript': False,
                               'license_code': 'CC BY',
                               'pdf_url': 's3://pmc-oa-opendata/PMC123.2/PMC123.2.pdf?md5='+md5}).encode()
        return pdf
    monkeypatch.setattr('app.literature.fetch', fake)
    assert Literature().retrieve({'pmcid': 'PMC123'}) == (pdf, 'CC BY; PMC dataset version 2')
    assert all('oa.fcgi' not in url for url in calls)


def test_search_uses_actual_ids_and_excludes_retractions(monkeypatch):
    def fake(url, params, limit):
        assert 'OPEN_ACCESS:Y' in params['query']
        return json.dumps({'resultList': {'result': [
            {'pmcid': 'PMC1', 'title': 'real', 'isRetracted': 'N'},
            {'pmcid': 'PMC2', 'title': 'retracted', 'isRetracted': 'Y'},
            {'pmcid': '../../bad', 'title': 'invalid'}]}}).encode()
    monkeypatch.setattr('app.literature.fetch', fake)
    assert [r['pmcid'] for r in Literature().search('criteria')] == ['PMC1']


def test_ai_model_from_environment(monkeypatch):
    monkeypatch.setenv('AI_BASE_URL', 'http://gateway.example:8080/v1')
    monkeypatch.setenv('AI_API_KEY', 'synthetic-key')
    monkeypatch.setenv('AI_MODEL', 'custom-model-from-env')
    assert CompatibleAI.from_env().model == 'custom-model-from-env'


def test_resume_keeps_original_model(queue, tmp_path, monkeypatch):
    queue.receive(update(1))
    pipeline = pipeline_fixture(queue, tmp_path, monkeypatch)
    original_publish = pipeline.service.publish_preliminary
    def fail_publish(*args):
        raise RuntimeError('simulated crash before publication')
    monkeypatch.setattr(pipeline.service, 'publish_preliminary', fail_publish)
    with queue.sessions() as db:
        job = db.scalar(select(ResearchJob))
    with pytest.raises(RuntimeError):
        pipeline.run(job)
    with queue.sessions() as db:
        assert db.get(ResearchJob, job.id).provenance['configured_model'] == 'gemini-test'
    captured = []
    def capture(*args):
        captured.append(args[-1])
        return original_publish(*args)
    pipeline.ai.model = 'different-model-after-restart'
    monkeypatch.setattr(pipeline.service, 'publish_preliminary', capture)
    pipeline.run(job)
    assert captured == ['gemini-test']
