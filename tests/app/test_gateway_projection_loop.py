"""Synthetic selector10 append-only Gateway loop coverage; no live provider."""
from __future__ import annotations

from copy import deepcopy
import json

import httpx
import pytest

from app.db import DomainError
from app.gateway import Gateway, InvalidModelOutput, ProviderPaused
from app.reference_projection_decision import CONTRACT_VERSION
from app.reference_projection_loop_receipt import LOOP_VERSION
from app.reference_projection_preview import projection_preview_proof
from app.reference_projection_stage import append_projection_stage, prepare_projection_stage
from app.reference_text_profiles import profile
from app.settings import Settings
from .test_reference_projection_input import _input


QUESTION = 'alpha'


def _setup(client, project):
    db, run, _documents, _selection, bundle = _input(client, project, [
        ('initial.pdf', ['alpha initial projected review note']),
        ('beta.pdf', ['BETAKEY projected note']),
        ('gamma.pdf', ['GAMMAKEY projected note']),
    ], question=QUESTION)
    stage = prepare_projection_stage(bundle, db, client.app.state.uploads)
    return db, run, stage


def _gateway(tmp_path, db, handler):
    settings = Settings(tmp_path, provider='deepseek', api_key='synthetic-key',
                        live_enabled=True, cheap_model='deepseek-flash', start_worker=False)
    return Gateway(settings, db, httpx.Client(transport=httpx.MockTransport(handler)))


def _need(stage, query):
    return {
        'contract_version': CONTRACT_VERSION,
        'projection_input_sha256': stage.projection_input_sha256,
        'status': 'NEED_EVIDENCE', 'reason_code': 'MISSING_SOURCE_TEXT',
        'missing_facts': [{'part_ref': 'P1', 'gap_code': 'SOURCE_TEXT'}],
        'requests': [{'tool': 'SEARCH_TEXT', 'query': query}], 'selections': [],
    }


def _review(stage):
    return {
        'contract_version': CONTRACT_VERSION,
        'projection_input_sha256': stage.projection_input_sha256,
        'status': 'REVIEW_REQUIRED', 'reason_code': 'OBJECT_CONDITION_REVIEW_REQUIRED',
        'missing_facts': [], 'requests': [],
        'selections': [{'row_ref': stage.context['rows'][0]['row_ref'], 'part_refs': ['P1']}],
    }


def _call(gateway, run, stage, prior, proof, *, route=None):
    return gateway._projection_loop_decision_v10(
        run, QUESTION, stage, route or profile('FLASH_NONE'), prior_chain=prior, preview_proof=proof)


