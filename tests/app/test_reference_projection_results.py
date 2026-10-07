"""Projection-result persistence uses the one formal ReferenceResultStore only."""
from __future__ import annotations

import hashlib
import json

import pytest

from app.db import DomainError
from app.gateway import InvalidModelOutput
from app.reference_projection_loop import ProjectProjectionLoop
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V2
from app.reference_results import ReferenceResultStore, _projection_looking, require_legacy_result
from app.reference_text_profiles import profile
from tests.app.test_projection_loop2_gateway import _insufficient, _need, _setup
from tests.app.test_reference_results import _public_result, _saved_run
from tests.app.test_reference_v9_input_auth import QUESTION as V9_QUESTION, _real_result


def _route():
    return profile('FLASH_NONE')


def _review(envelope):
    return {
        'contract_version': envelope['contract_version'],
        'projection_input_sha256': envelope['projection_input_sha256'],
        'status': 'REVIEW_REQUIRED', 'reason_code': 'OBJECT_CONDITION_REVIEW_REQUIRED',
        'missing_facts': [], 'requests': [],
        'selections': [{'row_ref': envelope['projection_context']['rows'][-1]['row_ref'],
                        'part_refs': ['P1']}],
    }


def _call_rows(db):
    return db.all('''SELECT id,project_id,run_id,task_key,model,state,request_hash,actual_units,
                             response,error,reference_input_commitment_sha256
                      FROM model_calls ORDER BY id''')


def _output(loop, run):
    route = _route()
    proof = loop.preview(run, 'alpha?', route)['preview_proof']
    return route, loop.ask(run, 'alpha?', route, preview_proof=proof)


def _formal_store(client, project, tmp_path, responder, *, pages=None):
    db, run, gateway, _unused, sent = _setup(client, project, tmp_path, responder, pages=pages)
    loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V2)
    return db, run, gateway, loop, sent, ReferenceResultStore(db, client.app.state.uploads)


def test_require_legacy_result_rejects_projection_or_unknown_kind_without_reparsing_legacy_contract():
    legacy = {'result_kind': 'REFERENCE_QA_RESULT', 'result_json': '{"qa_version":"3"}'}
    assert require_legacy_result(legacy) == {'qa_version': '3'}
    for row in (
        {'result_kind': 'PROJECTION_LOOP_OUTCOME', 'result_json': '{}'},
        {'result_kind': 'UNKNOWN', 'result_json': '{}'},
        {'result_kind': 'REFERENCE_QA_RESULT', 'result_json': json.dumps({
            'projection_execution_version': 'reference-projection-execution-2'})},
    ):
        with pytest.raises(DomainError):
            require_legacy_result(row)


def test_projection_save_replays_raw_first_record_and_all_read_paths_authenticate(client, project, tmp_path):
    db, run, gateway, loop, sent, store = _formal_store(client, project, tmp_path, _review)
    try:
        route, fresh_output = _output(loop, run)
        fresh = store.save_projection(run, 'alpha?', fresh_output, route)
        raw = db.one('SELECT id,result_json,result_hash,result_kind FROM reference_results')
        replay_output = loop.ask(run, 'alpha?', route, preview_proof=loop.preview(run, 'alpha?', route)['preview_proof'])
        replay = store.save_projection(run, 'alpha?', replay_output, route)
        assert store.get(fresh['result_id']) == fresh
        assert store.list(project['id'])['items'][0]['result_kind'] == 'PROJECTION_LOOP_OUTCOME'
        assert store.export(project['id'])['results'][0]['review_history'] == []
        assert store.history(fresh['result_id']) == []
        with pytest.raises(DomainError, match='cannot be reviewed'):
            store.review(fresh['result_id'], 'ACCEPTED', 0, 'synthetic')
    finally:
        gateway.close()
    assert fresh['result_id'] == replay['result_id'] == raw['id']
    assert raw['result_kind'] == 'PROJECTION_LOOP_OUTCOME'
    assert hashlib.sha256(raw['result_json'].encode('utf-8')).hexdigest() == raw['result_hash']
    assert len(sent) == 1


