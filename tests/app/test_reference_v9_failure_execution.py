"""Offline safe complete-chain persistence for named-v9 evaluation failures."""
import json
import copy

import pytest
from fastapi.testclient import TestClient

from app.db import DomainError
from app.evidence_loop import ProjectEvidenceLoop
from app.gateway import InvalidModelOutput
from app.main import create_app
from app.page_selector import LAYOUT_BOUND_SELECTOR_VERSION
from app.reference_text_profiles import profile
from app.settings import Settings

from .test_reference_v9_evaluations import _channel, _create_v9, _execute, _v9_run
from .test_page_selector import _raw_run, _spatial_text


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(tmp_path, start_worker=False, reference_layout_enabled=True)),
                    headers={'X-CIRP-Client': 'browser'}) as current:
        yield current


def test_named_v9_terminal_failure_persists_and_replays_safe_complete_chain(client, project):
    db, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: 'not-json')
    evaluation = _create_v9(client, project, run)
    first = _execute(client, evaluation)
    execution = first['failure_execution']
    assert set(execution) == {'failure_execution_version', 'receipt_scope', 'execution_receipts',
                              'supplement_round_count', 'accepted_supplement_request_count'}
    assert execution['receipt_scope'] == 'COMPLETE_CHAIN'
    assert execution['supplement_round_count'] == 0
    assert execution['accepted_supplement_request_count'] == 0
    assert len(execution['execution_receipts']) == 1
    assert execution['execution_receipts'][-1] == first['failure']['execution_receipt']
    assert not {'question', 'source', 'output', 'reasoning', 'query'} & set(json.dumps(execution).lower().split())
    row = db.one('SELECT failure_execution_json FROM reference_evaluation_failures WHERE item_id=?',
                 (evaluation['items'][0]['item_id'],))
    assert json.loads(row['failure_execution_json']) == execution
    replay = _execute(client, evaluation)
    assert replay['replayed'] is True and replay['failure_execution'] == execution
    assert len(calls) == 1


def test_complete_chain_rejects_recursive_question_or_output_fields(client, project):
    _, run, _ = _v9_run(client, project)
    _channel(client, lambda *_: 'not-json')
    evaluation = _create_v9(client, project, run)
    first = _execute(client, evaluation)
    context = client.app.state.reference_evaluations.item_context(
        evaluation['evaluation_id'], evaluation['items'][0]['item_id'])
    for key in ('question', 'query', 'output', 'reasoning', 'raw_text'):
        malformed = copy.deepcopy(first['failure_execution'])
        malformed['execution_receipts'][0][key] = 'synthetic-private-text'
        with pytest.raises(DomainError):
            client.app.state.reference_evaluations._authenticate_failure_execution(
                context['evaluation'], context['item'], context['profile'],
                first['failure']['execution_receipt'], malformed)


