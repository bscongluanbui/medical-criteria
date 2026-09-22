import pytest
from sqlalchemy import select
from app.database import ResearchJob
from app.research import extraction_schema
from tests.test_automation import queue, pipeline_fixture, update


def test_route_constrains_schema():
    s=extraction_schema([],{'intent':'imaging_diagnostic_criteria','modality':'ultrasound'})
    assert s['properties']['type']['const']=='imaging_diagnostic_criteria'
    assert s['properties']['modality']['const']=='ultrasound'
    assert extraction_schema([],{'intent':'diagnostic_criteria','modality':None})['properties']['modality'].get('const') is None

@pytest.mark.parametrize('outcome',['fixed','wrong','unsupported'])
def test_route_repair_is_bounded_and_revalidated(queue,tmp_path,monkeypatch,outcome):
    queue.receive(update(1))
    with queue.sessions.begin() as db:
        job=db.scalar(select(ResearchJob))
        job.provenance={'route':{'topic_id':'test-topic','intent':'diagnostic_criteria','modality':None}}
    pipeline=pipeline_fixture(queue,tmp_path,monkeypatch)
    original=pipeline.ai.ask; drafts=[];calls=[]
    def ask(task,data):
        if task.startswith('Resolve this requested-route'):
            calls.append(data)
            if outcome=='unsupported':return {'card':None}
            draft=drafts[0].copy()
            if outcome=='fixed':draft['type']='diagnostic_criteria'
            return {'card':draft}
        result=original(task,data)
        if result.get('card'):drafts.append(result['card'])
        return result
    pipeline.ai.ask=ask
    queue.process(pipeline)
    assert len(calls)==1
    with queue.sessions() as db:
        job=db.scalar(select(ResearchJob))
        if outcome=='fixed':assert job.status=='completed',job.error_code
        else:
            assert job.status=='needs_review'
            assert job.provenance['route_errors'][0]['expected']['type']=='diagnostic_criteria'
            assert job.error_code==('EXTRACTED_CARD_ROUTE_MISMATCH' if outcome=='wrong' else 'REQUESTED_ROUTE_NOT_SUPPORTED_BY_SOURCES')
