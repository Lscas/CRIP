"""Offline adversarial gate for formal projection execution authentication.

It uses only existing synthetic PDF/SQLite/MockTransport fixtures and performs
no provider call, settings read, migration, or persistent write.
"""
from __future__ import annotations

import copy
import json

import pytest

from app.db import DomainError
from app import reference_projection_execution as execution_module
from app.reference_projection_decision import ProjectionDecisionError
from app.reference_projection_loop import ProjectProjectionLoop
from app.reference_text_profiles import profile
from tests.app.test_reference_projection_loop import QUESTION, _ask, _preview, _setup


def _module():
    return execution_module


def _raises_auth(module, db, uploads, run, route, record, label):
    try:
        module.authenticate_projection_execution(db, uploads, run, QUESTION, route, record)
    except (DomainError, ProjectionDecisionError):
        return
    except Exception as exc:  # Explicitly reject accidental TypeError/KeyError acceptance.
        pytest.fail(f'{label}: unexpected {type(exc).__name__}')
    pytest.fail(f'{label}: authentication accepted forged content')


@pytest.fixture
def execution(client, project, tmp_path):
    """A three-settled-call REVIEW_REQUIRED runtime output and candidate record."""
    def respond(content):
        from tests.app.test_reference_projection_loop import _decision
        return _decision(content, {1: 'bravo', 2: 'charlie'}.get(content['round']))
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, respond)
    route = profile('FLASH_NONE')
    try:
        proof = _preview(loop, run)
        runtime = _ask(loop, run, proof)
        candidate = _module()
        record = candidate.compile_projection_execution(run, QUESTION, route, runtime)
        yield candidate, db, client.app.state.uploads, run, route, runtime, record, gateway, loop, sent
    finally:
        gateway.close()


def test_compile_and_authenticate_reject_model_disabled(client, project, tmp_path):
    db, run, gateway, _loop, _sent = _setup(client, project, tmp_path, lambda content: {})
    try:
        loop = ProjectProjectionLoop(db, None, client.app.state.uploads)
        proof = loop.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        runtime = loop.ask(run, QUESTION, profile('FLASH_NONE'), preview_proof=proof)
        assert runtime['status'] == 'MODEL_DISABLED'
        with pytest.raises(DomainError):
            _module().compile_projection_execution(run, QUESTION, profile('FLASH_NONE'), runtime)
    finally:
        gateway.close()


def test_compiler_rejects_malformed_runtime_identity_and_shape(execution):
    candidate, _db, _uploads, run, route, runtime, _record, _gateway, _loop, _sent = execution
    for path, value in ((('status',), 'CANNOT_ANSWER'),
                        (('reason_code',), 'NO_NEW_EVIDENCE'),
                        (('preview_proof', 'run_id'), 'RUN-forged')):
        forged = copy.deepcopy(runtime); cursor = forged
        for item in path[:-1]: cursor = cursor[item]
        cursor[path[-1]] = value
        with pytest.raises(DomainError):
            candidate.compile_projection_execution(run, QUESTION, route, forged)


def test_compiler_omits_nonpersistent_model_telemetry(execution):
    candidate, db, uploads, run, route, runtime, record, _gateway, _loop, _sent = execution
    forged = copy.deepcopy(runtime); forged['model_call_count'] = 999_999
    assert candidate.compile_projection_execution(run, QUESTION, route, forged) == record
    # These are well-typed runtime counters, so compile may copy them; the
    # local replay, rather than caller telemetry, is the authority for values.
    for counter, value in (('decision_count', 1), ('supplement_round_count', 0),
                           ('accepted_supplement_request_count', 0), ('projection_row_count', 1)):
        semantic_forge = copy.deepcopy(runtime); semantic_forge[counter] = value
        compiled = candidate.compile_projection_execution(run, QUESTION, route, semantic_forge)
        _raises_auth(candidate, db, uploads, run, route, compiled, f'counter:{counter}')
    for path, value in ((('execution_receipts', 0, 'request_hash'), '0' * 64),
                        (('review_packet', 'projection_input_sha256'), '0' * 64)):
        semantic_forge = copy.deepcopy(runtime); cursor = semantic_forge
        for item in path[:-1]: cursor = cursor[item]
        cursor[path[-1]] = value
        compiled = candidate.compile_projection_execution(run, QUESTION, route, semantic_forge)
        _raises_auth(candidate, db, uploads, run, route, compiled, f'hash:{path}')


def test_authentication_rejects_settled_ledger_tampering(execution):
    candidate, db, uploads, run, route, _runtime, record, _gateway, _loop, _sent = execution
    call_id = record['execution_receipts'][-1]['model_call_id']
    for column, value in (
            ('state', 'PENDING'),
            ('request_hash', '0' * 64),
            ('model', 'forged-model'),
            ('reference_input_commitment_sha256', '0' * 64),
            ('response', '{}')):
        original = db.one(f'SELECT {column} AS value FROM model_calls WHERE id=?', (call_id,))['value']
        db.execute(f'UPDATE model_calls SET {column}=? WHERE id=?', (value, call_id))
        _raises_auth(candidate, db, uploads, run, route, record, f'ledger:{column}')
        db.execute(f'UPDATE model_calls SET {column}=? WHERE id=?', (original, call_id))