def test_old_null_failure_execution_remains_legacy_shape(client, project):
    db, run, _ = _v9_run(client, project)
    store = client.app.state.reference_evaluations
    evaluation = store.create(
        project['id'], run['id'], 'Frozen legacy failure',
        ['What approved color applies to Finish key PT9?'],
        store.profile(client.app.state.gateway.s))
    item = evaluation['items'][0]
    db.execute('''INSERT INTO reference_evaluation_failures(
        id,evaluation_id,item_id,code,stage,detail,created_at,execution_receipt_json,failure_execution_json)
        VALUES('QAEFAIL-00000000000000000000000000000000',?,?, 'MODEL_OUTPUT_REJECTED','EXECUTION','old',datetime('now'),NULL,NULL)''',
        (evaluation['evaluation_id'], item['item_id']))
    failure = client.app.state.reference_evaluations.failure(item['item_id'])
    assert set(failure) == {'failure_id', 'code', 'stage', 'detail', 'validator_category',
                            'execution_receipt', 'created_at'}
    assert failure['execution_receipt'] is None
    assert client.app.state.reference_scorecards.scorecard(evaluation['evaluation_id'])['summary']['TERMINAL'] == 1
    candidate = store.create(
        project['id'], run['id'], 'Frozen legacy failure candidate',
        ['What approved color applies to Finish key PT9?'],
        store.profile(client.app.state.gateway.s))
    assert client.app.state.reference_evaluations.compare(
        project['id'], evaluation['evaluation_id'], candidate['evaluation_id'])['summary']['PENDING'] == 1
    db.execute("UPDATE reference_evaluation_failures SET failure_execution_json='{}' WHERE item_id=?",
               (item['item_id'],))
    before_adjudications = db.one('SELECT COUNT(*) AS n FROM reference_evaluation_adjudications')['n']
    before_events = db.one('SELECT COUNT(*) AS n FROM reference_evaluation_adjudication_events')['n']
    with pytest.raises(DomainError):
        client.app.state.reference_scorecards.scorecard(evaluation['evaluation_id'])
    with pytest.raises(DomainError):
        client.app.state.reference_evaluations.compare(
            project['id'], evaluation['evaluation_id'], candidate['evaluation_id'])
    with pytest.raises(DomainError):
        client.app.state.reference_scorecards.adjudicate(
            evaluation['evaluation_id'], item['item_id'], 'UNUSABLE', False, 0)
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluation_adjudications')['n'] == before_adjudications
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluation_adjudication_events')['n'] == before_events


def test_named_v9_null_failure_receipt_is_rejected_on_all_read_paths(client, project):
    db, run, _ = _v9_run(client, project)
    evaluation = _create_v9(client, project, run)
    item = evaluation['items'][0]
    db.execute('''INSERT INTO reference_evaluation_failures(
        id,evaluation_id,item_id,code,stage,detail,created_at,execution_receipt_json,failure_execution_json)
        VALUES('QAEFAIL-11111111111111111111111111111111',?,?, 'MODEL_OUTPUT_REJECTED','EXECUTION','old',datetime('now'),NULL,NULL)''',
        (evaluation['evaluation_id'], item['item_id']))
    candidate = _create_v9(client, project, run)
    before = db.one('SELECT COUNT(*) AS n FROM reference_evaluation_adjudications')['n']
    before_events = db.one('SELECT COUNT(*) AS n FROM reference_evaluation_adjudication_events')['n']
    with pytest.raises(DomainError):
        client.app.state.reference_scorecards.scorecard(evaluation['evaluation_id'])
    with pytest.raises(DomainError):
        client.app.state.reference_evaluations.compare(
            project['id'], evaluation['evaluation_id'], candidate['evaluation_id'])
    with pytest.raises(DomainError):
        client.app.state.reference_scorecards.adjudicate(
            evaluation['evaluation_id'], item['item_id'], 'UNUSABLE', False, 0)
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluation_adjudications')['n'] == before
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluation_adjudication_events')['n'] == before_events


def test_v8_failure_response_keeps_legacy_top_level_shape(client, project):
    _, run, _ = _v9_run(client, project)
    _channel(client, lambda *_: 'not-json')
    evaluation = client.app.state.reference_evaluations.create(
        project['id'], run['id'], 'Frozen v8 failure',
        ['What approved color applies to Finish key PT9?'], profile('FLASH_NONE'))
    response = _execute(client, evaluation)
    assert response['result'] is None and 'failure_execution' not in response
    assert 'execution_telemetry' not in response
    assert set(response['failure']) == {
        'failure_id', 'code', 'stage', 'detail', 'validator_category',
        'execution_receipt', 'created_at'}


