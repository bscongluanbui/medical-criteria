"""Routing happens before source discovery; unknown topics never become raw-query cards."""
import time
from app.database import ResearchJob
from app.query_router import Route, route_key
from app.research import ResearchPipeline, NeedsReview


class RoutedResearch:
    def __init__(self, sessions, ai, root, router, router_ai):
        self.sessions, self.router, self.router_ai = sessions, router, router_ai
        self.pipeline = ResearchPipeline(sessions, ai, root)

    def run(self, job):
        route_data=(job.provenance or {}).get('route')
        route=Route.model_validate(route_data) if route_data else self.router.resolve(job.query, self.router_ai)
        self.router.record(job.query,route)
        if not route.topic_id or route.confidence!='high' or route.intent=='unknown':
            raise NeedsReview('QUERY_NEEDS_CLARIFICATION')
        hits=self.router.cards(route)
        if hits:
            if len(hits)==1: return hits[0]['card_id']
            raise NeedsReview('MULTIPLE_CARDS_SELECT_FROM_OVERVIEW')
        if route.intent=='overview': route=route.model_copy(update={'intent':'diagnostic_criteria'})
        key=route_key(route)
        canonical_query=self.router.title(route.topic_id)+' / '+route.intent+' / '+(route.modality or '')
        with self.sessions.begin() as db:
            original=db.get(ResearchJob,job.id)
            original.provenance={**(original.provenance or {}),'route':route.model_dump(),'router_model':self.router_ai.model}
            target=db.get(ResearchJob,key)
            if target is None:
                target=ResearchJob(id=key,query=canonical_query,status='running',created_at=int(time.time()),available_at=int(time.time())+1800,provenance={'route':route.model_dump()})
                db.add(target)
                db.flush()
            else:
                target.provenance={**(target.provenance or {}),'route':route.model_dump()}
                target.query=canonical_query
            completed_card = target.card_id if target.status=='completed' else None
        if completed_card:
            return self.pipeline.resume(completed_card)
        try:
            card_id=self.pipeline.run(target)
        except Exception:
            if key != job.id:
                with self.sessions.begin() as db:
                    db.get(ResearchJob,key).status='needs_review'
            raise
        with self.sessions.begin() as db:
            row=db.get(ResearchJob,key)
            row.status,row.card_id='completed',card_id
        return card_id
