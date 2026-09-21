import pytest
from sqlalchemy import select
from pydantic import ValidationError
from app.database import ResearchJob, BotUpdate
from app.schemas import Card
from app.literature import SourceError
from app.research_errors import error_details
from app.job_admin import operate
from tests.test_automation import queue, pipeline_fixture, update


def test_source_code_and_retry_history(queue):
    queue.receive(update(1))
    class Pipeline:
        def run(self,job):raise SourceError('SOURCE_HTTP_503')
    queue.process(Pipeline())
    with queue.sessions() as db:
        job=db.scalar(select(ResearchJob))
        assert job.error_code=='SOURCE_HTTP_503'
        assert job.provenance['failure_history'][0]['code']=='SOURCE_HTTP_503'


def test_schema_diagnostic_omits_input():
    with pytest.raises(ValidationError) as caught:Card.model_validate({'api_key':'synthetic-secret'})
    result=error_details(caught.value)
    assert result['code']=='SCHEMA_VALIDATION_ERROR'
    assert 'synthetic-secret' not in str(result)
    assert all(set(e)=={'field','type'} for e in result['schema_errors'])


def test_schema_repair_reuses_sources(queue,tmp_path,monkeypatch):
    queue.receive(update(1))
    pipeline=pipeline_fixture(queue,tmp_path,monkeypatch)
    original=pipeline.ai.ask
    saved={};repairs=[]
    def ask(task,data):
        if task.startswith('Repair'):
            repairs.append(data)
            return {'card':saved['card']}
        result=original(task,data)
        if result.get('card'):
            import copy
            saved['card']=copy.deepcopy(result['card'])
            result['card'].pop('applicability')
        return result
    pipeline.ai.ask=ask
    queue.process(pipeline)
    with queue.sessions() as db:
        job=db.scalar(select(ResearchJob))
        assert job.status=='completed',job.error_code
    assert len(repairs)==1
    assert any(e['field']=='applicability' for e in repairs[0]['validation_errors'])
    assert repairs[0]['documents']


def test_failed_repair_is_review_not_full_retry(queue,tmp_path,monkeypatch):
    queue.receive(update(1))
    pipeline=pipeline_fixture(queue,tmp_path,monkeypatch)
    original=pipeline.ai.ask
    def ask(task,data):
        if task.startswith('Repair'):return {'card':{'invalid':'synthetic'}}
        result=original(task,data)
        if result.get('card'):result['card'].pop('applicability')
        return result
    pipeline.ai.ask=ask
    queue.process(pipeline)
    with queue.sessions() as db:
        job=db.scalar(select(ResearchJob))
        assert job.status=='needs_review'
        assert job.error_code=='CARD_SCHEMA_INVALID_AFTER_REPAIR'
        assert job.provenance['schema_errors']


def test_operator_retry_preserves_history(queue):
    queue.receive(update(1))
    with queue.sessions.begin() as db:
        job=db.scalar(select(ResearchJob));key=job.id
        job.status='failed';job.attempts=2;job.error_code='SOURCE_HTTP_503'
        job.provenance={'failure_history':[{'code':'SOURCE_HTTP_503'}]}
        db.get(BotUpdate,1).delivered=True
    result=operate(queue.sessions,key[:12],True)
    assert result['status']=='queued' and result['failure_history']
    with queue.sessions() as db: assert not db.get(BotUpdate,1).delivered
    with pytest.raises(ValueError):operate(queue.sessions,key[:12],True)


@pytest.mark.parametrize('mutation,expected', [
    ('logic','LOGIC_CLAIM_SET_MISMATCH'),
    ('evidence','CLAIM_EVIDENCE_REFERENCE_MISSING'),
    ('modality','MEASUREMENT_MODALITY_MISMATCH'),
])
def test_cross_field_rules_are_actionable(mutation,expected):
    from tests.fixtures import card
    draft=card()
    if mutation=='logic':draft['logic']['claim_ids']=['nonexistent']
    elif mutation=='evidence':draft['claims'][0]['evidence_ids']=['nonexistent']
    else:draft['measurement']['modality']='different'
    with pytest.raises(ValidationError) as caught:Card.model_validate(draft)
    detail=error_details(caught.value)['schema_errors'][0]
    assert detail['field']==''
    assert detail['rule']==expected
    assert detail['message']


def test_cross_field_repair_receives_exact_rule(queue,tmp_path,monkeypatch):
    import copy
    queue.receive(update(1))
    pipeline=pipeline_fixture(queue,tmp_path,monkeypatch)
    original=pipeline.ai.ask;saved={};repairs=[]
    def ask(task,data):
        if task.startswith('Repair'):
            repairs.append(data)
            assert data['validation_errors'][0]['rule']=='LOGIC_CLAIM_SET_MISMATCH'
            return {'card':saved['card']}
        result=original(task,data)
        if result.get('card'):
            saved['card']=copy.deepcopy(result['card'])
            result['card']['logic']['claim_ids']=['wrong-id']
        return result
    pipeline.ai.ask=ask
    queue.process(pipeline)
    with queue.sessions() as db:assert db.scalar(select(ResearchJob)).status=='completed'
    assert len(repairs)==1