def test_complete_chain_rejects_prior_request_with_no_new_evidence(client, project):
    _, run, _ = _chain_run(client, project, [])
    question = 'What initial information is available on Sheet A5.01?'
    _channel(client, lambda _, content: _need('Initial') if content['round'] == 1 else 'not-json')
    evaluation = _create_v9(client, project, run, question=question)
    loop = ProjectEvidenceLoop(client.app.state.db, client.app.state.gateway)
    selection, rows, conflicts, manifest = loop._prepare_initial(
        run, question, LAYOUT_BOUND_SELECTOR_VERSION)
    route = client.app.state.reference_evaluations.item_context(
        evaluation['evaluation_id'], evaluation['items'][0]['item_id'])['profile']
    first = client.app.state.gateway.evidence_decision_v3(
        run, question, rows, conflicts, [], 0,
        selector_version=LAYOUT_BOUND_SELECTOR_VERSION, route=route,
        initial_evidence_manifest_sha256=manifest)
    with pytest.raises(InvalidModelOutput) as invalid:
        client.app.state.gateway.evidence_decision_v3(
            run, question, rows, conflicts, first.data['requests'], 1,
            selector_version=LAYOUT_BOUND_SELECTOR_VERSION, route=route,
            initial_evidence_manifest_sha256=manifest)
    context = client.app.state.reference_evaluations.item_context(
        evaluation['evaluation_id'], evaluation['items'][0]['item_id'])
    complete = {
        'failure_execution_version': 'reference-failure-execution-1',
        'receipt_scope': 'COMPLETE_CHAIN',
        'execution_receipts': [first.execution_receipt, invalid.value.execution_receipt],
        'supplement_round_count': 1, 'accepted_supplement_request_count': 1,
    }
    with pytest.raises(DomainError, match='did not add source evidence'):
        client.app.state.reference_evaluations._authenticate_failure_execution(
            context['evaluation'], context['item'], context['profile'],
            invalid.value.execution_receipt, complete)


def _need(query):
    return {'status': 'NEED_EVIDENCE', 'reason_code': 'MISSING_SOURCE_TEXT',
            'missing_facts': ['Synthetic supplemental source is required.'],
            'requests': [{'tool': 'SEARCH_TEXT', 'query': query}],
            'answer': {'claims': [], 'calculations': [], 'coverage': []}}


def _chain_run(client, project, supplements):
    pages=[]
    for page, words in enumerate([('Initial', '511'), *supplements], 1):
        text, text_map = _spatial_text([(10, [(words[0], 10, 90), (words[1], 100, 150)]),
                                        (30, [('NOTE', 10, 44)])])
        text += ' RIGHT'
        text_map.append({'start': len(text) - 5, 'end': len(text), 'bbox': [300, 10, 340, 20]})
        pages.append({'page': page, 'sheet': f'A5.{page:02d}', 'text': text, 'text_map': text_map})
    return _raw_run(client, project, pages)


@pytest.mark.parametrize(('supplements', 'decisions', 'expected_rounds'), [
    ([], ['not-json'], 1),
    ([('SupplementOne', '593')], [_need('SupplementOne'), 'not-json'], 2),
    ([('SupplementOne', '593'), ('SupplementTwo', '697')],
     [_need('SupplementOne'), _need('SupplementTwo'), 'not-json'], 3),
])
def test_named_v9_failure_chain_tracks_fresh_cached_and_replay_calls(
        client, project, supplements, decisions, expected_rounds):
    _, run, _ = _chain_run(client, project, supplements)
    calls = _channel(client, lambda *_: decisions.pop(0))
    question = 'What initial information is available on Sheet A5.01?'
    first_evaluation = _create_v9(client, project, run, question=question)
    first = _execute(client, first_evaluation)
    execution = first['failure_execution']
    telemetry = first['execution_telemetry']
    assert len(execution['execution_receipts']) == expected_rounds
    assert execution['supplement_round_count'] == expected_rounds - 1
    assert execution['accepted_supplement_request_count'] == expected_rounds - 1
    assert telemetry == {'scope': 'CURRENT_EXECUTE', 'model_call_count': expected_rounds,
                         'decision_count': expected_rounds, 'cached_decision_count': 0}
    assert len(calls) == expected_rounds

    cached_evaluation = _create_v9(client, project, run, question=question)
    cached = _execute(client, cached_evaluation)
    assert cached['replayed'] is False
    assert cached['failure_execution']['execution_receipts'][-1]['cached'] is True
    assert cached['execution_telemetry'] == {
        'scope': 'CURRENT_EXECUTE', 'model_call_count': 0,
        'decision_count': expected_rounds, 'cached_decision_count': expected_rounds}
    assert len(calls) == expected_rounds

    replay = _execute(client, cached_evaluation)
    assert replay['replayed'] is True
    assert replay['execution_telemetry'] == {
        'scope': 'CURRENT_EXECUTE', 'model_call_count': 0,
        'decision_count': 0, 'cached_decision_count': 0}
    assert len(calls) == expected_rounds


