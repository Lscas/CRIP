"""Saved projection follow-up proof: source-authenticated build, source-free replay."""
from __future__ import annotations

import json

import pytest

from app.db import DomainError, dumps, now, uid
from app.reference_projection_followup import (
    build_projection_followup_proof,
    validate_projection_followup_proof,
)
from app.reference_results import ReferenceResultStore
from app.reference_projection_loop import ProjectProjectionLoop
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V2
from app.reference_text_profiles import profile
from .test_projection_loop2_gateway import _need, _setup
from .test_reference_projection_identity import QUESTION, _review
from .conftest import upload


def _saved_supplement(client, project, tmp_path):
    """A real receipt6 loop: alpha starts, bravo is appended in round two."""
    def responder(envelope):
        return _need(envelope, 'bravo') if envelope['round'] == 1 else _review(envelope)
    db, run, gateway, _loop, sent = _setup(
        client, project, tmp_path, responder,
        entries=[('initial.pdf', ['alpha original material']),
                 ('supplement.pdf', ['bravo new supplemental material']),
                 ('unused.pdf', ['charlie unrelated material'])])
    try:
        loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V2)
        route = profile('FLASH_NONE')
        preview = loop.preview(run, QUESTION, route)['preview_proof']
        output = loop.ask(run, QUESTION, route, preview_proof=preview)
        saved = ReferenceResultStore(db, client.app.state.uploads).save_projection(run, QUESTION, output, route)
        result = db.one('SELECT * FROM reference_results WHERE id=?', (saved['result_id'],))
        supplement = db.one("SELECT * FROM documents WHERE name='supplement.pdf'")
        _record, stage = ReferenceResultStore(db, client.app.state.uploads)._authenticate_projection_row_stage(result)
        assert any(row.get('binding', {}).get('document_id') == supplement['id'] and row.get('text', '').strip()
                   for row in stage.context['rows']), stage.context['rows']
        assert any(source['document_id'] == supplement['id'] for receipt in _record['execution_receipts']
                   for source in receipt['projection_sources']), _record['execution_receipts']
        assert len(sent) == 2
        return db, run, result, supplement
    finally:
        gateway.close()


