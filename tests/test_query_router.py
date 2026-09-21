import pytest
from sqlalchemy import select
from app.database import Base, connect, TopicAlias, AliasCandidate, ResearchJob
from app.query_router import QueryRouter, normalize_query, route_key
from app.schemas import Card
from tests.fixtures import card

@pytest.fixture
def router(tmp_path):
    engine,sessions=connect('sqlite:///'+str(tmp_path/'router.db'))
    Base.metadata.create_all(engine)
    r=QueryRouter(sessions);r.bootstrap();r.bootstrap()
    yield r
    engine.dispose()

@pytest.mark.parametrize('query', ['hẹp van hai lá','hẹp van 2 lá','hep van hai la','hep van 2 la','hep 2 la','mitral stenosis','  HẸP   VAN 2 LÁ! '])
def test_canonical_aliases(router,query):
    route=router.local(query)
    assert (route.topic_id,route.intent,route.confidence)==('mitral_stenosis','overview','high')

@pytest.mark.parametrize('query,topic,intent,modality', [
 ('tiêu chuẩn hẹp van hai lá','mitral_stenosis','diagnostic_criteria',None),
 ('tiêu chuẩn siêu âm hẹp van 2 lá','mitral_stenosis','imaging_diagnostic_criteria','ultrasound'),
 ('tiêu chuẩn CT viêm ruột thừa','acute_appendicitis','imaging_diagnostic_criteria','ct'),
 ('gợi ý MRI u màng não','meningioma','diagnostic_features','mri'),
 ('tiêu chuẩn MRI đa xơ cứng','multiple_sclerosis','imaging_diagnostic_criteria','mri'),
 ('MS MRI','multiple_sclerosis','imaging_diagnostic_criteria','mri'),
 ('MS siêu âm','mitral_stenosis','imaging_diagnostic_criteria','ultrasound'),
])
def test_intents(router,query,topic,intent,modality):
    r=router.local(query)
    assert (r.topic_id,r.intent,r.modality)==(topic,intent,modality)

@pytest.mark.parametrize('query',['MS','phân độ hẹp van hai lá','MVA 0.9 là mức nào','điều trị viêm ruột thừa','tiêu chuẩn CT MRI viêm ruột thừa'])
def test_no_unsafe_guess(router,query):
    class AI:
        def ask(self,*args): raise AssertionError('must not call AI')
    assert router.resolve(query,AI()).confidence=='low'

def test_fuzzy_and_numeric_preservation(router):
    assert router.local('hpe van hai la').topic_id=='mitral_stenosis'
    assert normalize_query('MVA <= 0.9')=='mva <= 0.9'
    assert router.local('unrelated words').topic_id is None

def test_fallback_candidates_need_admin_approval(router):
    class AI:
        model='router-from-env'
        def ask(self,*args):return dict(topic_id='mitral_stenosis',intent='overview',modality=None,confidence='high',reason='alias interpretation')
    r=router.resolve('van mitral bi hep',AI())
    assert r.topic_id=='mitral_stenosis'
    assert router.local('van mitral bi hep').topic_id is None
    with router.sessions() as db:
        c=db.scalar(select(AliasCandidate));assert c.router_model=='router-from-env';cid=c.id
    router.approve(cid,'admin@example.org')
    assert router.local('van mitral bi hep').reason=='exact_alias'

def test_router_cannot_invent_topic(router):
    class AI:
        model='router'
        def ask(self,*args):return dict(topic_id='invented',intent='overview',modality=None,confidence='high',reason='')
    assert router.resolve('unrecognized query',AI()).topic_id is None

def test_missing_card_key(router):
    a=router.local('tiêu chuẩn siêu âm hẹp van 2 lá');b=router.local('tieu chuan ultrasound mitral stenosis')
    assert route_key(a)==route_key(b)
    assert route_key(a)!=route_key(router.local('tiêu chuẩn MRI hẹp van hai lá'))

def test_features_are_not_formal_criteria():
    c=card();c['type']='diagnostic_features'
    with pytest.raises(ValueError):Card.model_validate(c)
    c['strength']='supportive';c['logic']['kind']='reference_only';c['logic']['minimum']=None
    assert Card.model_validate(c).strength=='supportive'

def test_bot_deduplicates_canonical_missing_cards(router):
    from app.bot import BotQueue
    q=BotQueue(router.sessions,router=router,cooldown=0)
    for i,text in enumerate(['tiêu chuẩn siêu âm hẹp van 2 lá','tieu chuan ultrasound mitral stenosis'],1):
        q.receive({'update_id':i,'message':{'chat':{'id':i},'text':text}})
    with router.sessions() as db:
        jobs=list(db.scalars(select(ResearchJob)))
        assert len(jobs)==1
        assert jobs[0].provenance['route']['topic_id']=='mitral_stenosis'



def test_published_overview_buttons_and_withdrawal(router):
    from app.bot import BotQueue
    from app.database import BotUpdate, BotMenu
    from app.service import KnowledgeService
    from app.schemas import SourceVersion
    from tests.fixtures import source
    service=KnowledgeService(router.sessions)
    service.register_source(SourceVersion.model_validate(source()))
    c=card(); c['origin']='ai_extracted';c['type']='diagnostic_criteria'; c['topic_id']='mitral_stenosis';c['name_vi']='Hẹp van hai lá';c['name_en']='Mitral stenosis'
    service.add_revision('menu-test',0,Card.model_validate(c),'test')
    service.publish_preliminary('menu-test',1,'test','synthetic fixture','model')
    router.bootstrap()
    q=BotQueue(router.sessions,router=router)
    q.receive({'update_id':11,'message':{'chat':{'id':1},'text':'hep 2 la'}})
    with router.sessions() as db:
        menu=db.get(BotMenu,11);button=menu.buttons[0][0]['callback_data']
        assert db.get(BotUpdate,11).job_id is None
    q.receive({'update_id':12,'callback_query':{'id':'test','data':button,'message':{'chat':{'id':1}},'from':{'is_bot':False}}})
    with router.sessions() as db: assert 'AI SƠ BỘ' in db.get(BotUpdate,12).reply


def test_router_rejects_conflicting_alias_approval(router):
    with router.sessions.begin() as db:
        c=AliasCandidate(alias='MS',normalized_alias='ms',suggested_topic_id='mitral_stenosis',router_model='test')
        db.add(c);db.flush();cid=c.id
    with pytest.raises(ValueError):router.approve(cid,'admin')

def test_routed_pipeline_keeps_canonical_topic(router,tmp_path,monkeypatch):
    from app.bot import BotQueue
    from app.routed_research import RoutedResearch
    from tests.test_automation import pipeline_fixture
    from app.database import Revision
    q=BotQueue(router.sessions,router=router,cooldown=0)
    q.receive({'update_id':1,'message':{'chat':{'id':1},'text':'tiêu chuẩn hẹp van hai lá'}})
    fixture=pipeline_fixture(q,tmp_path,monkeypatch)
    original_ask=fixture.ai.ask
    def ask(task,data):
        result=original_ask(task,data)
        if 'card' in result: result['card']['type']='diagnostic_criteria'
        return result
    fixture.ai.ask=ask
    wrapper=RoutedResearch(router.sessions,fixture.ai,tmp_path,router,fixture.ai)
    wrapper.pipeline=fixture
    q.process(wrapper)
    with router.sessions() as db:
        job=db.scalar(select(ResearchJob))
        assert job.status=='completed',job.error_code
        revision=db.scalar(select(Revision))
        assert revision.payload['topic_id']=='mitral_stenosis'
        assert job.provenance['route']['intent']=='diagnostic_criteria'