@pytest.mark.parametrize(('responder', 'pages', 'reason', 'rounds'), [
    (lambda envelope: _need(envelope, 'alpha'), None, 'NO_NEW_EVIDENCE', 1),
    (lambda envelope: _need(envelope, 'bravo') if envelope['round'] == 1 else _need(envelope, 'alpha'),
     None, 'NO_NEW_EVIDENCE', 2),
    (lambda envelope: (_need(envelope, 'bravo') if envelope['round'] == 1
                       else _need(envelope, 'charlie') if envelope['round'] == 2
                       else _insufficient(envelope)),
     ['alpha initial note', 'bravo supplement', 'charlie supplement'], 'INSUFFICIENT_EVIDENCE', 3),
])
def test_projection_terminal_variants_replay_without_new_http(
        client, project, tmp_path, responder, pages, reason, rounds):
    db, run, gateway, loop, sent, store = _formal_store(client, project, tmp_path, responder, pages=pages)
    try:
        route, output = _output(loop, run)
        saved = store.save_projection(run, 'alpha?', output, route)
        calls_before = _call_rows(db)
        assert store.get(saved['result_id']) == saved
        assert store.list(project['id'])['items'][0]['result_kind'] == 'PROJECTION_LOOP_OUTCOME'
        assert store.export(project['id'])['results'][0]['result']['reason_code'] == reason
    finally:
        gateway.close()
    assert saved['result']['decision_count'] == rounds
    assert _call_rows(db) == calls_before and len(sent) == rounds


def test_failure_execution_model_disabled_and_cross_profile_cannot_save_projection(client, project, tmp_path):
    db, run, gateway, loop, _sent, store = _formal_store(client, project, tmp_path, lambda _envelope: {})
    try:
        route = _route(); proof = loop.preview(run, 'alpha?', route)['preview_proof']
        with pytest.raises(InvalidModelOutput) as failure:
            loop.ask(run, 'alpha?', route, preview_proof=proof)
        before = _call_rows(db)
        with pytest.raises(DomainError):
            store.save_projection(run, 'alpha?', failure.value.failure_execution, route)
        with pytest.raises(DomainError):
            store.save_projection(run, 'alpha?', {'status': 'MODEL_DISABLED'}, route)
        other_db, other_run, other_gateway, other_loop, _other_sent, other_store = _formal_store(
            client, project, tmp_path, _review)
        try:
            original_route, output = _output(other_loop, other_run)
            with pytest.raises(DomainError):
                other_store.save_projection(other_run, 'alpha?', output, profile('FLASH_LOW'))
            before = _call_rows(other_db)
        finally:
            other_gateway.close()
    finally:
        gateway.close()
    assert _call_rows(db) == before


@pytest.mark.parametrize(('column', 'value'), [
    ('result_hash', '0' * 64), ('status', 'NEED_USER_INPUT'),
    ('provider', 'tampered-provider'), ('model', 'tampered-model'),
    ('snapshot_id', 'S-tampered'), ('question', 'beta?'),
    ('review_version', 1), ('id', 'QAR-not-a-hex-id'),
])
def test_projection_metadata_tampering_is_rejected_on_every_read_path(
        client, project, tmp_path, column, value):
    db, run, gateway, loop, _sent, store = _formal_store(client, project, tmp_path, _review)
    try:
        route, output = _output(loop, run); saved = store.save_projection(run, 'alpha?', output, route)
        target_id = value if column == 'id' else saved['result_id']; calls_before = _call_rows(db)
        db.execute(f'UPDATE reference_results SET {column}=? WHERE id=?', (value, saved['result_id']))
        for reader in (lambda: store.get(target_id), lambda: store.list(project['id']),
                       lambda: store.export(project['id'])):
            with pytest.raises(DomainError): reader()
    finally:
        gateway.close()
    assert _call_rows(db) == calls_before


