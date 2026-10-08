"""Core candidate tests using existing synthetic PDF/MockTransport fixtures only."""
from __future__ import annotations

from copy import deepcopy

import httpx
import pytest

from app.db import DomainError
from app.gateway import InvalidModelOutput
from app import reference_projection_execution as candidate
from app.reference_projection_loop import ProjectProjectionLoop
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V2
from tests.app.test_reference_projection_loop import _ask, _decision, _preview, _setup


def _call_count(db):
    return db.one('SELECT COUNT(*) AS n FROM model_calls')['n']


def test_review_record_compiles_then_rebuilds_only_from_source_and_ledger(client, project, tmp_path):
    db, run, gateway, loop, _sent = _setup(client, project, tmp_path, lambda body: _decision(body))
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        record = candidate.compile_projection_execution(run, 'alpha?', route, output)
        authenticated = candidate.authenticate_projection_execution(
            db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()
    assert authenticated == record
    assert record['status'] == 'REVIEW_REQUIRED'
    assert record['answer'] == '' and record['claims'] == record['calculations'] == []
    assert 'decision_trace' not in record and record['provider_output_included'] is False


def test_no_new_record_rebuilds_local_terminal_without_another_provider_call(client, project, tmp_path):
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, lambda body: _decision(body, 'alpha'))
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        record = candidate.compile_projection_execution(run, 'alpha?', route, output)
        authenticated = candidate.authenticate_projection_execution(
            db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()
    assert authenticated['status'] == 'CANNOT_ANSWER'
    assert authenticated['reason_code'] == 'NO_NEW_EVIDENCE'
    assert len(sent) == _call_count(db) == 1


def test_compiler_is_not_authority_for_coherent_forged_record_and_writes_nothing(client, project, tmp_path):
    db, run, gateway, loop, _sent = _setup(client, project, tmp_path, lambda body: _decision(body))
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        forged_output = deepcopy(output)
        forged_output['projection_row_count'] += 1
        record = candidate.compile_projection_execution(run, 'alpha?', route, forged_output)
        before = _call_count(db)
        with pytest.raises(DomainError):
            candidate.authenticate_projection_execution(
                db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()
    assert _call_count(db) == before


def test_receipt_only_failure_chain_authenticates_without_provider_output(client, project, tmp_path):
    db, run, gateway, loop, _sent = _setup(client, project, tmp_path, lambda _body: {})
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        proof = _preview(loop, run)
        with pytest.raises(InvalidModelOutput) as raised:
            _ask(loop, run, proof)
        failure = raised.value.failure_execution
        value = candidate.authenticate_projection_failure(
            db, client.app.state.uploads, run, 'alpha?', route,
            raised.value.execution_receipt, failure)
    finally:
        gateway.close()
    assert value['receipt_scope'] == 'COMPLETE_CHAIN'
    assert value['execution_receipts'][-1]['model_call_id'] == raised.value.execution_receipt['model_call_id']


@pytest.mark.parametrize(('status', 'reason', 'gap'), [
    ('CANNOT_ANSWER', 'UNSUPPORTED_TASK', 'UNSUPPORTED_TASK'),
    ('NEED_USER_INPUT', 'MISSING_PROJECT_FILE', 'PROJECT_FILE'),
])
def test_direct_nonreview_terminal_rebuilds_without_review_packet(
        client, project, tmp_path, status, reason, gap):
    def responder(body):
        value = _decision(body)
        value.update(status=status, reason_code=reason, selections=[],
                     missing_facts=[{'part_ref': 'P1', 'gap_code': gap}])
        return value
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, responder)
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        record = candidate.compile_projection_execution(run, 'alpha?', route, output)
        value = candidate.authenticate_projection_execution(
            db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()
    assert value['status'] == status and value['review_packet'] is None
    assert len(sent) == _call_count(db) == 1


@pytest.mark.parametrize('supplements', [1, 2])
def test_one_or_two_authenticated_supplements_then_review_rebuilds(
        client, project, tmp_path, supplements):
    queries = {1: 'bravo', 2: 'charlie'}
    db, run, gateway, loop, sent = _setup(
        client, project, tmp_path,
        lambda body: _decision(body, queries.get(body['round']) if body['round'] <= supplements else None))
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        record = candidate.compile_projection_execution(run, 'alpha?', route, output)
        value = candidate.authenticate_projection_execution(
            db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()
    assert value['status'] == 'REVIEW_REQUIRED'
    assert value['supplement_round_count'] == supplements
    assert len(sent) == _call_count(db) == supplements + 1


@pytest.mark.parametrize('terminal_round', [2, 3])
def test_second_or_third_contract_failure_rebuilds_receipt_only_chain(
        client, project, tmp_path, terminal_round):
    def responder(body):
        return {} if body['round'] == terminal_round else _decision(
            body, {1: 'bravo', 2: 'charlie'}[body['round']])
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, responder)
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        with pytest.raises(InvalidModelOutput) as raised:
            _ask(loop, run, _preview(loop, run))
        value = candidate.authenticate_projection_failure(
            db, client.app.state.uploads, run, 'alpha?', route,
            raised.value.execution_receipt, raised.value.failure_execution)
    finally:
        gateway.close()
    assert len(value['execution_receipts']) == terminal_round
    assert value['supplement_round_count'] == terminal_round - 1
    assert len(sent) == _call_count(db) == terminal_round


def test_positive_first_supplement_then_no_new_is_authenticated_local_terminal(client, project, tmp_path):
    db, run, gateway, loop, sent = _setup(
        client, project, tmp_path,
        lambda body: _decision(body, 'bravo' if body['round'] == 1 else 'alpha'))
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        record = candidate.compile_projection_execution(run, 'alpha?', route, output)
        value = candidate.authenticate_projection_execution(
            db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()
    assert value['reason_code'] == 'NO_NEW_EVIDENCE'
    assert value['supplement_round_count'] == 2
    assert len(sent) == _call_count(db) == 2


def test_authentication_rejects_selected_pdf_drift_without_writing_or_http(client, project, tmp_path):
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, lambda body: _decision(body))
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        record = candidate.compile_projection_execution(run, 'alpha?', route, output)
        document = db.one('SELECT * FROM documents WHERE project_id=? ORDER BY id LIMIT 1', (project['id'],))
        client.app.state.uploads.object_path(document).write_bytes(b'synthetic changed selected PDF')
        before = _call_count(db)
        with pytest.raises(DomainError):
            candidate.authenticate_projection_execution(
                db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()
    assert _call_count(db) == before == len(sent)


def test_authenticator_never_constructs_settings_or_sends_http(client, project, tmp_path, monkeypatch):
    db, run, gateway, loop, _sent = _setup(client, project, tmp_path, lambda body: _decision(body))
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        record = candidate.compile_projection_execution(run, 'alpha?', route, output)
        before = _call_count(db)
        monkeypatch.setattr(httpx.Client, 'send', lambda *_args, **_kwargs: pytest.fail('candidate must not send HTTP'))
        from app.settings import Settings
        monkeypatch.setattr(Settings, '__init__', lambda *_args, **_kwargs: pytest.fail('candidate must not construct Settings'))
        original_connect = db.connect
        def read_only_connect(write=False):
            if write:
                pytest.fail('candidate must not open a write connection')
            return original_connect(write)
        monkeypatch.setattr(db, 'connect', read_only_connect)
        candidate.authenticate_projection_execution(db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()
    assert _call_count(db) == before


@pytest.mark.parametrize('field,value', [
    ('answer', 'forbidden answer'),
    ('claims', [{'claim': 'forbidden'}]),
    ('calculations', [{'result': 'forbidden'}]),
    ('execution_receipts', {'not': 'a list'}),
    ('missing', [{'part_ref': 'P1', 'gap_code': 'SOURCE_TEXT', 'raw_text': 'forbidden'}]),
    ('source_scope', {'snapshot_id': 'x', 'policy': 'RUN_SNAPSHOT_APPEND_ONLY', 'conflicts': [],
                      'source_text': 'forbidden'}),
    ('preview_proof', {'chain_of_thought': 'forbidden'}),
    ('review_packet', {'raw_text': 'forbidden'}),
    ('reason_code', {'raw_text': 'forbidden'}),
    ('answer_basis', {'chain_of_thought': 'forbidden'}),
])
def test_compiler_rejects_nonempty_answer_and_private_or_malformed_runtime_members(
        client, project, tmp_path, field, value):
    db, run, gateway, loop, _sent = _setup(client, project, tmp_path, lambda body: _decision(body))
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        output[field] = value
        with pytest.raises(DomainError):
            candidate.compile_projection_execution(run, 'alpha?', route, output)
    finally:
        gateway.close()


def _reordered(value):
    if isinstance(value, dict):
        return {key: _reordered(child) for key, child in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_reordered(child) for child in value]
    return value


def test_authentication_treats_json_key_order_as_nonsemantic_but_checks_db_run_state(
        client, project, tmp_path):
    db, run, gateway, loop, _sent = _setup(client, project, tmp_path, lambda body: _decision(body))
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        record = candidate.compile_projection_execution(run, 'alpha?', route, output)
        assert candidate.authenticate_projection_execution(
            db, client.app.state.uploads, run, 'alpha?', route, _reordered(record))
        db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run['id'],))
        with pytest.raises(DomainError):
            candidate.authenticate_projection_execution(
                db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()


def test_failure_authentication_returns_exact_detached_failure_shape(client, project, tmp_path):
    db, run, gateway, loop, _sent = _setup(client, project, tmp_path, lambda _body: {})
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        with pytest.raises(InvalidModelOutput) as raised:
            _ask(loop, run, _preview(loop, run))
        failure = raised.value.failure_execution
        value = candidate.authenticate_projection_failure(
            db, client.app.state.uploads, run, 'alpha?', route,
            raised.value.execution_receipt, failure)
    finally:
        gateway.close()
    assert value == failure and value is not failure
    value['execution_receipts'].clear()
    assert failure['execution_receipts']


def test_model_direct_no_new_cannot_be_relabelled_as_local_append_exhaustion(client, project, tmp_path):
    def responder(body):
        value = _decision(body)
        value.update(status='CANNOT_ANSWER', reason_code='NO_NEW_EVIDENCE', selections=[], requests=[],
                     missing_facts=[{'part_ref': 'P1', 'gap_code': 'LOCAL_RETRIEVAL_EXHAUSTED'}])
        return value
    db, run, gateway, loop, _sent = _setup(client, project, tmp_path, responder)
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        record = candidate.compile_projection_execution(run, 'alpha?', route, output)
        with pytest.raises(DomainError, match='no-new'):
            candidate.authenticate_projection_execution(
                db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()


def test_packet_direction_is_closed_but_real_direction_contract_compiles(client, project, tmp_path):
    db, run, gateway, loop, _sent = _setup(client, project, tmp_path, lambda body: _decision(body))
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    try:
        output = _ask(loop, run, _preview(loop, run))
        assert candidate.compile_projection_execution(run, 'alpha?', route, output)
        malformed = deepcopy(output)
        malformed['review_packet']['source_bindings'][0]['direction']['untrusted'] = 'extra'
        with pytest.raises(DomainError):
            candidate.compile_projection_execution(run, 'alpha?', route, malformed)
    finally:
        gateway.close()


def test_v2_three_decision_insufficient_evidence_compiles_and_authenticates(
        client, project, tmp_path):
    def responder(body):
        value = _decision(body, {1: 'bravo', 2: 'charlie'}.get(body['round']))
        value['contract_version'] = PROJECTION_PROTOCOL_V2.decision_contract_version
        if body['round'] == 3:
            value.update(status='CANNOT_ANSWER', reason_code='INSUFFICIENT_EVIDENCE',
                         selections=[], requests=[],
                         missing_facts=[{'part_ref': 'P1', 'gap_code': 'SOURCE_TEXT'}])
        return value
    db, run, gateway, _legacy_loop, sent = _setup(client, project, tmp_path, responder)
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V2)
    try:
        proof = loop.preview(run, 'alpha?', route)['preview_proof']
        output = loop.ask(run, 'alpha?', route, preview_proof=proof)
        record = candidate.compile_projection_execution(run, 'alpha?', route, output)
        value = candidate.authenticate_projection_execution(
            db, client.app.state.uploads, run, 'alpha?', route, record)
    finally:
        gateway.close()
    assert record['projection_execution_version'] == PROJECTION_PROTOCOL_V2.execution_version
    assert record['preview_proof']['proof_version'] == PROJECTION_PROTOCOL_V2.proof_version
    assert value['status'] == 'CANNOT_ANSWER'
    assert value['reason_code'] == 'INSUFFICIENT_EVIDENCE'
    assert value['answer_basis'] == 'PROJECTION_LOOP_TERMINAL'
    assert len(sent) == value['decision_count'] == 3


def test_v2_review_replay_and_cross_version_record_tamper(client, project, tmp_path):
    def responder(body):
        value = _decision(body, {1: 'bravo', 2: 'charlie'}.get(body['round']))
        value['contract_version'] = PROJECTION_PROTOCOL_V2.decision_contract_version
        return value
    db, run, gateway, _legacy_loop, sent = _setup(client, project, tmp_path, responder)
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V2)
    try:
        proof = loop.preview(run, 'alpha?', route)['preview_proof']
        fresh = candidate.compile_projection_execution(run, 'alpha?', route,
                                                       loop.ask(run, 'alpha?', route, preview_proof=proof))
        replay = candidate.compile_projection_execution(run, 'alpha?', route,
                                                        loop.ask(run, 'alpha?', route, preview_proof=proof))
        assert candidate.authenticate_projection_execution(
            db, client.app.state.uploads, run, 'alpha?', route, replay) == replay
        forged = deepcopy(fresh)
        forged['loop_version'] = 'project-projection-loop-1'
        with pytest.raises(DomainError):
            candidate.authenticate_projection_execution(
                db, client.app.state.uploads, run, 'alpha?', route, forged)
    finally:
        gateway.close()
    assert fresh['status'] == replay['status'] == 'REVIEW_REQUIRED'
    assert [item['cached'] for item in fresh['execution_receipts']] == [False, False, False]
    assert [item['cached'] for item in replay['execution_receipts']] == [True, True, True]
    assert len(sent) == 3


def test_v2_failure_and_input_commitment_tamper_are_read_only_rejected(client, project, tmp_path):
    def responder(body):
        if body['round'] == 2:
            return {}
        value = _decision(body, 'bravo')
        value['contract_version'] = PROJECTION_PROTOCOL_V2.decision_contract_version
        return value
    db, run, gateway, _legacy_loop, _sent = _setup(client, project, tmp_path, responder)
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V2)
    try:
        proof = loop.preview(run, 'alpha?', route)['preview_proof']
        with pytest.raises(InvalidModelOutput) as failed:
            loop.ask(run, 'alpha?', route, preview_proof=proof)
        terminal = failed.value.execution_receipt
        chain = failed.value.failure_execution
        assert candidate.authenticate_projection_failure(
            db, client.app.state.uploads, run, 'alpha?', route, terminal, chain) == chain
        previous = db.one(
            'SELECT reference_input_commitment_sha256 AS value FROM model_calls WHERE id=?',
            (terminal['model_call_id'],))['value']
        db.execute('UPDATE model_calls SET reference_input_commitment_sha256=? WHERE id=?',
                   ('0' * 64, terminal['model_call_id']))
        with pytest.raises(DomainError):
            candidate.authenticate_projection_failure(
                db, client.app.state.uploads, run, 'alpha?', route, terminal, chain)
        db.execute('UPDATE model_calls SET reference_input_commitment_sha256=? WHERE id=?',
                   (previous, terminal['model_call_id']))
    finally:
        gateway.close()


@pytest.mark.parametrize(('queries', 'expected_rounds'), [
    ({1: 'alpha'}, 1),
    ({1: 'bravo', 2: 'alpha'}, 2),
])
def test_v2_authenticated_local_no_new_replays_without_new_http(
        client, project, tmp_path, queries, expected_rounds):
    def responder(body):
        value = _decision(body, queries[body['round']])
        value['contract_version'] = PROJECTION_PROTOCOL_V2.decision_contract_version
        return value
    db, run, gateway, _legacy_loop, sent = _setup(client, project, tmp_path, responder)
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V2)
    try:
        proof = loop.preview(run, 'alpha?', route)['preview_proof']
        fresh_output = loop.ask(run, 'alpha?', route, preview_proof=proof)
        fresh = candidate.compile_projection_execution(run, 'alpha?', route, fresh_output)
        assert candidate.authenticate_projection_execution(
            db, client.app.state.uploads, run, 'alpha?', route, fresh) == fresh
        before = len(sent)
        replay_output = loop.ask(run, 'alpha?', route, preview_proof=proof)
        replay = candidate.compile_projection_execution(run, 'alpha?', route, replay_output)
        assert candidate.authenticate_projection_execution(
            db, client.app.state.uploads, run, 'alpha?', route, replay) == replay
    finally:
        gateway.close()
    assert len(sent) == before == expected_rounds
    for record in (fresh, replay):
        assert record['status'] == 'CANNOT_ANSWER'
        assert record['reason_code'] == 'NO_NEW_EVIDENCE'
        assert record['answer_basis'] == 'PROJECTION_LOCAL_RETRIEVAL_EXHAUSTED'
        assert record['supplement_round_count'] == expected_rounds
        assert record['accepted_supplement_request_count'] == expected_rounds
    assert [receipt['cached'] for receipt in fresh['execution_receipts']] == [False] * expected_rounds
    assert [receipt['cached'] for receipt in replay['execution_receipts']] == [True] * expected_rounds


