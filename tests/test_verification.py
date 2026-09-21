"""Synthetic fixtures only: verify publication and external audit contracts."""
import copy
import pytest
from tests.test_dashboard import client, login, publish
from tests.fixtures import card


def ai_card(client, headers, expected=1):
    payload = card()
    payload['origin'] = 'ai_extracted'
    r = client.post('/web/cards/test/revisions', headers=headers, json={'expected_revision': expected, 'card': payload})
    assert r.status_code == 201, r.text
    return expected + 1


def preliminary(client, headers, revision=2):
    return client.post('/web/cards/test/publish-preliminary', headers=headers, json={
        'expected_revision': revision, 'model': 'Gemini-test', 'reason': 'synthetic only'})


def package(client, headers, revision=2):
    r = client.post('/web/cards/test/audit-package', headers=headers, json={'expected_revision': revision, 'reason': 'test'})
    assert r.status_code == 200, r.text
    return r.json()


def result(pkg):
    return {**{k: pkg[k] for k in ['audit_package_id', 'card_id', 'revision', 'content_sha256', 'source_hashes']},
            'model': 'ChatGPT-Web-label', 'overall': 'GPT_VERIFIED',
            'claims': [{'claim_id': 'c1', 'result': 'SUPPORTED', 'evidence_ids': ['e1'], 'notes': 'Synthetic source page 1'}],
            'context_result': 'SUPPORTED', 'context_notes': 'Synthetic applicability and measurement checked', 'notes': 'test'}


def test_preliminary_public_badges_and_doctor_upgrade(client):
    headers = login(client)
    assert preliminary(client, headers, 1).status_code == 422
    ai_card(client, headers)
    assert preliminary(client, headers).status_code == 200
    assert client.app.state.service.pending()[0]['revision'] == 2
    assert client.post('/web/featured', headers=headers, json={'slot': 1, 'card_id': 'test', 'revision': 2}).status_code == 200
    row = client.get('/public/cards').json()['results'][0]
    assert row['verification']['doctor'] == 'DOCTOR_UNVERIFIED'
    assert row['verification']['gemini'] == 'GEMINI_CREATED'
    assert row['verification']['chatgpt'] == 'GPT_UNVERIFIED'
    assert publish(client, headers, 2).status_code == 200
    assert client.app.state.service.pending() == []
    assert client.get('/public/cards').json()['results'][0]['verification']['doctor'] == 'DOCTOR_VERIFIED'
    ai_card(client, headers, 2)
    assert preliminary(client, headers, 3).status_code == 409


def test_import_idempotent_revision_bound_and_immutable(client):
    headers = login(client)
    ai_card(client, headers)
    pkg = package(client, headers)
    body = result(pkg)
    assert client.post('/web/audit-results', headers=headers, json=body).status_code == 200
    assert client.post('/web/audit-results', headers=headers, json=body).json()['duplicate']
    body['notes'] = 'changed'
    assert client.post('/web/audit-results', headers=headers, json=body).status_code == 409
    assert client.get('/web/cards').json()['results'][0]['verification']['chatgpt'] == 'GPT_VERIFIED'
    old = result(package(client, headers))
    ai_card(client, headers, 2)
    response = client.post('/web/audit-results', headers=headers, json=old)
    assert response.status_code == 200 and response.json()['historical']
    assert client.get('/web/cards').json()['results'][0]['verification']['chatgpt'] == 'GPT_UNVERIFIED'


@pytest.mark.parametrize('mutation', ['hash', 'source', 'revision', 'missing', 'duplicate', 'evidence', 'overall'])
def test_invalid_audits_rejected(client, mutation):
    headers = login(client)
    ai_card(client, headers)
    body = result(package(client, headers))
    if mutation == 'hash': body['content_sha256'] = 'b' * 64
    if mutation == 'source': body['source_hashes'] = {}
    if mutation == 'revision': body['revision'] = 1
    if mutation == 'missing': body['claims'] = []
    if mutation == 'duplicate': body['claims'] *= 2
    if mutation == 'evidence': body['claims'][0]['evidence_ids'] = ['bad']
    if mutation == 'overall': body['claims'][0]['result'] = 'CONTRADICTED'
    assert client.post('/web/audit-results', headers=headers, json=body).status_code in [409, 422]


def test_conflict_hides_published_and_prevents_republication(client):
    headers = login(client)
    ai_card(client, headers)
    assert preliminary(client, headers).status_code == 200
    client.post('/web/featured', headers=headers, json={'slot': 1, 'card_id': 'test', 'revision': 2})
    body = result(package(client, headers))
    body['claims'][0]['result'] = 'CONTRADICTED'
    body['overall'] = 'GPT_CONFLICT'
    assert client.post('/web/audit-results', headers=headers, json=body).status_code == 200
    assert client.get('/public/cards').json()['results'] == []
    assert preliminary(client, headers).status_code == 409
    assert publish(client, headers, 2).status_code == 409


def test_audit_auth_csrf(client):
    assert client.post('/web/audit-results', json={}).status_code == 403
    headers = login(client)
    del headers['X-CSRF-Token']
    assert client.post('/web/cards/test/audit-package', headers=headers, json={'expected_revision': 1, 'reason': 'test'}).status_code == 403


def test_indeterminate_is_not_verified(client):
    headers = login(client)
    body = result(package(client, headers, 1))
    body['context_result'] = 'INDETERMINATE'
    body['overall'] = 'GPT_INDETERMINATE'
    assert client.post('/web/audit-results', headers=headers, json=body).status_code == 200
    assert client.get('/web/cards').json()['results'][0]['verification']['chatgpt'] == 'GPT_INDETERMINATE'