def _case(db, project, run, result):
    case_id = uid('QACASE'); stamp = now()
    db.execute('''INSERT INTO reference_cases
        (id,source_key,project_id,run_id,snapshot_id,question,question_key,result_id,evaluation_id,
         evaluation_item_id,source_status,status,assignee,resolution,version,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (case_id, 'source:' + case_id, project['id'], run['id'], run['snapshot_id'], QUESTION,
         result['question_key'], result['id'], None, None, 'ANSWERED', 'OPEN', '', '', 0, stamp, stamp))
    return db.one('SELECT * FROM reference_cases WHERE id=?', (case_id,))


def _linked(db, case, run, result, proof):
    followup_id = uid('QACASEFOLLOW'); stamp = now()
    db.execute('''INSERT INTO reference_case_followups
        (id,case_id,run_id,snapshot_id,result_id,result_status,proof_json,payload_hash,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)''',
        (followup_id, case['id'], run['id'], run['snapshot_id'], result['id'], result['status'],
         dumps(proof), 'f' * 64, stamp))
    db.execute('''INSERT INTO reference_case_events
        (id,case_id,actor,action,before_json,after_json,note,created_at)
        VALUES(?,?,?,?,?,?,?,?)''',
        (uid('QACASEEVENT'), case['id'], 'human', 'UPDATED', dumps({}),
         dumps({'followup_id': followup_id, 'proof_sha256': proof['proof_sha256']}), '', stamp))
    return db.one('SELECT * FROM reference_case_followups WHERE id=?', (followup_id,))


def test_projection_followup_builds_real_supplement_receipt6_and_validates_without_pdf(
        client, project, tmp_path):
    db, run, result, supplement = _saved_supplement(client, project, tmp_path)
    case = _case(db, project, run, result)
    proof = build_projection_followup_proof(
        db, client.app.state.uploads, case_row=case, run_row=run, result_row=result,
        supplemental_document_ids=[supplement['id']])
    document = proof['documents'][0]
    assert proof['proof_version'] == 'reference-case-projection-followup-proof-1'
    assert document['text_input_rounds'] == [2] and document['text_input_count'] == 1
    assert document['visual_input_count'] == document['citation_count'] == 0
    receipt = document['receipts'][0]
    assert receipt['projection_rows'] and receipt['projection_text_bytes'] == sum(
        row['row_text_bytes'] for row in receipt['projection_rows'])
    assert all('text' not in row for row in receipt['projection_rows'])
    followup = _linked(db, case, run, result, proof)
    client.app.state.uploads.object_path(supplement).unlink()
    with db.connect(True) as connection:
        assert validate_projection_followup_proof(db, connection, case_row=case,
                                                  followup_row=followup, result_row=result) == proof


def test_projection_followup_rejects_unsent_or_missing_pdf_at_build(client, project, tmp_path):
    db, run, result, supplement = _saved_supplement(client, project, tmp_path)
    case = _case(db, project, run, result)
    # A requested document must have a non-empty retained stage row, not merely
    # be a member of the run snapshot.
    late = upload(client, project['id'], 'late-unsent.pdf', b'synthetic, not in frozen run')
    with pytest.raises(DomainError, match='not sent as non-empty'):
        build_projection_followup_proof(db, client.app.state.uploads, case_row=case, run_row=run,
                                        result_row=result, supplemental_document_ids=[late['document_id']])
    client.app.state.uploads.object_path(supplement).unlink()
    with pytest.raises(DomainError, match='unavailable'):
        build_projection_followup_proof(db, client.app.state.uploads, case_row=case, run_row=run,
                                        result_row=result, supplemental_document_ids=[supplement['id']])


@pytest.mark.parametrize('tamper', ['proof', 'event', 'descriptor', 'receipt_membership', 'model_route'])
def test_projection_followup_sourcefree_validation_detects_tamper(client, project, tmp_path, tamper):
    db, run, result, supplement = _saved_supplement(client, project, tmp_path)
    case = _case(db, project, run, result)
    proof = build_projection_followup_proof(db, client.app.state.uploads, case_row=case, run_row=run,
                                            result_row=result, supplemental_document_ids=[supplement['id']])
    followup = _linked(db, case, run, result, proof)
    if tamper == 'proof':
        altered = dict(proof); altered['result_hash'] = '0' * 64
        db.execute('UPDATE reference_case_followups SET proof_json=? WHERE id=?', (dumps(altered), followup['id']))
    elif tamper in {'descriptor', 'receipt_membership', 'model_route'}:
        altered = json.loads(dumps(proof))
        if tamper == 'descriptor':
            altered['documents'][0]['receipts'][0]['projection_rows'][0]['row_text_bytes'] += 1
        if tamper == 'receipt_membership':
            altered['documents'][0]['receipts'][0]['call_id'] = 'CALL-' + '0' * 32
        elif tamper == 'model_route':
            altered['execution_route'][0]['model'] = 'tampered-model'
        altered['proof_sha256'] = __import__('hashlib').sha256(dumps({k: v for k, v in altered.items() if k != 'proof_sha256'}).encode()).hexdigest()
        db.execute('UPDATE reference_case_followups SET proof_json=? WHERE id=?', (dumps(altered), followup['id']))
        db.execute("UPDATE reference_case_events SET after_json=? WHERE case_id=? AND action='UPDATED'",
                   (dumps({'followup_id': followup['id'], 'proof_sha256': altered['proof_sha256']}), case['id']))
    else:
        db.execute("UPDATE reference_case_events SET after_json=? WHERE case_id=? AND action='UPDATED'",
                   (dumps({'followup_id': followup['id']}), case['id']))
    followup = db.one('SELECT * FROM reference_case_followups WHERE id=?', (followup['id'],))
    client.app.state.uploads.object_path(supplement).unlink()
    with db.connect(True) as connection, pytest.raises(DomainError) as error:
        validate_projection_followup_proof(db, connection, case_row=case, followup_row=followup, result_row=result)
    assert error.value.code == 409


@pytest.mark.parametrize('field,value', [
    ('text_input_count', True), ('text_input_count', 1.0),
    ('first_input_round', True), ('text_input_rounds', [True]),
])
def test_projection_followup_sourcefree_validation_rejects_rehashed_non_integer_summary(
        client, project, tmp_path, field, value):
    db, run, result, supplement = _saved_supplement(client, project, tmp_path)
    case = _case(db, project, run, result)
    proof = build_projection_followup_proof(db, client.app.state.uploads, case_row=case, run_row=run,
                                            result_row=result, supplemental_document_ids=[supplement['id']])
    followup = _linked(db, case, run, result, proof)
    altered = json.loads(dumps(proof)); altered['documents'][0][field] = value
    import hashlib
    altered['proof_sha256'] = hashlib.sha256(dumps({key: item for key, item in altered.items()
                                                    if key != 'proof_sha256'}).encode()).hexdigest()
    db.execute('UPDATE reference_case_followups SET proof_json=? WHERE id=?', (dumps(altered), followup['id']))
    db.execute("UPDATE reference_case_events SET after_json=? WHERE case_id=? AND action='UPDATED'",
               (dumps({'followup_id': followup['id'], 'proof_sha256': altered['proof_sha256']}), case['id']))
    followup = db.one('SELECT * FROM reference_case_followups WHERE id=?', (followup['id'],))
    client.app.state.uploads.object_path(supplement).unlink()
    with db.connect(True) as connection, pytest.raises(DomainError) as error:
        validate_projection_followup_proof(db, connection, case_row=case,
                                          followup_row=followup, result_row=result)
    assert error.value.code == 409


@pytest.mark.parametrize('key,value', [
    ('pdf_sha256', []), ('page_numbers', [{'page': 2}]),
    ('page_numbers', [True]), ('page_numbers', [2, 2]),
])
def test_projection_followup_sourcefree_validation_rejects_rehashed_malformed_source(
        client, project, tmp_path, key, value):
    db, run, result, supplement = _saved_supplement(client, project, tmp_path)
    case = _case(db, project, run, result)
    proof = build_projection_followup_proof(db, client.app.state.uploads, case_row=case, run_row=run,
                                            result_row=result, supplemental_document_ids=[supplement['id']])
    followup = _linked(db, case, run, result, proof)
    altered = json.loads(dumps(proof))
    altered['documents'][0]['receipts'][0]['projection_sources'][0][key] = value
    import hashlib
    altered['proof_sha256'] = hashlib.sha256(dumps({name: item for name, item in altered.items()
                                                    if name != 'proof_sha256'}).encode()).hexdigest()
    db.execute('UPDATE reference_case_followups SET proof_json=? WHERE id=?', (dumps(altered), followup['id']))
    db.execute("UPDATE reference_case_events SET after_json=? WHERE case_id=? AND action='UPDATED'",
               (dumps({'followup_id': followup['id'], 'proof_sha256': altered['proof_sha256']}), case['id']))
    followup = db.one('SELECT * FROM reference_case_followups WHERE id=?', (followup['id'],))
    client.app.state.uploads.object_path(supplement).unlink()
    with db.connect(True) as connection, pytest.raises(DomainError) as error:
        validate_projection_followup_proof(db, connection, case_row=case,
                                          followup_row=followup, result_row=result)
    assert error.value.code == 409
