"""Canonical topic routing. Names/aliases are vocabulary, never clinical evidence."""
import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Literal
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from app.database import Topic, TopicAlias, TopicCardBinding, AliasCandidate, QueryEvent, Revision, CardHead, now

INTENTS = ('overview', 'diagnostic_criteria', 'imaging_diagnostic_criteria', 'diagnostic_features')
MODALITIES = ('ultrasound', 'ct', 'mri', 'xray', 'nuclear_medicine', 'other')
# Vocabulary supplied in the design document; no criteria/thresholds are seeded.
VOCABULARY = [
 ('mitral_stenosis', 'Hẹp van hai lá', 'Mitral stenosis', ['hep 2 la', 'MS']),
 ('multiple_sclerosis', 'Đa xơ cứng', 'Multiple sclerosis', ['MS']),
 ('acute_appendicitis', 'Viêm ruột thừa', 'Acute appendicitis', []),
 ('acute_pancreatitis', 'Viêm tụy cấp', 'Acute pancreatitis', []),
 ('acute_cholecystitis', 'Viêm túi mật cấp', 'Acute cholecystitis', ['viem tui mat']),
 ('renal_artery_stenosis', 'Hẹp động mạch thận', 'Renal artery stenosis', []),
 ('meningioma', 'U màng não', 'Meningioma', []),
]


def normalize_query(value):
    value = unicodedata.normalize('NFKC', value).casefold().replace('đ', 'd')
    value = ''.join(c for c in unicodedata.normalize('NFD', value) if not unicodedata.combining(c))
    value = re.sub(r'\b2\s+la\b', 'hai la', value)
    value = re.sub(r'\bhpe\s+van\b', 'hep van', value)
    # Keep numeric comparators and decimal points: never change a measurement.
    return ' '.join(re.sub(r'[^\w\s.,<>=+/-]', ' ', value).split())


class Route(BaseModel):
    model_config = ConfigDict(extra='forbid')
    topic_id: str | None = None
    intent: Literal['overview', 'diagnostic_criteria', 'imaging_diagnostic_criteria', 'diagnostic_features', 'unknown'] = 'overview'
    modality: Literal['ultrasound', 'ct', 'mri', 'xray', 'nuclear_medicine', 'other'] | None = None
    confidence: Literal['high', 'medium', 'low'] = 'low'
    reason: str = ''


def detect(query):
    q = normalize_query(query)
    if re.search(r'\b(phan do|severity|classification|phan loai|do luong|cach do|measurement|dieu tri|management|follow up|theo doi|chan doan phan biet|differential|clinical score|tinh diem|pitfalls)\b', q) or re.search(r'\d[.,]\d', q):
        return Route(intent='unknown', reason='intent_not_in_mvp'), q
    modality = None
    for m, pattern in [('ultrasound', r'\b(sieu am|ultrasound|doppler)\b'), ('ct', r'\b(ct|cat lop vi tinh)\b'), ('mri', r'\b(mri|cong huong tu)\b'), ('xray', r'\b(xray|x quang)\b')]:
        if re.search(pattern, q):
            if modality: return Route(reason='multiple_modalities'), q
            modality = m
            q = re.sub(pattern, ' ', q)
    features = bool(re.search(r'\b(goi y|dac diem|dau hieu|features|findings)\b', q))
    criteria = bool(re.search(r'\b(tieu chuan|criteria)\b', q))
    intent = 'diagnostic_features' if features else 'imaging_diagnostic_criteria' if modality else 'diagnostic_criteria' if criteria else 'overview'
    q = re.sub(r'\b(tieu chuan|chan doan|diagnostic criteria|criteria|goi y|dac diem|dau hieu|features|findings)\b', ' ', q)
    return Route(intent=intent, modality=modality), ' '.join(q.split())