def test_projection_source_ledger_and_citation_tampering_fail_closed_without_new_calls(client, project, tmp_path):
    db, run, gateway, loop, _sent, store = _formal_store(client, project, tmp_path, _review)
    try:
        route, output = _output(loop, run); saved = store.save_projection(run, 'alpha?', output, route)
        result_id = saved['result_id']; calls_before = _call_rows(db)
        document = db.one('SELECT * FROM documents WHERE project_id=? ORDER BY id LIMIT 1', (project['id'],))
        object_path = client.app.state.uploads.object_path(document); original_pdf = object_path.read_bytes()
        object_path.write_bytes(b'tampered synthetic PDF')
        for reader in (lambda: store.get(result_id), lambda: store.list(project['id']),
                       lambda: store.export(project['id'])):
            with pytest.raises(DomainError): reader()
        object_path.write_bytes(original_pdf)
        call_id = saved['result']['execution_receipts'][0]['model_call_id']
        response = db.one('SELECT response FROM model_calls WHERE id=?', (call_id,))['response']
        db.execute('UPDATE model_calls SET response=? WHERE id=?', ('{}', call_id))
        with pytest.raises(DomainError): store.get(result_id)
        db.execute('UPDATE model_calls SET response=? WHERE id=?', (response, call_id))
        db.execute('''INSERT INTO reference_result_citations(
                       result_id,ordinal,claim_index,citation_index,citation_type,evidence_id,region_id,
                       document_id,page_number,citation_json)
                      VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (result_id, 0, 0, 0, 'TEXT', 'SYNTHETIC', None, document['id'], 1, '{}'))
        with pytest.raises(DomainError): store.get(result_id)
    finally:
        gateway.close()
    assert _call_rows(db) == calls_before


def test_projection_metadata_event_ledger_source_and_kind_tampering_fail_closed(client, project, tmp_path):
    db, run, gateway, loop, sent, store = _formal_store(client, project, tmp_path, lambda envelope: _need(envelope, 'alpha'))
    try:
        route, output = _output(loop, run)
        saved = store.save_projection(run, 'alpha?', output, route)
        result_id = saved['result_id']
        calls_before = _call_rows(db); sent_before = len(sent)
        db.execute('''INSERT INTO reference_result_review_events
                      (id,result_id,actor,action,before_json,after_json,note,created_at)
                      VALUES(?,?,?,?,?,?,?,?)''', (
            'QAREVIEW-' + '2' * 32, result_id, 'synthetic', 'ACCEPTED',
            '{"status":"NOT_APPLICABLE","version":0,"event_id":null}',
            '{"status":"ACCEPTED","version":1,"event_id":"QAREVIEW-' + '2' * 32 + '"}',
            'tamper', '2026-10-06T00:00:00+00:00'))
        for operation in (
                lambda: store.get(result_id), lambda: store.list(project['id']),
                lambda: store.export(project['id']), lambda: store.history(result_id),
                lambda: store.review(result_id, 'ACCEPTED', 0, 'must not write')):
            with pytest.raises(DomainError):
                operation()
        assert _call_rows(db) == calls_before and len(sent) == sent_before
    finally:
        gateway.close()


def test_projection_kind_downgrade_and_payload_tampering_fail_every_consumer_without_writes(
        client, project, tmp_path):
    db, run, gateway, loop, sent, store = _formal_store(client, project, tmp_path,
                                                         lambda envelope: _need(envelope, 'alpha'))
    try:
        route, output = _output(loop, run); saved = store.save_projection(run, 'alpha?', output, route)
        result_id = saved['result_id']; calls_before = _call_rows(db); sent_before = len(sent)
        events_before = db.one('SELECT COUNT(*) AS n FROM reference_result_review_events')['n']
        db.execute("UPDATE reference_results SET result_kind='REFERENCE_QA_RESULT' WHERE id=?", (result_id,))
        for operation in (
                lambda: store.get(result_id), lambda: store.list(project['id']),
                lambda: store.export(project['id']), lambda: store.history(result_id),
                lambda: store.authenticate_saved_result(run, db.one(
                    'SELECT * FROM reference_results WHERE id=?', (result_id,)))):
            with pytest.raises(DomainError): operation()
        with pytest.raises(DomainError): store.review(result_id, 'ACCEPTED', 0, 'must not write')
        _other_db, other_run, other_gateway, _other_loop, _other_sent, _other_store = _formal_store(
            client, project, tmp_path, _review)
        try:
            with pytest.raises(DomainError): store.compare(project['id'], run['id'], other_run['id'])
        finally:
            other_gateway.close()
        db.execute("UPDATE reference_results SET result_kind='PROJECTION_LOOP_OUTCOME' WHERE id=?", (result_id,))
        tampered = json.loads(db.one('SELECT result_json FROM reference_results WHERE id=?', (result_id,))['result_json'])
        tampered['status'] = 'NEED_USER_INPUT'
        db.execute('UPDATE reference_results SET result_json=? WHERE id=?', (json.dumps(tampered), result_id))
        for operation in (lambda: store.get(result_id), lambda: store.list(project['id']),
                          lambda: store.export(project['id'])):
            with pytest.raises(DomainError): operation()
    finally:
        gateway.close()
    assert _call_rows(db) == calls_before and len(sent) == sent_before
    assert db.one('SELECT COUNT(*) AS n FROM reference_result_review_events')['n'] == events_before


@pytest.mark.parametrize('receipt_version', [
    'reference-model-input-receipt-4', 'reference-model-input-receipt-5',
    'reference-model-input-receipt-6',
])
def test_projection_receipt_identity_versions_cannot_pose_as_legacy(receipt_version):
    assert _projection_looking({'execution_receipts': [{'receipt_version': receipt_version}]}) is True


def test_real_named_v9_legacy_row_keeps_public_shape_and_projection_marker_downgrade_is_refused(
        client, project, monkeypatch):
    db, run, _document, calls, value = _real_result(client, project)
    store = ReferenceResultStore(db, client.app.state.uploads)
    saved = store.save(run, V9_QUESTION, value, 'deepseek', profile('FLASH_NONE')['text_model'])
    before = store.get(saved['result_id'])
    fixed = '2026-10-06T00:00:00+00:00'
    import app.reference_results as module
    monkeypatch.setattr(module, 'now', lambda: fixed)
    assert store.export(project['id'], run['id'])['results'] == [{**before, 'review_history': []}]
    row = db.one('SELECT * FROM reference_results WHERE id=?', (saved['result_id'],))
    assert store.authenticate_saved_result(run, row)[0]['question'] == V9_QUESTION
    tampered = json.loads(row['result_json']); tampered['selector_version'] = 'literal-page-selector-10'
    db.execute('UPDATE reference_results SET result_json=? WHERE id=?', (json.dumps(tampered), saved['result_id']))
    for operation in (
            lambda: store.get(saved['result_id']), lambda: store.list(project['id']),
            lambda: store.export(project['id']), lambda: store.history(saved['result_id']),
            lambda: store.authenticate_saved_result(run, db.one(
                'SELECT * FROM reference_results WHERE id=?', (saved['result_id'],)))):
        with pytest.raises(DomainError):
            operation()
    assert calls


def test_legacy_save_shape_is_unchanged(client, project):
    db, run, _document, _evidence = _saved_run(client, project, 'REFERENCE_QA')
    store = ReferenceResultStore(db, client.app.state.uploads)
    question = 'What is the approved color?'
    saved = store.save(run, question, _public_result(run, question), 'custom-0123456789abcdef', 'gpt-5.6')
    public = store.get(saved['result_id'])
    assert 'result_kind' not in public
    assert public['result_id'] == saved['result_id']
