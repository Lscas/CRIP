"""Offline evaluation coverage for the frozen v9 layout-bound selector."""
from dataclasses import replace
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db import DomainError
from app.main import create_app
from app.page_selector import LAYOUT_BOUND_SELECTOR_VERSION
from app.reference_text_profiles import profile
from app.settings import DEEPSEEK_BASE_URL, Settings
from .test_page_selector import _raw_run, _spatial_text


def _cannot_answer():
    return {
        'status': 'CANNOT_ANSWER', 'reason_code': 'NO_NEW_EVIDENCE',
        'missing_facts': ['Synthetic terminal boundary.'], 'requests': [],
        'answer': {'claims': [], 'calculations': [], 'coverage': []},
    }


def _v9_run(client, project):
    text, text_map = _spatial_text([
        (10, [('Finish', 10, 48), ('PT9', 52, 76)]),
        (30, [('blue', 10, 42), ('RIGHT', 300, 350)]),
    ])
    return _raw_run(client, project, [
        {'page': 1, 'sheet': 'A5.01', 'text': text, 'text_map': text_map},
    ])


def _channel(client, decision):
    gateway = client.app.state.gateway
    gateway.s = replace(
        gateway.s, provider='deepseek', api_base_url=DEEPSEEK_BASE_URL,
        api_key='synthetic-key', live_enabled=True, cheap_model='deepseek-flash',
        vision_enabled=False, structured_output_mode='json_object',
        api_protocol='chat_completions')
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        content = json.loads(payload['messages'][1]['content'])
        calls.append((payload, content))
        value = decision(payload, content)
        return httpx.Response(200, json={
            'id': f'synthetic-v9-evaluation-{len(calls)}',
            'choices': [{'finish_reason': 'stop', 'message': {
                'content': value if isinstance(value, str) else json.dumps(value)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130},
        })

    gateway.client.close()
    gateway.client = httpx.Client(transport=httpx.MockTransport(handler))
    return calls


def _create_v9(client, project, run, profile_id='FLASH_NONE', *, question=None):
    return client.app.state.reference_evaluations.create(
        project['id'], run['id'], 'Frozen v9 layout evaluation',
        [question or 'What approved color applies to Finish key PT9?'],
        profile(profile_id), selector_version=LAYOUT_BOUND_SELECTOR_VERSION)


def _execute(client, evaluation):
    item = evaluation['items'][0]
    response = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{item['item_id']}/execute")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(tmp_path, start_worker=False, reference_layout_enabled=True))
    with TestClient(app, headers={'X-CIRP-Client': 'browser'}) as current:
        yield current


def test_v9_creation_rejects_non_named_profile_without_dispatch(client, project):
    db, run, _ = _v9_run(client, project)
    store = client.app.state.reference_evaluations
    with pytest.raises(DomainError, match='canonical named'):
        store.create(
            project['id'], run['id'], 'Non-named v9 is invalid',
            ['What approved color applies to Finish key PT9?'],
            store.profile(client.app.state.gateway.s),
            selector_version=LAYOUT_BOUND_SELECTOR_VERSION)
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluations')['n'] == 0
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0


def test_v9_named_evaluation_uses_frozen_layout_path_through_management_chain(client, project):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    baseline = _create_v9(client, project, run)

    first = _execute(client, baseline)
    assert first['replayed'] is False
    receipt = first['result']['result']['execution_receipts'][0]
    assert baseline['selector_version'] == LAYOUT_BOUND_SELECTOR_VERSION
    assert receipt['receipt_version'] == 'reference-model-input-receipt-3'
    assert receipt['selector_version'] == LAYOUT_BOUND_SELECTOR_VERSION
    assert receipt['layout_navigation_version'] == 'source-layout-navigation-1'
    assert calls[0][1]['layout_navigation']['navigation_version'] == 'source-layout-navigation-1'
    assert all('layout_lines' not in row for row in calls[0][1]['evidence'])

    v8 = client.app.state.reference_evaluations.create(
        project['id'], run['id'], 'Frozen v8 control',
        ['What approved color applies to Finish key PT9?'], profile('FLASH_NONE'))
    with pytest.raises(DomainError, match='selector'):
        client.app.state.reference_evaluations.attach(
            v8['evaluation_id'], v8['items'][0]['item_id'], first['result']['result_id'])

    replay = _execute(client, baseline)
    assert replay['replayed'] is True
    assert len(calls) == 1

    clone_response = client.post(
        f"/api/reference-evaluations/{baseline['evaluation_id']}/clone",
        json={'profile_id': 'PRO'})
    assert clone_response.status_code == 201, clone_response.text
    candidate = clone_response.json()['evaluation']
    assert candidate['selector_version'] == LAYOUT_BOUND_SELECTOR_VERSION
    assert candidate['run_id'] == baseline['run_id']
    assert candidate['question_set_hash'] == baseline['question_set_hash']
    assert candidate['profile']['profile_id'] == 'PRO'
    candidate_run = _execute(client, candidate)
    assert candidate_run['result']['result']['execution_receipts'][0]['receipt_version'] == (
        'reference-model-input-receipt-3')

    comparison = client.get(
        f"/api/projects/{project['id']}/reference-evaluations/compare",
        params={'baseline_evaluation_id': baseline['evaluation_id'],
                'candidate_evaluation_id': candidate['evaluation_id']})
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()['selector_version'] == LAYOUT_BOUND_SELECTOR_VERSION
    assert comparison.json()['items'][0]['fair_fixed_input'] is True

    scorecard = client.get(
        f"/api/reference-evaluations/{baseline['evaluation_id']}/scorecard")
    assert scorecard.status_code == 200, scorecard.text
    assert scorecard.json()['selector_version'] == LAYOUT_BOUND_SELECTOR_VERSION
    readiness = client.get(
        f"/api/reference-evaluations/{candidate['evaluation_id']}/readiness")
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()['selector_version'] == LAYOUT_BOUND_SELECTOR_VERSION

    queued = _create_v9(
        client, project, run, question='Which drawing sheet contains Finish key PT9?')
    job_response = client.post(
        f"/api/reference-evaluations/{queued['evaluation_id']}/jobs", json={'confirmed': True})
    assert job_response.status_code == 202, job_response.text
    job = job_response.json()
    assert job['selector_version'] == LAYOUT_BOUND_SELECTOR_VERSION
    client.app.state.reference_evaluation_jobs.process(job['job_id'])
    completed = client.get(f"/api/reference-evaluation-jobs/{job['job_id']}")
    assert completed.status_code == 200, completed.text
    assert completed.json()['state'] == 'COMPLETED'
    assert _execute(client, queued)['replayed'] is True
    assert len(calls) == 3


def test_v9_safe_failure_replays_and_cannot_attach_to_v8_evaluation(client, project):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: 'not-json')
    v9 = _create_v9(client, project, run)

    failed = _execute(client, v9)
    assert failed['result'] is None and failed['replayed'] is False
    receipt = failed['failure']['execution_receipt']
    assert receipt['receipt_version'] == 'reference-model-input-receipt-3'
    assert receipt['selector_version'] == LAYOUT_BOUND_SELECTOR_VERSION
    assert _execute(client, v9)['replayed'] is True
    assert len(calls) == 1

    v8 = client.app.state.reference_evaluations.create(
        project['id'], run['id'], 'Frozen v8 control',
        ['What approved color applies to Finish key PT9?'], profile('FLASH_NONE'))
    v8_item = v8['items'][0]
    with pytest.raises(DomainError, match='selector'):
        client.app.state.reference_evaluations.fail(
            v8['evaluation_id'], v8_item['item_id'], 'MODEL_OUTPUT_REJECTED', receipt)
