"""Operator-only inspection/retry; no deletion or automatic retry escalation."""
import argparse
import json
import os
import re
from sqlalchemy import select
from app.database import ResearchJob, BotUpdate, connect


def operate(sessions,prefix,retry=False):
    if not re.fullmatch(r'[a-f0-9]{8,64}',prefix): raise ValueError('Use at least 8 hexadecimal job ID characters')
    with sessions.begin() as db:
        rows=list(db.scalars(select(ResearchJob).where(ResearchJob.id.startswith(prefix)).with_for_update()))
        if len(rows)!=1: raise ValueError('Job prefix must identify exactly one job')
        job=rows[0]
        if retry:
            if job.status not in ('failed','needs_review'): raise ValueError('Only failed/needs_review jobs may be retried')
            if job.card_id: raise ValueError('Existing card requires review, not a job reset')
            job.status,job.attempts,job.available_at,job.error_code='queued',0,0,None
            for update in db.scalars(select(BotUpdate).where(BotUpdate.job_id==job.id)):
                update.delivered=False
                update.acknowledged=False
                update.send_attempts=0
                update.next_send_at=0
        p=job.provenance or {}
        return {'id':job.id,'status':job.status,'error_code':job.error_code,'stage':p.get('stage'),
                'schema_errors':p.get('schema_errors',[]),'source_failures':p.get('source_failures',[]),
                'failure_history':p.get('failure_history',[])}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['inspect','retry'])
    parser.add_argument('job_prefix')
    args=parser.parse_args()
    engine,sessions=connect(os.environ['DATABASE_URL'])
    try: print(json.dumps(operate(sessions,args.job_prefix,args.action=='retry'),ensure_ascii=False,indent=2))
    finally: engine.dispose()

if __name__=='__main__':main()