def test_saved_chain_reauthenticates_prior_response_and_counter(client, project):
    db, run, _ = _chain_run(client, project, [('SupplementOne', '593')])
    _channel(client, lambda *_: _need('SupplementOne') if not db.one(
        "SELECT COUNT(*) AS n FROM model_calls WHERE state='SETTLED'")['n'] else 'not-json')
    evaluation = _create_v9(
        client, project, run, question='What initial information is available on Sheet A5.01?')
    first = _execute(client, evaluation)
    item_id = evaluation['items'][0]['item_id']
    prior_id = first['failure_execution']['execution_receipts'][0]['model_call_id']
    db.execute('UPDATE model_calls SET response=? WHERE id=?', (
        json.dumps(_need('Initial')), prior_id))
    failure_bytes = db.one('SELECT failure_execution_json FROM reference_evaluation_failures WHERE item_id=?',
                           (item_id,))['failure_execution_json']
    replay = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/execute")
    assert replay.status_code == 409
    with pytest.raises(DomainError):
        client.app.state.reference_evaluations.fail(
            evaluation['evaluation_id'], item_id, 'MODEL_OUTPUT_REJECTED')
    assert db.one('SELECT failure_execution_json FROM reference_evaluation_failures WHERE item_id=?',
                  (item_id,))['failure_execution_json'] == failure_bytes

    execution = first['failure_execution']
    execution['supplement_round_count'] = True
    context = client.app.state.reference_evaluations.item_context(evaluation['evaluation_id'], item_id)
    with pytest.raises(DomainError):
        client.app.state.reference_evaluations._authenticate_failure_execution(
            context['evaluation'], context['item'], context['profile'],
            first['failure']['execution_receipt'], execution)


@pytest.mark.parametrize(('candidate_query', 'expected_path'), [
    ('SupplementOne', 'FULL_PATH_MATCHED'),
    ('SupplementTwo', 'DYNAMIC_PATH_DIVERGED'),
])
def test_named_failure_comparison_uses_authenticated_complete_paths(
        client, project, candidate_query, expected_path):
    _, run, _ = _chain_run(client, project, [
        ('SupplementOne', '593'), ('SupplementTwo', '697')])

    def decision(payload, content):
        if content['round'] == 1:
            # FLASH_LOW makes a different but still schema-valid supplemental request.
            low = payload.get('thinking') == {'type': 'enabled'}
            return _need(candidate_query if low else 'SupplementOne')
        return 'not-json'

    _channel(client, decision)
    question = 'What initial information is available on Sheet A5.01?'
    baseline = _create_v9(client, project, run, 'FLASH_NONE', question=question)
    candidate = _create_v9(client, project, run, 'FLASH_LOW', question=question)
    _execute(client, baseline)
    _execute(client, candidate)
    comparison = client.app.state.reference_evaluations.compare(
        project['id'], baseline['evaluation_id'], candidate['evaluation_id'])
    item = comparison['items'][0]
    assert item['input_path_status'] == expected_path
    assert item['fair_fixed_input'] is (expected_path == 'FULL_PATH_MATCHED')
