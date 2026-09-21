import copy
import pytest
from sqlalchemy import select
from app.database import ResearchJob
from app.schemas import Card, SourceVersion
from app.research import bind_evidence_source, extraction_schema
from tests.fixtures import card, source
from tests.test_automation import queue, pipeline_fixture, update


def test_exact_binding_only():
    s=SourceVersion.model_validate(source())
    evidence=Card.model_validate(card()).evidence[0]
    evidence.document_version_id=s.source_id
    evidence.document_sha256=s.sha256
    assert bind_evidence_source(evidence,[s]) is s
    assert evidence.document_version_id==s.id
    evidence.document_version_id='invented-source'
    assert bind_evidence_source(evidence,[s]) is None
    evidence.document_version_id=s.source_id
    evidence.document_sha256='0'*64
    assert bind_evidence_source(evidence,[s]) is None


def test_schema_limits_ids():
    s=SourceVersion.model_validate(source())
    fields=extraction_schema([s])['$defs']['Evidence']['properties']
    assert fields['document_version_id']['enum']==[s.id]
    assert fields['document_sha256']['enum']==[s.sha256]


@pytest.mark.parametrize('identifier,success',[('PMC123',True),('made-up-id',False)])
def test_pipeline_binds_only_verified_source(queue,tmp_path,monkeypatch,identifier,success):
    queue.receive(update(1))
    with queue.sessions.begin() as db:
        job=db.scalar(select(ResearchJob))
        job.provenance={'schema_errors':[{'field':'','type':'value_error'}], 'source_failures':[{'pmcid':'old'}]}
    pipeline=pipeline_fixture(queue,tmp_path,monkeypatch)
    original=pipeline.ai.ask
    def ask(task,data):
        result=original(task,data)
        if result.get('card'):
            result['card']['evidence'][0]['document_version_id']=identifier
        return result
    pipeline.ai.ask=ask
    queue.process(pipeline)
    with queue.sessions() as db:
        job=db.scalar(select(ResearchJob))
        assert job.provenance['schema_errors']==[]
        assert job.provenance['source_failures']==[]
        assert job.provenance['retained_sources']
        if success:assert job.status=='completed',job.error_code
        else:
            assert job.error_code=='UNRETAINED_EVIDENCE'
            assert job.provenance['stage']=='evidence_binding_failed'
            assert job.provenance['evidence_errors'][0]['document_version_id']==identifier