def test_three_round_append_only_chain_settles_and_replays_without_http(tmp_path, client, project):
    db, run, stage0 = _setup(client, project)
    proof = projection_preview_proof(run, stage0, profile('FLASH_NONE'))
    calls = []
    stages = [stage0]

    def handler(request):
        payload = json.loads(request.content); calls.append(payload)
        current = stages[len(calls) - 1]
        data = (_need(current, 'BETAKEY') if len(calls) == 1 else
                _need(current, 'GAMMAKEY') if len(calls) == 2 else _review(current))
        return httpx.Response(200, json={'id': f'loop-{len(calls)}', 'choices': [{
            'finish_reason': 'stop', 'message': {'content': json.dumps(data)}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})

    gateway = _gateway(tmp_path, db, handler)
    try:
        first = _call(gateway, run, stage0, [], proof)
        stage1 = append_projection_stage(stage0, first.data['requests'], db, client.app.state.uploads)
        stages.append(stage1)
        prior1 = [{'receipt': first.execution_receipt, 'decision': first.data}]
        second = _call(gateway, run, stage1, prior1, proof)
        stage2 = append_projection_stage(stage1, second.data['requests'], db, client.app.state.uploads)
        stages.append(stage2)
        prior2 = prior1 + [{'receipt': second.execution_receipt, 'decision': second.data}]
        third = _call(gateway, run, stage2, prior2, proof)
        replay = _call(gateway, run, stage2, prior2, proof)
    finally:
        gateway.close()

    assert len(calls) == 3
    assert [result.execution_receipt['receipt_version'] for result in (first, second, third)] == [
        'reference-model-input-receipt-5'] * 3
    assert [result.execution_receipt['round'] for result in (first, second, third)] == [1, 2, 3]
    assert all(result.execution_receipt['loop_version'] == LOOP_VERSION
               for result in (first, second, third))
    assert stage1.added_page_count == stage2.added_page_count == 1
    assert replay.cached is True and replay.data == third.data
    assert len(calls) == 3


def test_forged_prior_response_or_no_new_supplement_is_rejected_before_http(tmp_path, client, project):
    db, run, stage0 = _setup(client, project)
    proof = projection_preview_proof(run, stage0, profile('FLASH_NONE'))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'loop-prior', 'choices': [{
            'finish_reason': 'stop', 'message': {'content': json.dumps(_need(stage0, 'BETAKEY'))}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})

    gateway = _gateway(tmp_path, db, handler)
    try:
        first = _call(gateway, run, stage0, [], proof)
        stage1 = append_projection_stage(stage0, first.data['requests'], db, client.app.state.uploads)
        forged = [{'receipt': first.execution_receipt, 'decision': deepcopy(first.data)}]
        forged[0]['decision']['requests'][0]['query'] = 'GAMMAKEY'
        with pytest.raises(DomainError):
            _call(gateway, run, stage1, forged, proof)

        no_new = deepcopy(first.data)
        no_new['requests'][0]['query'] = 'alpha initial'
        # The stage itself is deliberately deterministic and exposes a no-op;
        # a caller cannot turn that into a paid loop round because its response
        # no longer matches the settled ledger payload.
        with pytest.raises(DomainError):
            _call(gateway, run, stage1,
                  [{'receipt': first.execution_receipt, 'decision': no_new}], proof)
    finally:
        gateway.close()
    assert len(calls) == 1


def test_loop_contract_failure_is_terminal_and_tampered_stage_or_proof_never_calls_http(
        tmp_path, client, project):
    db, run, stage0 = _setup(client, project)
    proof = projection_preview_proof(run, stage0, profile('FLASH_NONE'))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'loop-invalid', 'choices': [{
            'finish_reason': 'stop', 'message': {'content': '{}'}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})

    gateway = _gateway(tmp_path, db, handler)
    try:
        with pytest.raises(InvalidModelOutput):
            _call(gateway, run, stage0, [], proof)
        with pytest.raises(InvalidModelOutput):
            _call(gateway, run, stage0, [], proof)
        bad_proof = {**proof, 'prompt_contract_hash': '0' * 64}
        with pytest.raises(DomainError):
            _call(gateway, run, stage0, [], bad_proof)
        with pytest.raises(DomainError):
            _call(gateway, run, object(), [], proof)
    finally:
        gateway.close()
    assert len(calls) == 1


@pytest.mark.parametrize('tamper', ['receipt', 'commitment', 'usage', 'task', 'prior_error', 'route', 'source'])
def test_prior_chain_ledger_and_source_tampering_is_rejected_before_another_http(
        tmp_path, client, project, tamper):
    db, run, stage0 = _setup(client, project)
    proof = projection_preview_proof(run, stage0, profile('FLASH_NONE'))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'loop-prior-tamper', 'choices': [{
            'finish_reason': 'stop', 'message': {'content': json.dumps(_need(stage0, 'BETAKEY'))}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})

    gateway = _gateway(tmp_path, db, handler)
    try:
        first = _call(gateway, run, stage0, [], proof)
        stage1 = append_projection_stage(stage0, first.data['requests'], db, client.app.state.uploads)
        prior = [{'receipt': deepcopy(first.execution_receipt), 'decision': deepcopy(first.data)}]
        if tamper == 'receipt':
            prior[0]['receipt']['request_hash'] = '0' * 64
        elif tamper == 'commitment':
            db.execute('UPDATE model_calls SET reference_input_commitment_sha256=? WHERE id=?',
                       ('0' * 64, first.request_id))
        elif tamper == 'usage':
            db.execute('UPDATE model_calls SET actual_units=NULL WHERE id=?', (first.request_id,))
        elif tamper == 'task':
            db.execute('UPDATE model_calls SET task_key=? WHERE id=?', ('wrong-task', first.request_id))
        elif tamper == 'prior_error':
            db.execute('UPDATE model_calls SET error=? WHERE id=?',
                       ('{"kind":"CONTRACT_ERROR","class":"PROJECT_PROJECTION_DECISION"}',
                        first.request_id))
        elif tamper == 'source':
            document = db.one('SELECT * FROM documents WHERE id=? AND project_id=?',
                              (stage1.projection_sources[-1]['document_id'], project['id']))
            client.app.state.uploads.object_path(document).write_bytes(b'changed-after-settlement')
        with pytest.raises(DomainError):
            _call(gateway, run, stage1, prior, proof,
                  route=profile('FLASH_LOW') if tamper == 'route' else None)
    finally:
        gateway.close()
    assert len(calls) == 1


def test_missing_usage_stays_pending_and_blocks_loop_recovery_without_new_http(tmp_path, client, project):
    db, run, stage0 = _setup(client, project)
    proof = projection_preview_proof(run, stage0, profile('FLASH_NONE'))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'loop-no-usage', 'choices': [{
            'finish_reason': 'stop', 'message': {'content': json.dumps(_need(stage0, 'BETAKEY'))}}]})

    gateway = _gateway(tmp_path, db, handler)
    try:
        with pytest.raises(ProviderPaused, match='omitted usage'):
            _call(gateway, run, stage0, [], proof)
        with pytest.raises(ProviderPaused, match='unresolved model call'):
            _call(gateway, run, stage0, [], proof)
    finally:
        gateway.close()
    assert len(calls) == 1