def test_v2_failure_rejects_failure1_or_receipt5_cross_version_mix_without_writes(
        client, project, tmp_path):
    def responder(body):
        if body['round'] == 2:
            return {}
        value = _decision(body, 'bravo')
        value['contract_version'] = PROJECTION_PROTOCOL_V2.decision_contract_version
        return value
    db, run, gateway, _legacy_loop, _sent = _setup(client, project, tmp_path, responder)
    route = __import__('app.reference_text_profiles', fromlist=['profile']).profile('FLASH_NONE')
    loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V2)
    try:
        with pytest.raises(InvalidModelOutput) as failed:
            loop.ask(run, 'alpha?', route,
                     preview_proof=loop.preview(run, 'alpha?', route)['preview_proof'])
        terminal = failed.value.execution_receipt
        chain = failed.value.failure_execution
        before = _call_count(db)
        wrong_failure = deepcopy(chain)
        wrong_failure['failure_execution_version'] = 'projection-failure-execution-1'
        wrong_receipt = deepcopy(chain)
        wrong_receipt['execution_receipts'][0]['receipt_version'] = 'reference-model-input-receipt-5'
        for forged in (wrong_failure, wrong_receipt):
            with pytest.raises(DomainError):
                candidate.authenticate_projection_failure(
                    db, client.app.state.uploads, run, 'alpha?', route, terminal, forged)
    finally:
        gateway.close()
    assert _call_count(db) == before
