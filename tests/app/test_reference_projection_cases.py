"""Red contract for human cases over a saved V2 projection terminal.

This intentionally starts at the persisted result boundary: the source object
has gone away, while source review continues to require full authentication.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from app.db import dumps
from app.db import DomainError
from app.reference_results import ReferenceResultStore
from .test_reference_projection_identity import QUESTION, _saved
from .test_reference_cases import _followup_result, _result
from .test_reference_results import _public_result, _saved_run
from .test_reference_projection_case_followup_flow import _later_projection
from .test_reference_projection_input import _pdf
from .conftest import upload


def _machine_snapshot(db, result_id):
    return {
        'result': db.one('''SELECT result_json,result_hash,result_key,result_kind,status
                            FROM reference_results WHERE id=?''', (result_id,)),
        'ledger': db.all('SELECT * FROM model_calls ORDER BY id'),
        'citations': db.all('SELECT * FROM reference_result_citations WHERE result_id=? ORDER BY ordinal',
                            (result_id,)),
        'reviews': db.all('SELECT * FROM reference_result_review_events WHERE result_id=? ORDER BY created_at',
                          (result_id,)),
    }


def test_saved_projection_review_terminal_can_be_human_case_after_source_is_gone(
        client, project, tmp_path):
    """Desired red path: source-free identity is enough for human case work."""
    db, run, gateway, sent, saved, _row = _saved(client, project, tmp_path)
    try:
        document = db.one('SELECT * FROM documents WHERE project_id=? ORDER BY id LIMIT 1',
                          (project['id'],))
        client.app.state.uploads.object_path(document).unlink()
        before = _machine_snapshot(db, saved['result_id'])
        sent_before = deepcopy(sent)

        # The review surface is deliberately stricter than routine case work.
        source_view = client.get(f"/api/reference-results/{saved['result_id']}/projection-review")
        assert source_view.status_code == 409

        created = client.post(f"/api/projects/{project['id']}/reference-cases", json={
            'run_id': run['id'], 'question': QUESTION, 'result_id': saved['result_id'],
            'note': 'Needs a human field decision.',
        })
        assert created.status_code == 201, created.text
        case = created.json()
        case_id, version = case['case_id'], case['version']

        assert client.get(f'/api/reference-cases/{case_id}').status_code == 200
        listing = client.get(f"/api/projects/{project['id']}/reference-cases", params={'run_id': run['id']})
        assert listing.status_code == 200

        updated = client.post(f'/api/reference-cases/{case_id}/update', json={
            'expected_version': version, 'note': 'Assigned for field review.',
            'assignee': 'field-engineer', 'attachments': [document['id']],
        })
        assert updated.status_code == 200, updated.text
        resolved = client.post(f'/api/reference-cases/{case_id}/update', json={
            'expected_version': updated.json()['version'], 'status': 'RESOLVED',
            'resolution': 'Human reviewer recorded the required condition.',
        })
        assert resolved.status_code == 200, resolved.text
        reopened = client.post(f'/api/reference-cases/{case_id}/update', json={
            'expected_version': resolved.json()['version'], 'status': 'OPEN',
            'note': 'Human reviewer reopened the case.',
        })
        assert reopened.status_code == 200, reopened.text

        assert _machine_snapshot(db, saved['result_id']) == before
        assert sent == sent_before
    finally:
        gateway.close()


def test_projection_case_create_keeps_project_question_and_version_guards(client, project, tmp_path):
    """Small negative contract; detailed field/view shape follows the design decision."""
    db, run, gateway, _sent, saved, _row = _saved(client, project, tmp_path)
    try:
        wrong_question = client.post(f"/api/projects/{project['id']}/reference-cases", json={
            'run_id': run['id'], 'question': 'different?', 'result_id': saved['result_id'],
        })
        assert wrong_question.status_code == 409

        other = client.post('/api/projects', json={'name': 'Other projection case project'}).json()
        cross_project = client.post(f"/api/projects/{other['id']}/reference-cases", json={
            'run_id': run['id'], 'question': QUESTION, 'result_id': saved['result_id'],
        })
        assert cross_project.status_code == 404

        created = client.post(f"/api/projects/{project['id']}/reference-cases", json={
            'run_id': run['id'], 'question': QUESTION, 'result_id': saved['result_id'],
        })
        assert created.status_code == 201, created.text
        invalid_version = client.post(f"/api/reference-cases/{created.json()['case_id']}/update", json={
            'expected_version': 'not-an-integer', 'note': 'Must be rejected before mutation.',
        })
        assert invalid_version.status_code == 422
    finally:
        gateway.close()


def test_projection_case_source_key_and_kind_integrity_are_not_legacy_compatible(
        client, project, tmp_path):
    """A direct projection case has its own source-key domain and refuses drift."""
    db, run, gateway, _sent, saved, _row = _saved(client, project, tmp_path)
    try:
        created = client.post(f"/api/projects/{project['id']}/reference-cases", json={
            'run_id': run['id'], 'question': QUESTION, 'result_id': saved['result_id'],
        })
        assert created.status_code == 201, created.text
        question_key = hashlib.sha256(QUESTION.encode()).hexdigest()
        expected = hashlib.sha256(dumps([
            'reference-case-projection-source-1', project['id'], run['id'], run['snapshot_id'],
            question_key, saved['result_id'], saved['result_hash'],
        ]).encode()).hexdigest()
        stored = db.one('SELECT source_key FROM reference_cases WHERE id=?',
                        (created.json()['case_id'],))
        assert stored['source_key'] == expected

        # A legacy-kind downgrade must never make this projection source appear
        # authentic under the legacy case key.
        legacy = _public_result(run, QUESTION)
        legacy.update(status='CANNOT_ANSWER', answer='', claims=[], missing=['Synthetic legacy result.'])
        raw = json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute("UPDATE reference_results SET result_kind='REFERENCE_QA_RESULT',status='CANNOT_ANSWER',result_json=?,result_hash=? WHERE id=?",
                   (raw, hashlib.sha256(raw.encode()).hexdigest(), saved['result_id']))
        assert client.get(f"/api/reference-cases/{created.json()['case_id']}").status_code == 409
    finally:
        gateway.close()


@pytest.mark.parametrize('mutation', ['raw_hash', 'unknown_kind'])
def test_projection_case_creation_rejects_raw_or_kind_tampering(client, project, tmp_path, mutation):
    db, run, gateway, _sent, saved, _row = _saved(client, project, tmp_path)
    try:
        if mutation == 'raw_hash':
            db.execute('UPDATE reference_results SET result_hash=? WHERE id=?', ('0' * 64, saved['result_id']))
        else:
            row = db.one('SELECT result_json FROM reference_results WHERE id=?', (saved['result_id'],))
            record = json.loads(row['result_json'])
            record['result_kind'] = 'UNKNOWN_RESULT_KIND'
            raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?',
                       (raw, hashlib.sha256(raw.encode()).hexdigest(), saved['result_id']))
        response = client.post(f"/api/projects/{project['id']}/reference-cases", json={
            'run_id': run['id'], 'question': QUESTION, 'result_id': saved['result_id'],
        })
        assert response.status_code == 409
        assert db.one('SELECT COUNT(*) AS n FROM reference_cases')['n'] == 0
    finally:
        gateway.close()


def test_legacy_followup_auth_window_rejects_source_key_change_without_writes(client, project, tmp_path, monkeypatch):
    db, source_run, gateway, _sent, saved, _row = _saved(client, project, tmp_path,
        pages=['alpha? The approved color is blue.'])
    try:
        document = db.one('SELECT id,name FROM documents WHERE project_id=? ORDER BY id LIMIT 1', (project['id'],))
        cases = client.app.state.reference_cases
        case = cases.create(project['id'], source_run['id'], QUESTION, result_id=saved['result_id'],
                            attachments=[document['id']])
        run, result_id = _followup_result(db, client, project,
            {'document_id': document['id'], 'name': document['name']}, QUESTION)
        before = db.one('SELECT status,version FROM reference_cases WHERE id=?', (case['case_id'],))
        counts = db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'], db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']
        original = ReferenceResultStore.authenticate_saved_result
        def race(store, run_row, result_row):
            value = original(store, run_row, result_row)
            db.execute('UPDATE reference_cases SET source_key=? WHERE id=?', ('0' * 64, case['case_id']))
            return value
        monkeypatch.setattr(ReferenceResultStore, 'authenticate_saved_result', race)
        with pytest.raises(DomainError):
            cases.link_followup(case['case_id'], case['version'], run['id'], result_id, [document['id']], 'race')
        assert db.one('SELECT status,version FROM reference_cases WHERE id=?', (case['case_id'],)) == before
        assert (db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'], db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']) == counts
    finally:
        gateway.close()


def test_legacy_followup_auth_window_rejects_result_hash_change_without_writes(client, project, monkeypatch):
    db, source_run, document, _ = _saved_run(client, project, 'REFERENCE_QA')
    cases = client.app.state.reference_cases
    source = _result(client.app.state.reference_results, source_run, QUESTION, 'NEED_USER_INPUT')
    case = cases.create(project['id'], source_run['id'], QUESTION, result_id=source, attachments=[document['document_id']])
    run, result_id = _followup_result(db, client, project, document, QUESTION)
    before = db.one('SELECT status,version FROM reference_cases WHERE id=?', (case['case_id'],))
    counts = db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'], db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']
    original = ReferenceResultStore.authenticate_saved_result
    def race(store, run_row, result_row):
        value = original(store, run_row, result_row)
        db.execute('UPDATE reference_results SET result_hash=? WHERE id=?', ('0' * 64, result_id))
        return value
    monkeypatch.setattr(ReferenceResultStore, 'authenticate_saved_result', race)
    with pytest.raises(DomainError):
        cases.link_followup(case['case_id'], case['version'], run['id'], result_id, [document['document_id']], 'race')
    assert db.one('SELECT status,version FROM reference_cases WHERE id=?', (case['case_id'],)) == before
    assert (db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'], db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']) == counts


def test_legacy_followup_auth_window_rejects_settled_ledger_response_change(client, project, monkeypatch):
    db, source_run, document, _ = _saved_run(client, project, 'REFERENCE_QA')
    cases = client.app.state.reference_cases
    source = _result(client.app.state.reference_results, source_run, QUESTION, 'NEED_USER_INPUT')
    case = cases.create(project['id'], source_run['id'], QUESTION, result_id=source, attachments=[document['document_id']])
    run, result_id = _followup_result(db, client, project, document, QUESTION)
    before = db.one('SELECT status,version FROM reference_cases WHERE id=?', (case['case_id'],))
    counts = db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'], db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']
    original = ReferenceResultStore.authenticate_saved_result
    def race(store, run_row, result_row):
        value = original(store, run_row, result_row)
        call_id = value[1][0]['model_call_id']
        db.execute('UPDATE model_calls SET response=? WHERE id=?', ('{"changed":true}', call_id))
        return value
    monkeypatch.setattr(ReferenceResultStore, 'authenticate_saved_result', race)
    with pytest.raises(DomainError):
        cases.link_followup(case['case_id'], case['version'], run['id'], result_id, [document['document_id']], 'race')
    assert db.one('SELECT status,version FROM reference_cases WHERE id=?', (case['case_id'],)) == before
    assert (db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'], db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']) == counts


def test_legacy_followup_rejects_unbounded_persisted_receipts_without_writes(client, project):
    db, source_run, document, _ = _saved_run(client, project, 'REFERENCE_QA')
    cases = client.app.state.reference_cases
    source = _result(client.app.state.reference_results, source_run, QUESTION, 'NEED_USER_INPUT')
    case = cases.create(project['id'], source_run['id'], QUESTION, result_id=source, attachments=[document['document_id']])
    run, result_id = _followup_result(db, client, project, document, QUESTION)
    row = db.one('SELECT result_json FROM reference_results WHERE id=?', (result_id,)); value = json.loads(row['result_json'])
    value['execution_receipts'] *= 4
    raw = json.dumps(value, separators=(',', ':'), ensure_ascii=False)
    db.execute('UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?',
               (raw, hashlib.sha256(raw.encode()).hexdigest(), result_id))
    counts = db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'], db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']
    with pytest.raises(DomainError):
        cases.link_followup(case['case_id'], case['version'], run['id'], result_id, [document['document_id']], 'malformed')
    assert (db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'], db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']) == counts


def test_legacy_followup_auth_window_rejects_evidence_payload_change(client, project, monkeypatch):
    db, source_run, document, _ = _saved_run(client, project, 'REFERENCE_QA')
    cases = client.app.state.reference_cases; source = _result(client.app.state.reference_results, source_run, QUESTION, 'NEED_USER_INPUT')
    case = cases.create(project['id'], source_run['id'], QUESTION, result_id=source, attachments=[document['document_id']])
    run, result_id = _followup_result(db, client, project, document, QUESTION); before = db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n']
    original = ReferenceResultStore.authenticate_saved_result
    def race(store, run_row, result_row):
        value = original(store, run_row, result_row)
        db.execute("UPDATE evidence SET payload=? WHERE run_id=?", ('{}', run['id']))
        return value
    monkeypatch.setattr(ReferenceResultStore, 'authenticate_saved_result', race)
    with pytest.raises(DomainError):
        cases.link_followup(case['case_id'], case['version'], run['id'], result_id, [document['document_id']], 'race')
    assert db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'] == before


def test_projection_followup_auth_window_rejects_target_hash_change_without_writes(client, project, tmp_path, monkeypatch):
    """The real proof builder completes before the target is changed in the second-lock window."""
    db, source_run, _source_doc, _ = _saved_run(client, project, 'REFERENCE_QA')
    source = _result(client.app.state.reference_results, source_run, QUESTION, 'NEED_USER_INPUT')
    created = client.post(f'/api/projects/{project["id"]}/reference-cases', json={
        'run_id': source_run['id'], 'question': QUESTION, 'result_id': source,
    })
    assert created.status_code == 201
    initial = upload(client, project['id'], 'race-initial.pdf', _pdf('alpha original material'))
    supplement = upload(client, project['id'], 'race-supplement.pdf', _pdf('bravo new supplemental material'))
    attached = client.post(f'/api/reference-cases/{created.json()["case_id"]}/update', json={
        'expected_version': created.json()['version'], 'attachments': [supplement['document_id']],
    }).json()
    db, run, result = _later_projection(client, project, tmp_path, initial, supplement)
    case_id = attached['case_id']; before = db.one('SELECT status,version FROM reference_cases WHERE id=?', (case_id,))
    counts = db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'], db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']
    import app.reference_cases as cases_module
    actual = cases_module.build_projection_followup_proof
    def race(*args, **kwargs):
        proof = actual(*args, **kwargs)
        db.execute('UPDATE reference_results SET result_hash=? WHERE id=?', ('0' * 64, result['id']))
        return proof
    monkeypatch.setattr(cases_module, 'build_projection_followup_proof', race)
    response = client.post(f'/api/reference-cases/{case_id}/follow-up-results', json={
        'expected_version': attached['version'], 'run_id': run['id'], 'result_id': result['id'],
        'supplemental_document_ids': [supplement['document_id']], 'note': 'race target hash',
    })
    assert response.status_code == 409, response.text
    assert db.one('SELECT status,version FROM reference_cases WHERE id=?', (case_id,)) == before
    assert (db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n'], db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']) == counts
