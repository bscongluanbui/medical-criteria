"""Bounded diagnostic records, without API keys, model responses or source text."""
import re
import time
from pydantic import ValidationError
import httpx


def error_details(exc):
    if isinstance(exc, ValidationError):
        return {'code':'SCHEMA_VALIDATION_ERROR','schema_errors':[
            {'field':'.'.join(str(x) for x in e['loc'])[:180], 'type':e['type']}
            for e in exc.errors(include_url=False,include_context=False,include_input=False)[:15]]}
    if isinstance(exc,httpx.TimeoutException):return {'code':'SOURCE_TIMEOUT'}
    if isinstance(exc,httpx.HTTPError):return {'code':'SOURCE_TRANSPORT_ERROR'}
    from app.literature import SourceError
    from app.ai_client import AIError
    from app.research import NeedsReview
    if isinstance(exc,(SourceError,AIError,NeedsReview)) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,99}',str(exc)):
        return {'code':str(exc)}
    return {'code':type(exc).__name__[:100]}


def record_failure(record,exc):
    details=error_details(exc)
    record.error_code=details['code']
    provenance=record.provenance or {}
    history=list(provenance.get('failure_history',[]))[-9:]
    history.append({'at':int(time.time()),'attempt':record.attempts,'stage':provenance.get('stage','routing'),**details})
    record.provenance={**provenance,'failure_history':history}