class QueryRouter:
    def __init__(self, sessions): self.sessions = sessions

    def bootstrap(self):
        with self.sessions.begin() as db:
            for tid, vi, en, aliases in VOCABULARY:
                if not db.get(Topic, tid):
                    db.add(Topic(id=tid, canonical_name_vi=vi, canonical_name_en=en))
                    db.flush()
                for alias in [vi, en, *aliases]:
                    n = normalize_query(alias)
                    if not db.scalar(select(TopicAlias.id).where(TopicAlias.topic_id == tid, TopicAlias.normalized_alias == n)):
                        db.add(TopicAlias(topic_id=tid, alias=alias, normalized_alias=n, is_ambiguous=n=='ms', alias_type='abbreviation' if n=='ms' else 'common_name'))
            # Existing revisions stay immutable. Register their identities without merging diseases automatically.
            for rev in db.scalars(select(Revision).join(CardHead, CardHead.id==Revision.card_id).where(Revision.number==CardHead.latest)):
                c=rev.payload; tid=c['topic_id']
                matches={v[0] for v in VOCABULARY if normalize_query(c['name_vi'])==normalize_query(v[1]) or normalize_query(c['name_en'])==normalize_query(v[2])}
                if len(matches)==1: tid=next(iter(matches))
                if not db.get(Topic, tid):
                    db.add(Topic(id=tid, canonical_name_vi=c['name_vi'], canonical_name_en=c['name_en']))
                    db.flush()
                if not db.get(TopicCardBinding,rev.card_id):
                    db.add(TopicCardBinding(card_id=rev.card_id,topic_id=tid))
                for alias in [c['name_vi'], c['name_en'], *c.get('aliases', [])]:
                    n=normalize_query(alias)
                    if not db.scalar(select(TopicAlias.id).where(TopicAlias.topic_id==tid, TopicAlias.normalized_alias==n)):
                        db.add(TopicAlias(topic_id=tid, alias=alias, normalized_alias=n))
                        db.flush()

    def local(self, query):
        route, core = detect(query)
        if route.intent == 'unknown' or route.reason == 'multiple_modalities': return route
        with self.sessions() as db:
            aliases=list(db.scalars(select(TopicAlias)))
            topics={t.id:t for t in db.scalars(select(Topic))}
        direct={a.topic_id for a in aliases if a.normalized_alias==core}
        if core in topics: direct.add(core)
        if len(direct)==1 and not any(a.is_ambiguous and a.normalized_alias==core for a in aliases):
            return route.model_copy(update={'topic_id':next(iter(direct)), 'confidence':'high', 'reason':'exact_alias'})
        if (len(direct)>1 or core=='ms') and not (core=='ms' and route.modality in ('mri','ultrasound')): return route.model_copy(update={'reason':'ambiguous_abbreviation'})
        # Context can disambiguate MS; no blanket abbreviation replacement.
        if re.search(r'\bms\b', core):
            tid='multiple_sclerosis' if route.modality=='mri' else 'mitral_stenosis' if route.modality=='ultrasound' else None
            if tid: return route.model_copy(update={'topic_id':tid,'confidence':'high','reason':'abbreviation_context'})
            return route.model_copy(update={'reason':'ambiguous_abbreviation'})
        scores={}
        for a in aliases:
            if a.is_ambiguous or len(a.normalized_alias)<5 or len(core)<5: continue
            score=SequenceMatcher(None, core, a.normalized_alias).ratio()
            scores[a.topic_id]=max(scores.get(a.topic_id,0),score)
        ranked=sorted(scores.items(),key=lambda x:x[1],reverse=True)
        if ranked and ranked[0][1]>=.91 and (len(ranked)==1 or ranked[0][1]-ranked[1][1]>=.1):
            return route.model_copy(update={'topic_id':ranked[0][0],'confidence':'high','reason':'fuzzy'})
        # Token/full-name retrieval, conservative: a complete alias must occur.
        names={a.topic_id for a in aliases if not a.is_ambiguous and len(a.normalized_alias.split())>=2 and (' '+a.normalized_alias+' ') in (' '+core+' ')}
        if len(names)==1:
            return route.model_copy(update={'topic_id':next(iter(names)),'confidence':'high','reason':'token_search'})
        return route.model_copy(update={'reason':'unresolved'})

    def resolve(self, query, ai=None):
        route=self.local(query)
        if route.confidence=='high' or route.reason in ('intent_not_in_mvp','ambiguous_abbreviation','multiple_modalities') or ai is None: return route
        with self.sessions() as db:
            topics=[{'topic_id':t.id,'vi':t.canonical_name_vi,'en':t.canonical_name_en} for t in db.scalars(select(Topic).limit(200))]
            aliases=[{'topic_id':a.topic_id,'alias':a.alias} for a in db.scalars(select(TopicAlias).limit(600))]
        result=Route.model_validate(ai.ask('Route only, never answer medically or generate thresholds. Choose an existing supplied topic_id or null. Allowed intents: overview, diagnostic_criteria, imaging_diagnostic_criteria, diagnostic_features, unknown. Output exactly topic_id,intent,modality,confidence,reason. Ambiguous or uncertain queries must return low confidence. Do not create topics. Only high-confidence mappings can be used.', {'original_query':query,'normalized_query':normalize_query(query),'candidate_topics':topics,'known_aliases':aliases}))
        if result.topic_id not in {t['topic_id'] for t in topics} or result.confidence!='high' or result.intent=='unknown': return route
        if result.intent=='imaging_diagnostic_criteria' and not result.modality: return route
        # Deterministically recognized intent/modality must not be changed by model.
        if route.modality and result.modality!=route.modality: return route
        if route.intent!='overview' and result.intent!=route.intent: return route
        _, core=detect(query)
        with self.sessions.begin() as db:
            c=db.scalar(select(AliasCandidate).where(AliasCandidate.normalized_alias==core,AliasCandidate.suggested_topic_id==result.topic_id))
            if c: c.hit_count+=1
            else: db.add(AliasCandidate(alias=core,normalized_alias=core,suggested_topic_id=result.topic_id,router_model=ai.model))
        return result

    def record(self, query, route):
        with self.sessions.begin() as db: db.add(QueryEvent(normalized_query=normalize_query(query),route=route.model_dump()))

    def cards(self, route):
        with self.sessions() as db:
            bindings={b.card_id:b.topic_id for b in db.scalars(select(TopicCardBinding))}
            rows=list(db.scalars(select(Revision).join(CardHead,CardHead.id==Revision.card_id).where(Revision.number==CardHead.published)))
        return [{'card_id':r.card_id,'revision_id':r.id,'card':r.payload} for r in rows if bindings.get(r.card_id,r.payload['topic_id'])==route.topic_id and (route.intent=='overview' or r.payload['type']==route.intent) and (not route.modality or normalize_query(r.payload['modality'])==route.modality)]

    def title(self, topic_id):
        with self.sessions() as db:
            topic=db.get(Topic,topic_id)
            return topic.canonical_name_vi if topic else topic_id

    def approve(self, candidate_id, actor):
        with self.sessions.begin() as db:
            c=db.get(AliasCandidate,candidate_id)
            if not c: raise ValueError('Unknown alias candidate')
            existing=list(db.scalars(select(TopicAlias).where(TopicAlias.normalized_alias==c.normalized_alias)))
            if len(c.normalized_alias)<5 or any(a.is_ambiguous or a.topic_id!=c.suggested_topic_id for a in existing): raise ValueError('Ambiguous alias requires explicit disambiguation, not approval')
            if not existing: db.add(TopicAlias(topic_id=c.suggested_topic_id,alias=c.alias,normalized_alias=c.normalized_alias,alias_type='colloquial'))
            c.status,c.reviewed_by,c.reviewed_at='approved',actor,now()


def route_key(route):
    return hashlib.sha256('|'.join([route.topic_id,route.intent,route.modality or '']).encode()).hexdigest()
