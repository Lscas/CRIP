"""Offline source-free identity checks; full execution authentication stays separate."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from app.db import DomainError
from app.reference_projection_identity import verify_projection_result_identity
from app.reference_projection_loop import ProjectProjectionLoop
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V2
from app.reference_results import ReferenceResultStore
from app.reference_text_profiles import profile
from .test_projection_loop2_gateway import _need, _setup


QUESTION = 'alpha?'


def _review(envelope):
    return {'contract_version': envelope['contract_version'],
            'projection_input_sha256': envelope['projection_input_sha256'],
            'status': 'REVIEW_REQUIRED', 'reason_code': 'OBJECT_CONDITION_REVIEW_REQUIRED',
            'missing_facts': [], 'requests': [],
            'selections': [{'row_ref': envelope['projection_context']['rows'][-1]['row_ref'],
                            'part_refs': ['P1']}]}


def _saved(client, project, tmp_path, responder=_review, pages=None):
    db, run, gateway, _loop, sent = _setup(client, project, tmp_path, responder, pages=pages)
    loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V2)
    route = profile('FLASH_NONE')
    proof = loop.preview(run, QUESTION, route)['preview_proof']
    output = loop.ask(run, QUESTION, route, preview_proof=proof)
    saved = ReferenceResultStore(db, client.app.state.uploads).save_projection(run, QUESTION, output, route)
    row = db.one('SELECT * FROM reference_results WHERE id=?', (saved['result_id'],))
    return db, run, gateway, sent, saved, row


def _record(row):
    return json.loads(row['result_json'])


def _write(db, row, record):
    from app.reference_projection_identity import _KEY_DOMAIN, _hash, _semantic
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    route = record['execution_profile']
    key = _hash([_KEY_DOMAIN, row['run_id'], row['snapshot_id'], record['question_key'],
                 route['provider'], record['execution_receipts'][-1]['model'], _hash(_semantic(record))])
    db.execute('UPDATE reference_results SET result_json=?,result_hash=?,result_key=? WHERE id=?',
               (raw, hashlib.sha256(raw.encode()).hexdigest(), key, row['id']))
    return db.one('SELECT * FROM reference_results WHERE id=?', (row['id'],))


def test_identity_is_persisted_source_free_and_returns_only_machine_identity(client, project, tmp_path):
    db, _run, gateway, sent, saved, row = _saved(client, project, tmp_path)
    try:
        document = db.one('SELECT * FROM documents WHERE project_id=? ORDER BY id LIMIT 1', (project['id'],))
        client.app.state.uploads.object_path(document).unlink()
        value = verify_projection_result_identity(db, saved['result_id'])
    finally:
        gateway.close()
    assert value['result_id'] == saved['result_id'] and value['status'] == 'REVIEW_REQUIRED'
    assert not {'preview_proof', 'execution_receipts', 'source_scope', 'review_packet', 'source_verified'} & set(value)
    assert len(sent) == 1


@pytest.mark.parametrize('tamper', [
    'shadow_id', 'duplicate_receipt', 'raw_hash', 'packet_row', 'proof_neutral', 'response_contract',
    'response_extra', 'citation', 'review_event', 'non_object',
])
def test_identity_rejects_persisted_or_ledger_tampering_without_http(client, project, tmp_path, tamper):
    db, _run, gateway, sent, saved, row = _saved(client, project, tmp_path)
    try:
        if tamper == 'shadow_id':
            result_id = 'QAR-' + 'f' * 32
        elif tamper == 'raw_hash':
            db.execute('UPDATE reference_results SET result_hash=? WHERE id=?', ('0' * 64, row['id']))
            result_id = row['id']
        elif tamper == 'citation':
            document = db.one('SELECT id FROM documents WHERE project_id=? ORDER BY id LIMIT 1', (project['id'],))
            db.execute('''INSERT INTO reference_result_citations(result_id,ordinal,claim_index,citation_index,citation_type,evidence_id,region_id,document_id,page_number,citation_json)
                          VALUES(?,?,?,?,?,?,?,?,?,?)''', (row['id'], 0, 0, 0, 'TEXT', 'synthetic', None, document['id'], 1, '{}')); result_id = row['id']
        elif tamper == 'review_event':
            db.execute('''INSERT INTO reference_result_review_events(id,result_id,actor,action,before_json,after_json,note,created_at)
                          VALUES(?,?,?,?,?,?,?,?)''', ('QAREVIEW-' + '9' * 32, row['id'], 'synthetic', 'ACCEPTED', '{}', '{}', '', '2026-10-07T00:00:00+00:00')); result_id = row['id']
        elif tamper == 'response_contract' or tamper == 'response_extra':
            receipt = _record(row)['execution_receipts'][0]
            payload = json.loads(db.one('SELECT response FROM model_calls WHERE id=?', (receipt['model_call_id'],))['response'])
            if tamper == 'response_contract': payload['contract_version'] = 'project-projection-decision-1'
            else: payload['unexpected'] = 'x'
            db.execute('UPDATE model_calls SET response=? WHERE id=?', (json.dumps(payload, separators=(',', ':')), receipt['model_call_id'])); result_id = row['id']
        else:
            record = _record(row)
            if tamper == 'duplicate_receipt': record['execution_receipts'].append(deepcopy(record['execution_receipts'][0]))
            elif tamper == 'packet_row': record['review_packet']['source_bindings'][0]['row_ref'] = 'X999'
            elif tamper == 'proof_neutral': record['preview_proof']['first_profile_neutral_input_sha256'] = 'f' * 64
            else: record = []
            row = _write(db, row, record) if isinstance(record, dict) else row
            if tamper == 'non_object':
                raw = json.dumps(record); db.execute('UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?', (raw, hashlib.sha256(raw.encode()).hexdigest(), row['id']))
            result_id = row['id']
        before = len(sent)
        with pytest.raises(DomainError): verify_projection_result_identity(db, result_id)
        assert len(sent) == before
    finally:
        gateway.close()


def test_identity_accepts_three_decisions_two_supplements_and_no_new(client, project, tmp_path):
    def responder(envelope):
        if envelope['round'] < 3: return _need(envelope, 'bravo' if envelope['round'] == 1 else 'charlie')
        return _review(envelope)
    db, _run, gateway, sent, saved, _row = _saved(
        client, project, tmp_path, responder, ['alpha initial note', 'bravo supplemental note', 'charlie supplemental note'])
    try:
        value = verify_projection_result_identity(db, saved['result_id'])
    finally:
        gateway.close()
    assert len(sent) == 3 and value['supplement_round_count'] == 2 and value['decision_count'] == 3


def test_identity_binds_receipt_question_hash_and_wraps_structure_errors(client, project, tmp_path):
    db, _run, gateway, sent, saved, row = _saved(client, project, tmp_path)
    try:
        from app.reference_input_commitment import sha256 as commitment_sha256
        record = _record(row); receipt = record['execution_receipts'][0]
        receipt['question_hash'] = hashlib.sha256(b'different question').hexdigest()
        _write(db, row, record)
        db.execute('UPDATE model_calls SET reference_input_commitment_sha256=? WHERE id=?',
                   (commitment_sha256(record['execution_profile'], receipt), receipt['model_call_id']))
        with pytest.raises(DomainError) as question_error:
            verify_projection_result_identity(db, saved['result_id'])
        assert question_error.value.code == 409
    finally:
        gateway.close()
    assert len(sent) == 1


def test_identity_wraps_shared_structure_error_and_deduplicates_local_no_new_parts(client, project, tmp_path):
    def responder(envelope):
        return {'contract_version': envelope['contract_version'],
                'projection_input_sha256': envelope['projection_input_sha256'],
                'status': 'NEED_EVIDENCE', 'reason_code': 'MISSING_SOURCE_TEXT',
                'missing_facts': [{'part_ref': 'P1', 'gap_code': 'SOURCE_TEXT'},
                                  {'part_ref': 'P1', 'gap_code': 'OBJECT_CONDITION'}],
                'requests': [{'tool': 'SEARCH_TEXT', 'query': 'alpha'}], 'selections': []}
    db, _run, gateway, sent, saved, row = _saved(client, project, tmp_path, responder)
    try:
        value = verify_projection_result_identity(db, saved['result_id'])
        assert value['status'] == 'CANNOT_ANSWER'
        receipt = _record(row)['execution_receipts'][0]
        payload = json.loads(db.one('SELECT response FROM model_calls WHERE id=?', (receipt['model_call_id'],))['response'])
        payload['requests'] = []
        db.execute('UPDATE model_calls SET response=? WHERE id=?',
                   (json.dumps(payload, separators=(',', ':')), receipt['model_call_id']))
        with pytest.raises(DomainError) as semantic_error:
            verify_projection_result_identity(db, saved['result_id'])
        assert semantic_error.value.code == 409
    finally:
        gateway.close()
    assert len(sent) == 1


def _saved_three_decision_review(client, project, tmp_path):
    def responder(envelope):
        if envelope['round'] == 1:
            return _need(envelope, 'bravo')
        if envelope['round'] == 2:
            return _need(envelope, 'charlie')
        return _review(envelope)
    return _saved(client, project, tmp_path, responder,
                  ['alpha initial note', 'bravo supplemental note', 'charlie supplemental note'])


def _saved_terminal(client, project, tmp_path, responder):
    return _saved(client, project, tmp_path, responder)


@pytest.mark.parametrize('tamper', ['semantic_key', 'kind', 'ledger'])
def test_identity_rejects_additional_durable_tampering_without_http(client, project, tmp_path, tamper):
    db, _run, gateway, sent, saved, row = _saved(client, project, tmp_path)
    try:
        if tamper == 'semantic_key':
            db.execute('UPDATE reference_results SET result_key=? WHERE id=?', ('f' * 64, row['id']))
        elif tamper == 'kind':
            db.execute("UPDATE reference_results SET result_kind='REFERENCE_QA_RESULT',status='CANNOT_ANSWER' WHERE id=?",
                       (row['id'],))
        else:
            receipt = _record(row)['execution_receipts'][0]
            db.execute('UPDATE model_calls SET model=? WHERE id=?', ('tampered-model', receipt['model_call_id']))
        with pytest.raises(DomainError):
            verify_projection_result_identity(db, saved['result_id'])
    finally:
        gateway.close()
    assert len(sent) == 1


def test_identity_allows_cached_observation_change_with_new_raw_hash(client, project, tmp_path):
    db, _run, gateway, _sent, saved, row = _saved(client, project, tmp_path)
    try:
        before = verify_projection_result_identity(db, saved['result_id'])
        record = _record(row)
        record['execution_receipts'][0]['cached'] = not record['execution_receipts'][0]['cached']
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        raw_hash = hashlib.sha256(raw.encode()).hexdigest()
        db.execute('UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?',
                   (raw, raw_hash, row['id']))
        after = verify_projection_result_identity(db, saved['result_id'])
    finally:
        gateway.close()
    assert after['result_hash'] == raw_hash != before['result_hash']
    assert after['result_id'] == before['result_id']
    assert after['question_key'] == before['question_key']
    assert after['status'] == before['status']


@pytest.mark.parametrize('tamper', [
    'decision_count', 'receipt_round', 'proof_initial_context', 'scope_snapshot', 'review_packet',
])
def test_identity_rejects_incoherent_chain_with_recomputed_raw_and_key(client, project, tmp_path, tamper):
    db, _run, gateway, _sent, saved, row = _saved(client, project, tmp_path)
    try:
        record = _record(row)
        if tamper == 'decision_count':
            record['decision_count'] = 2
        elif tamper == 'receipt_round':
            record['execution_receipts'][0]['round'] = 2
        elif tamper == 'proof_initial_context':
            record['preview_proof']['initial_projection_context_sha256'] = '0' * 64
        elif tamper == 'scope_snapshot':
            record['source_scope']['snapshot_id'] = 'S-' + '0' * 32
        else:
            record['review_packet'] = None
        _write(db, row, record)
        with pytest.raises(DomainError):
            verify_projection_result_identity(db, saved['result_id'])
    finally:
        gateway.close()


@pytest.mark.parametrize('tamper', ['review_missing', 'selected_row', 'neutral_input'])
def test_identity_rejects_terminal_and_preview_cross_binding_tampering(client, project, tmp_path, tamper):
    db, _run, gateway, sent, saved, row = _saved(client, project, tmp_path)
    try:
        record = _record(row)
        if tamper == 'review_missing':
            record['missing'] = [{'part_ref': 'P1', 'gap_code': 'SOURCE_TEXT'}]
        elif tamper == 'selected_row':
            record['review_packet']['source_bindings'][0]['row_ref'] = 'X999'
        else:
            record['preview_proof']['first_profile_neutral_input_sha256'] = 'f' * 64
        _write(db, row, record)
        before = len(sent)
        with pytest.raises(DomainError):
            verify_projection_result_identity(db, saved['result_id'])
        assert len(sent) == before
    finally:
        gateway.close()


def test_identity_accepts_real_three_decision_two_supplement_review_chain(client, project, tmp_path):
    db, _run, gateway, sent, saved, _row = _saved_three_decision_review(client, project, tmp_path)
    try:
        value = verify_projection_result_identity(db, saved['result_id'])
    finally:
        gateway.close()
    assert len(sent) == 3
    assert value['decision_count'] == 3
    assert value['supplement_round_count'] == 2
    assert value['accepted_supplement_request_count'] == 2


def test_identity_accepts_loop2_no_new_when_final_append_discovers_conflict(
        client, project, tmp_path, monkeypatch):
    """The terminal NEED was made before the local append found the conflict."""
    import app.reference_projection_stage as stage_module
    actual_select = stage_module.select_pages

    def select(*args, **kwargs):
        selected = actual_select(*args, **kwargs)
        if args[2] == 'alpha' and kwargs.get('scope_question'):
            return replace(selected, source_conflicts=('SYNTHETIC_CONFLICT',))
        return selected

    monkeypatch.setattr(stage_module, 'select_pages', select)
    db, _run, gateway, sent, saved, _row = _saved(
        client, project, tmp_path,
        lambda envelope: _need(envelope, 'bravo' if envelope['round'] == 1 else 'alpha'))
    try:
        value = verify_projection_result_identity(db, saved['result_id'])
    finally:
        gateway.close()
    assert len(sent) == 2
    assert value['status'] == 'CANNOT_ANSWER'
    assert value['reason_code'] == 'NO_NEW_EVIDENCE'


@pytest.mark.parametrize('terminal', ['review', 'need_user', 'ordinary_cannot'])
def test_identity_rejects_conflicts_on_nonpermitted_terminal(client, project, tmp_path, terminal):
    if terminal == 'review':
        responder = _review
    elif terminal == 'need_user':
        responder = lambda envelope: {
            'contract_version': envelope['contract_version'],
            'projection_input_sha256': envelope['projection_input_sha256'],
            'status': 'NEED_USER_INPUT', 'reason_code': 'MISSING_PROJECT_FILE',
            'missing_facts': [{'part_ref': 'P1', 'gap_code': 'PROJECT_FILE'}],
            'requests': [], 'selections': [],
        }
    else:
        responder = lambda envelope: {
            'contract_version': envelope['contract_version'],
            'projection_input_sha256': envelope['projection_input_sha256'],
            'status': 'CANNOT_ANSWER', 'reason_code': 'UNSUPPORTED_TASK',
            'missing_facts': [{'part_ref': 'P1', 'gap_code': 'UNSUPPORTED_TASK'}],
            'requests': [], 'selections': [],
        }
    db, _run, gateway, sent, saved, row = _saved_terminal(client, project, tmp_path, responder)
    try:
        record = _record(row)
        record['source_scope']['conflicts'] = ['SYNTHETIC_CONFLICT']
        _write(db, row, record)
        with pytest.raises(DomainError):
            verify_projection_result_identity(db, saved['result_id'])
    finally:
        gateway.close()
    assert len(sent) == 1


def test_identity_accepts_real_need_user_input_terminal(client, project, tmp_path):
    def responder(envelope):
        return {'contract_version': envelope['contract_version'],
                'projection_input_sha256': envelope['projection_input_sha256'],
                'status': 'NEED_USER_INPUT', 'reason_code': 'MISSING_PROJECT_FILE',
                'missing_facts': [{'part_ref': 'P1', 'gap_code': 'PROJECT_FILE'}],
                'requests': [], 'selections': []}
    db, _run, gateway, sent, saved, _row = _saved_terminal(client, project, tmp_path, responder)
    try:
        value = verify_projection_result_identity(db, saved['result_id'])
    finally:
        gateway.close()
    assert len(sent) == 1 and value['status'] == 'NEED_USER_INPUT'


@pytest.mark.parametrize('value', [[], None, True, 'not-a-record', 7])
def test_identity_rejects_canonical_non_object_result_json_without_side_effects(client, project, tmp_path, value):
    db, _run, gateway, sent, saved, row = _saved(client, project, tmp_path)
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?',
                   (raw, hashlib.sha256(raw.encode()).hexdigest(), row['id']))
        before = db.one('SELECT result_json,result_hash,result_key FROM reference_results WHERE id=?', (row['id'],))
        calls_before = db.one('SELECT COUNT(*) AS n FROM model_calls')['n']
        with pytest.raises(DomainError):
            verify_projection_result_identity(db, saved['result_id'])
        assert db.one('SELECT result_json,result_hash,result_key FROM reference_results WHERE id=?', (row['id'],)) == before
        assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == calls_before
    finally:
        gateway.close()
    assert len(sent) == 1


@pytest.mark.parametrize('mutation', ['contract_version', 'projection_input_sha256', 'extra_key', 'contract_version_type'])
def test_identity_binds_settled_response_schema_and_identity(client, project, tmp_path, mutation):
    db, _run, gateway, sent, saved, row = _saved(client, project, tmp_path)
    try:
        receipt = _record(row)['execution_receipts'][0]
        payload = json.loads(db.one('SELECT response FROM model_calls WHERE id=?', (receipt['model_call_id'],))['response'])
        if mutation == 'contract_version':
            payload['contract_version'] = 'project-projection-decision-1'
        elif mutation == 'projection_input_sha256':
            payload['projection_input_sha256'] = 'f' * 64
        elif mutation == 'extra_key':
            payload['unexpected'] = 'not-permitted'
        else:
            payload['contract_version'] = 7
        db.execute('UPDATE model_calls SET response=? WHERE id=?',
                   (json.dumps(payload, ensure_ascii=False, separators=(',', ':')), receipt['model_call_id']))
        with pytest.raises(DomainError):
            verify_projection_result_identity(db, saved['result_id'])
    finally:
        gateway.close()
    assert len(sent) == 1
