"""Named-profile management and fair-input comparisons, with synthetic HTTP only."""
from dataclasses import replace
import hashlib
import json

import httpx
import pytest

from app.settings import DEEPSEEK_BASE_URL
from .test_evidence_loop import _empty_answer, _provider_answer
from .test_page_selector import _raw_run
from .test_reference_results import _saved_run


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
        calls.append(payload)
        data = decision(payload, json.loads(payload['messages'][1]['content']))
        return httpx.Response(200, json={
            'id':f'synthetic-profile-comparison-{len(calls)}',
            'choices':[{'finish_reason':'stop', 'message':{
                'content':data if isinstance(data, str) else json.dumps(data)}}],
            'usage':{'prompt_tokens':100, 'completion_tokens':30, 'total_tokens':130}})

    gateway.client.close()
    gateway.client = httpx.Client(transport=httpx.MockTransport(handler))
    return calls


def _create(client, project, run, profile_id, question='What approved color applies?'):
    body = {'run_id':run['id'], 'name':'Synthetic named comparison',
            'questions':[question]}
    if profile_id is not None:
        body['profile_id'] = profile_id
    response = client.post(
        f"/api/projects/{project['id']}/reference-evaluations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _execute(client, evaluation):
    response = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{evaluation['items'][0]['item_id']}/execute")
    assert response.status_code == 200, response.text
    return response.json()


def _compare(client, project, baseline, candidate):
    return client.get(f"/api/projects/{project['id']}/reference-evaluations/compare",
        params={'baseline_evaluation_id':baseline['evaluation_id'],
                'candidate_evaluation_id':candidate['evaluation_id']})


def test_three_named_profiles_complete_public_management_chain_without_global_switch(client, project):
    db, run, _, _ = _saved_run(client, project, 'REFERENCE_QA')
    calls = _channel(client, lambda *_: _provider_answer())
    unchanged_settings = client.app.state.gateway.s
    baseline = _create(client, project, run, 'FLASH_NONE')
    evaluations = [baseline]
    for profile_id in ('FLASH_LOW', 'PRO'):
        clone = client.post(
            f"/api/reference-evaluations/{baseline['evaluation_id']}/clone",
            json={'profile_id':profile_id})
        assert clone.status_code == 201, clone.text
        assert clone.json()['model_called'] is False
        evaluation = clone.json()['evaluation']
        assert evaluation['profile']['profile_id'] == profile_id
        assert evaluation['question_set_hash'] == baseline['question_set_hash']
        evaluations.append(evaluation)
    pending = _compare(client, project, baseline, evaluations[-1])
    assert pending.status_code == 200, pending.text
    assert pending.json()['items'][0]['input_path_status'] == 'PENDING_UNEXECUTED'
    assert pending.json()['items'][0]['fair_fixed_input'] is False
    assert calls == []

    receipts = []
    result_ids = []
    for evaluation in evaluations:
        prefix = f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        readiness = client.get(prefix + '/readiness')
        assert readiness.status_code == 200, readiness.text
        assert readiness.json()['ready_to_confirm'] is True
        assert readiness.json()['frozen_profile'] == evaluation['profile']
        job_response = client.post(prefix + '/jobs', json={'confirmed':True})
        assert job_response.status_code == 202, job_response.text
        job = job_response.json()
        assert job['profile'] == evaluation['profile']
        client.app.state.reference_evaluation_jobs.process(job['job_id'])
        saved_job = client.get(f"/api/reference-evaluation-jobs/{job['job_id']}")
        assert saved_job.status_code == 200, saved_job.text
        assert saved_job.json()['state'] == 'COMPLETED'
        assert saved_job.json()['failed_count'] == 0
        replay = _execute(client, evaluation)
        assert replay['replayed'] is True
        result_ids.append(replay['result']['result_id'])
        assert replay['result']['result']['execution_profile']['profile_id'] == evaluation['profile']['profile_id']
        receipts.extend(replay['result']['result']['execution_receipts'])
        scorecard = client.get(prefix + '/scorecard')
        assert scorecard.status_code == 200, scorecard.text
        assert scorecard.json()['profile'] == evaluation['profile']
        assert scorecard.json()['summary']['CONTRACT_VALID'] == 1
        assert scorecard.json()['summary']['UNREVIEWED'] == 1

    assert len(calls) == 3
    assert len(set(result_ids)) == 3
    assert client.app.state.gateway.s == unchanged_settings
    assert len({item['request_hash'] for item in receipts}) == 3
    assert len({item['initial_evidence_manifest_sha256'] for item in receipts}) == 1
    assert len({item['profile_neutral_input_sha256'] for item in receipts}) == 1
    for candidate in evaluations[1:]:
        comparison = _compare(client, project, baseline, candidate)
        assert comparison.status_code == 200, comparison.text
        body = comparison.json()
        assert body['comparison_version'] == 'reference-evaluation-comparison-2'
        assert body['items'][0]['input_path_status'] == 'FULL_PATH_MATCHED'
        assert body['items'][0]['fair_fixed_input'] is True
        assert body['items'][0]['change'] == 'UNCHANGED'
        assert _compare(client, project, baseline, candidate).json() == body
    assert len(calls) == 3
    assert db.one("SELECT COUNT(*) AS n FROM model_calls WHERE state!='SETTLED'")['n'] == 0


def test_real_first_round_failure_can_be_compared_but_never_counted_as_valid(client, project):
    _, run, _, _ = _saved_run(client, project, 'REFERENCE_QA')
    calls = _channel(client, lambda payload, _: (
        'not-json' if payload['model'] == 'deepseek-v4-pro' else _provider_answer()))
    baseline = _create(client, project, run, 'FLASH_NONE')
    candidate = _create(client, project, run, 'PRO')
    _execute(client, baseline)
    failed = _execute(client, candidate)
    assert failed['result'] is None
    assert failed['failure']['execution_receipt']['round'] == 1
    comparison = _compare(client, project, baseline, candidate)
    assert comparison.status_code == 200, comparison.text
    item = comparison.json()['items'][0]
    assert item['candidate']['kind'] == 'FAILURE'
    assert item['input_path_status'] == 'FULL_PATH_MATCHED'
    assert item['fair_fixed_input'] is True
    card = client.get(f"/api/reference-evaluations/{candidate['evaluation_id']}/scorecard")
    assert card.status_code == 200, card.text
    assert card.json()['summary']['CONTRACT_VALID'] == 0
    assert card.json()['summary']['CONTRACT_INVALID'] == 1
    assert _execute(client, candidate)['replayed'] is True
    assert len(calls) == 2


@pytest.mark.parametrize('variant,expected', [
    ('same_history', 'FULL_PATH_MATCHED'),
    ('different_history', 'DYNAMIC_PATH_DIVERGED'),
    ('second_round_failure', 'SAME_INITIAL_PATH_UNKNOWN_FAILURE_HISTORY'),
])
def test_real_multiround_paths_distinguish_history_and_missing_failure_chain(
        client, project, variant, expected):
    _, run, _ = _raw_run(client, project, [
        {'page':1, 'section':'Section 09 90 00',
         'text':'The coating requirement is listed in the finish schedule.'},
        {'page':2, 'section':'Section 09 91 00',
         'text':'The approved color is blue. Finish key PT9 applies.'},
    ])

    def decision(payload, content):
        pro = payload['model'] == 'deepseek-v4-pro'
        if content['round'] == 1:
            query = ('PT9 approved color' if pro and variant == 'different_history'
                     else 'Finish key PT9 blue')
            return {'status':'NEED_EVIDENCE', 'reason_code':'MISSING_SOURCE_TEXT',
                    'missing_facts':['approved coating color'],
                    'requests':[{'tool':'SEARCH_TEXT', 'query':query}],
                    'answer':_empty_answer()}
        if pro and variant == 'second_round_failure':
            return 'not-json'
        source = next(item for item in content['evidence']
                      if 'approved color is blue' in item['text'])
        return _provider_answer(source['evidence_ref'])

    calls = _channel(client, decision)
    baseline = _create(client, project, run, 'FLASH_NONE', 'What is required for coating?')
    candidate = _create(client, project, run, 'PRO', 'What is required for coating?')
    before = _execute(client, baseline)
    after = _execute(client, candidate)
    before_chain = before['result']['result']['execution_receipts']
    assert [item['round'] for item in before_chain] == [1, 2]
    if variant == 'second_round_failure':
        assert after['result'] is None
        assert after['failure']['execution_receipt']['round'] == 2
    else:
        after_chain = after['result']['result']['execution_receipts']
        assert [item['round'] for item in after_chain] == [1, 2]
        assert before_chain[0]['initial_evidence_manifest_sha256'] == after_chain[0]['initial_evidence_manifest_sha256']
        assert before_chain[0]['profile_neutral_input_sha256'] == after_chain[0]['profile_neutral_input_sha256']
        assert before_chain[1]['ordered_evidence_manifest_sha256'] == after_chain[1]['ordered_evidence_manifest_sha256']
        assert (before_chain[1]['profile_neutral_input_sha256'] == after_chain[1]['profile_neutral_input_sha256']) == (variant == 'same_history')
    comparison = _compare(client, project, baseline, candidate)
    assert comparison.status_code == 200, comparison.text
    item = comparison.json()['items'][0]
    assert item['input_path_status'] == expected
    assert item['fair_fixed_input'] is (variant == 'same_history')
    assert len(calls) == 4


def test_named_and_legacy_real_results_are_observational_not_fixed_input_comparison(client, project):
    _, run, _, _ = _saved_run(client, project, 'REFERENCE_QA')
    calls = _channel(client, lambda *_: _provider_answer())
    baseline = _create(client, project, run, None)
    candidate = _create(client, project, run, 'PRO')
    assert 'profile_id' not in baseline['profile']
    _execute(client, baseline)
    _execute(client, candidate)
    comparison = _compare(client, project, baseline, candidate)
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()['items'][0]['input_path_status'] == 'UNKNOWN_LEGACY'
    assert comparison.json()['items'][0]['fair_fixed_input'] is False
    assert len(calls) == 2


def test_comparison_rejects_rehashed_external_input_receipt_before_fairness_claim(client, project):
    db, run, _, _ = _saved_run(client, project, 'REFERENCE_QA')
    calls = _channel(client, lambda *_: _provider_answer())
    baseline = _create(client, project, run, 'FLASH_NONE')
    candidate = _create(client, project, run, 'PRO')
    _execute(client, baseline)
    result = _execute(client, candidate)['result']
    valid = _compare(client, project, baseline, candidate)
    assert valid.status_code == 200, valid.text
    assert valid.json()['items'][0]['fair_fixed_input'] is True
    raw = db.one('SELECT result_json FROM reference_results WHERE id=?', (result['result_id'],))
    altered = json.loads(raw['result_json'])
    altered['execution_receipts'][0]['profile_neutral_input_sha256'] = '0' * 64
    serialized = json.dumps(altered, ensure_ascii=False, separators=(',', ':'))
    db.execute('UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?', (
        serialized, hashlib.sha256(serialized.encode()).hexdigest(), result['result_id']))
    rejected = _compare(client, project, baseline, candidate)
    assert rejected.status_code == 409, rejected.text
    assert len(calls) == 2


@pytest.mark.parametrize('field', [
    'initial_evidence_manifest_sha256', 'profile_neutral_input_sha256', 'inference_mode',
])
def test_comparison_reauthenticates_real_failure_receipt(client, project, field):
    db, run, _, _ = _saved_run(client, project, 'REFERENCE_QA')
    calls = _channel(client, lambda payload, _: (
        'not-json' if payload['model'] == 'deepseek-v4-pro' else _provider_answer()))
    baseline = _create(client, project, run, 'FLASH_NONE')
    candidate = _create(client, project, run, 'PRO')
    _execute(client, baseline)
    failure = _execute(client, candidate)['failure']
    valid = _compare(client, project, baseline, candidate)
    assert valid.status_code == 200, valid.text
    assert valid.json()['items'][0]['fair_fixed_input'] is True
    receipt = failure['execution_receipt']
    receipt[field] = 'thinking-low' if field == 'inference_mode' else '0' * 64
    db.execute('UPDATE reference_evaluation_failures SET execution_receipt_json=? WHERE id=?',
               (json.dumps(receipt), failure['failure_id']))
    rejected = _compare(client, project, baseline, candidate)
    assert rejected.status_code == 409, rejected.text
    assert len(calls) == 2


def test_comparison_identity_includes_common_input_chain_even_with_same_outcomes(
        client, project, monkeypatch):
    # Isolated identity-projection check after real terminal authentication.
    # Modifying a persisted receipt is separately proven to fail above.
    _, run, _, _ = _saved_run(client, project, 'REFERENCE_QA')
    calls = _channel(client, lambda *_: _provider_answer())
    baseline = _create(client, project, run, 'FLASH_NONE')
    candidate = _create(client, project, run, 'PRO')
    _execute(client, baseline)
    result_id = _execute(client, candidate)['result']['result_id']
    original = _compare(client, project, baseline, candidate)
    assert original.status_code == 200, original.text
    store = client.app.state.reference_evaluations
    original_projection = store._comparison_item

    def changed_projection(item):
        value = original_projection(item)
        if value.get('result_id') == result_id:
            value['_receipt_chain'][0]['profile_neutral_input_sha256'] = '0' * 64
        return value

    monkeypatch.setattr(store, '_comparison_item', changed_projection)
    changed = _compare(client, project, baseline, candidate)
    assert changed.status_code == 200, changed.text
    before = original.json()
    after = changed.json()
    assert before['comparison_id'] != after['comparison_id']
    assert before['items'][0]['candidate']['outcome_hash'] == after['items'][0]['candidate']['outcome_hash']
    assert after['items'][0]['input_path_status'] == 'DYNAMIC_PATH_DIVERGED'
    assert after['items'][0]['fair_fixed_input'] is False
    assert len(calls) == 2