@pytest.mark.parametrize('column,value', [
    ('model', 'different-model'),
    ('state', 'SETTLED_ERROR'),
    ('error', '{"kind":"CONTRACT_ERROR","class":"PROJECT_PROJECTION_DECISION"}'),
    ('project_id', 'other-project'),
])
def test_recovered_success_rechecks_ledger_terminal_shape_before_cached_return(
        tmp_path, client, project, column, value):
    db, run, stage0 = _setup(client, project)
    proof = projection_preview_proof(run, stage0, profile('FLASH_NONE'))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'loop-success', 'choices': [{
            'finish_reason': 'stop', 'message': {'content': json.dumps(_review(stage0))}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})

    gateway = _gateway(tmp_path, db, handler)
    try:
        first = _call(gateway, run, stage0, [], proof)
        if column == 'project_id':
            value = client.post('/api/projects', json={'name': 'Other synthetic project'}).json()['id']
        db.execute(f'UPDATE model_calls SET {column}=? WHERE id=?', (value, first.request_id))
        with pytest.raises(DomainError):
            _call(gateway, run, stage0, [], proof)
    finally:
        gateway.close()
    assert len(calls) == 1


@pytest.mark.parametrize('column,value', [
    ('state', 'SETTLED'),
    ('error', '{"kind":"CONTRACT_ERROR","class":"OTHER"}'),
])
def test_recovered_contract_error_requires_safe_terminal_shape_before_replay(
        tmp_path, client, project, column, value):
    db, run, stage0 = _setup(client, project)
    proof = projection_preview_proof(run, stage0, profile('FLASH_NONE'))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'id': 'loop-contract-error', 'choices': [{
            'finish_reason': 'stop', 'message': {'content': '{}'}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})

    gateway = _gateway(tmp_path, db, handler)
    try:
        with pytest.raises(InvalidModelOutput) as first:
            _call(gateway, run, stage0, [], proof)
        call_id = first.value.execution_receipt['model_call_id']
        db.execute(f'UPDATE model_calls SET {column}=? WHERE id=?', (value, call_id))
        with pytest.raises(DomainError):
            _call(gateway, run, stage0, [], proof)
    finally:
        gateway.close()
    assert len(calls) == 1
