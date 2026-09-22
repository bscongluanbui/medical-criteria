"""Bounded diagnostic records, without API keys, model responses or source text."""
import re
import time
from pydantic import ValidationError
import httpx


# Only fixed application messages can reach diagnostics/repair; never model input.
SCHEMA_RULES = {
    "bbox must be normalized, ordered page coordinates": "EVIDENCE_BBOX_INVALID",
    "duplicate logic claim": "LOGIC_DUPLICATE_CLAIM",
    "at_least requires a valid minimum": "LOGIC_MINIMUM_INVALID",
    "minimum is only valid for at_least": "LOGIC_MINIMUM_NOT_APPLICABLE",
    "imaging diagnostic criteria require a normalized modality": "IMAGING_MODALITY_INVALID",
    "diagnostic features must be supportive and reference_only, not a formal diagnostic rule": "FEATURES_MUST_BE_SUPPORTIVE",
    "duplicate claim/evidence id": "DUPLICATE_CLAIM_OR_EVIDENCE_ID",
    "claim refers to missing evidence": "CLAIM_EVIDENCE_REFERENCE_MISSING",
    "logic must reference every claim exactly once": "LOGIC_CLAIM_SET_MISMATCH",
    "measurement card requires measurement protocol": "MEASUREMENT_PROTOCOL_REQUIRED",
    "measurement modality does not match card": "MEASUREMENT_MODALITY_MISMATCH",
}


def schema_detail(error):
    detail = {'field': '.'.join(str(x) for x in error['loc'])[:180], 'type': error['type']}
    message = error.get('msg', '').removeprefix('Value error, ')
    if message in SCHEMA_RULES:
        detail.update(rule=SCHEMA_RULES[message], message=message)
    return detail


def error_details(exc):
    if isinstance(exc, ValidationError):
        return {'code':'SCHEMA_VALIDATION_ERROR','schema_errors':[
            schema_detail(e)
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
    history.append({'at':int(time.time()),'attempt':record.attempts,'stage':provenance.get('stage','routing'), 'route_errors':provenance.get('route_errors',[]), 'schema_errors':provenance.get('schema_errors',[]), 'source_failures':provenance.get('source_failures',[]), 'evidence_errors':provenance.get('evidence_errors',[]), 'evidence_corrections':provenance.get('evidence_corrections',[]), **details})
    record.provenance={**provenance,'failure_history':history}