def test_authentication_rejects_record_scope_proof_and_packet_tampering(execution):
    candidate, db, uploads, run, route, _runtime, record, _gateway, _loop, _sent = execution
    for field in ('source_scope', 'preview_proof', 'review_packet'):
        forged = copy.deepcopy(record)
        value = forged[field]
        if field == 'review_packet':
            value['verification']['object_condition_relations_verified'] = 0
        else:
            value['forged'] = True
        _raises_auth(candidate, db, uploads, run, route, forged, f'record:{field}')


def test_actual_no_new_append_zero_compiles_as_terminal_nonanswer(client, project, tmp_path):
    from tests.app.test_reference_projection_loop import _decision
    db, run, gateway, loop, _sent = _setup(client, project, tmp_path,
                                            lambda content: _decision(content, 'alpha'))
    route = profile('FLASH_NONE')
    try:
        runtime = _ask(loop, run, _preview(loop, run))
        assert runtime['status'] == 'CANNOT_ANSWER'
        assert runtime['reason_code'] == 'NO_NEW_EVIDENCE'
        assert runtime['accepted_supplement_request_count'] == 1
        record = _module().compile_projection_execution(run, QUESTION, route, runtime)
        assert record['status'] == 'CANNOT_ANSWER'
    finally:
        gateway.close()


def _without_cached(value):
    if isinstance(value, dict):
        return {key: _without_cached(child) for key, child in value.items() if key != 'cached'}
    if isinstance(value, list):
        return [_without_cached(child) for child in value]
    return value


def test_fresh_and_replay_keep_raw_cached_observation_but_same_semantic_record(execution):
    candidate, db, uploads, run, route, runtime, fresh, _gateway, loop, _sent = execution
    replay_runtime = _ask(loop, run, runtime['preview_proof'])
    replay = candidate.compile_projection_execution(run, QUESTION, route, replay_runtime)
    assert [receipt['cached'] for receipt in fresh['execution_receipts']] == [False, False, False]
    assert [receipt['cached'] for receipt in replay['execution_receipts']] == [True, True, True]
    assert fresh != replay
    assert _without_cached(fresh) == _without_cached(replay)
    assert candidate.authenticate_projection_execution(db, uploads, run, QUESTION, route, fresh) == fresh
    assert candidate.authenticate_projection_execution(db, uploads, run, QUESTION, route, replay) == replay


def test_history_authentication_is_read_only_and_does_not_require_settings_or_http(execution, monkeypatch):
    candidate, db, uploads, run, route, _runtime, record, _gateway, _loop, _sent = execution
    monkeypatch.setattr(db, 'execute', lambda *args, **kwargs: pytest.fail('authentication wrote to DB'))
    assert candidate.authenticate_projection_execution(db, uploads, run, QUESTION, route, record) == record


def test_authentication_rejects_equality_lookalike_types(execution):
    candidate, db, uploads, run, route, _runtime, record, _gateway, _loop, _sent = execution
    for target, value in (('review_boolean', 0), ('binding_page_number', True)):
        forged = copy.deepcopy(record)
        if target == 'review_boolean':
            forged['review_packet']['verification']['object_condition_relations_verified'] = value
        else:
            forged['review_packet']['source_bindings'][0]['page_number'] = value
        _raises_auth(candidate, db, uploads, run, route, forged, f'type:{target}')


def test_failure_authentication_rejects_raw_diagnostic_and_boolean_counter(client, project, tmp_path):
    from app.gateway import InvalidModelOutput
    from tests.app.test_reference_projection_loop import _decision

    def respond(content):
        return _decision(content, 'bravo') if content['round'] == 1 else {}

    db, run, gateway, loop, _sent = _setup(client, project, tmp_path, respond)
    route = profile('FLASH_NONE')
    try:
        with pytest.raises(InvalidModelOutput) as failed:
            _ask(loop, run, _preview(loop, run))
        terminal = failed.value.execution_receipt
        chain = failed.value.failure_execution
        candidate = _module()
        assert candidate.authenticate_projection_failure(
            db, client.app.state.uploads, run, QUESTION, route, terminal, chain)['receipt_scope'] == 'COMPLETE_CHAIN'

        forged_chain = copy.deepcopy(chain)
        forged_chain['supplement_round_count'] = True
        _raises_failure(candidate, db, client.app.state.uploads, run, route, terminal, forged_chain)

        stored = json.loads(db.one('SELECT error FROM model_calls WHERE id=?', (terminal['model_call_id'],))['error'])
        stored['semantic_detail'] = 'forged-safe-looking-diagnostic'
        db.execute('UPDATE model_calls SET error=? WHERE id=?', (json.dumps(stored), terminal['model_call_id']))
        _raises_failure(candidate, db, client.app.state.uploads, run, route, terminal, chain)
    finally:
        gateway.close()


def _raises_failure(module, db, uploads, run, route, terminal, chain):
    with pytest.raises(DomainError):
        module.authenticate_projection_failure(db, uploads, run, QUESTION, route, terminal, chain)
