"""Regression coverage for durable system candidates behind human review."""
from __future__ import annotations

from copy import deepcopy
import json
import pytest

from app.db import dumps
from app.db import DomainError
from app.gateway import mock_extract
from .conftest import upload


def _prepared_material(client, project, monkeypatch):
    upload(client, project['id'], 'synthetic.txt',
           b'DEMO_MATERIAL|M-1|Concrete|-|strength=4000 psi|quantity=7')
    run = client.post(f'/api/projects/{project["id"]}/analysis-runs').json()
    db = client.app.state.db
    runner = client.app.state.runner
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run['id'],))
    run = runner.get(run['id'])
    run['model'] = 'mock'
    runner.parse_one(run, run['document_ids'][0])
    monkeypatch.setattr(runner.verifier, 'refresh', lambda *args, **kwargs: {})
    return db, runner, run


def _publish_strength(db, runner, run, value):
    row = db.one('SELECT id,payload FROM evidence WHERE run_id=?', (run['id'],))
    evidence = json.loads(row['payload'])
    evidence['raw_text'] = f'DEMO_MATERIAL|M-1|Concrete|-|strength={value}|quantity=7'
    db.execute('UPDATE evidence SET payload=?,status=?,extraction=? WHERE id=?', (
        dumps(evidence), 'EXTRACTED',
        dumps({'data': mock_extract(evidence), 'request_id': None, 'cached': False}), row['id']))
    runner.publish(run)


def _record(db, run_id):
    row = db.one("SELECT id,envelope,review_version FROM records WHERE run_id=? AND kind='MATERIAL'", (run_id,))
    return row['id'], json.loads(row['envelope']), row['review_version']


def _three_generation_sequence(client, project, monkeypatch):
    db, runner, run = _prepared_material(client, project, monkeypatch)
    _publish_strength(db, runner, run, '4000 psi')
    record_id, _, version = _record(db, run['id'])
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run['id'],))
    assert client.post(f'/api/records/{record_id}/review', json={
        'action': 'ACCEPTED', 'expected_version': version,
    }).status_code == 200
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run['id'],))
    _publish_strength(db, runner, run, '5000 psi')  # B: accepted -> pending
    _publish_strength(db, runner, run, '6000 psi')  # C: pending -> pending
    record_id, current, version = _record(db, run['id'])
    assert current['candidate']['design_properties'][0]['value'] == '6000 psi'
    assert current['review']['status'] == 'PENDING'
    return db, runner, run, record_id, current, version


def test_publish_preserves_edited_c_when_latest_system_baseline_is_c(client, project, monkeypatch):
    db, runner, run, record_id, current, version = _three_generation_sequence(client, project, monkeypatch)
    # The independent provenance must include the pending-to-pending C update.
    events = db.all('SELECT action,after_json FROM review_events WHERE record_id=? ORDER BY created_at,id', (record_id,))
    assert any(event['action'] == 'SYSTEM_BASELINE_UPDATED'
               and json.loads(event['after_json'])['candidate']['design_properties'][0]['value'] == '6000 psi'
               for event in events)

    edited = deepcopy(current['candidate'])
    edited['name'] = 'Synthetic engineer edited name'
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run['id'],))
    response = client.post(f'/api/records/{record_id}/review', json={
        'action': 'EDITED', 'expected_version': version, 'candidate': edited, 'note': 'synthetic edit',
    })
    assert response.status_code == 200, response.text
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run['id'],))
    runner.publish(run)

    _, final, final_version = _record(db, run['id'])
    assert final['candidate']['name'] == 'Synthetic engineer edited name'
    assert final['candidate']['design_properties'][0]['value'] == '6000 psi'
    assert final['review']['status'] == 'EDITED'
    assert final_version == version + 1


def test_legacy_edited_c_recovers_c_baseline_without_new_provenance_event(client, project, monkeypatch):
    db, runner, run, record_id, current, version = _three_generation_sequence(client, project, monkeypatch)
    db.execute("DELETE FROM review_events WHERE record_id=? AND action='SYSTEM_BASELINE_UPDATED'", (record_id,))

    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run['id'],))
    quantity = client.post(f'/api/records/{record_id}/review', json={
        'expected_version': version, 'quantity_action': 'REJECTED', 'note': 'synthetic legacy quantity review',
    })
    assert quantity.status_code == 200, quantity.text
    _, quantity_reviewed, quantity_version = _record(db, run['id'])
    assert quantity_reviewed['quantity_review'] == 'REJECTED'

    edited = deepcopy(current['candidate'])
    edited['name'] = 'Synthetic legacy edited name'
    response = client.post(f'/api/records/{record_id}/review', json={
        'action': 'EDITED', 'expected_version': quantity_version, 'candidate': edited, 'note': 'synthetic legacy edit',
    })
    assert response.status_code == 200, response.text
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run['id'],))
    runner.publish(run)

    _, final, final_version = _record(db, run['id'])
    assert final['candidate']['name'] == 'Synthetic legacy edited name'
    assert final['candidate']['design_properties'][0]['value'] == '6000 psi'
    assert final['review']['status'] == 'EDITED'
    assert final_version == quantity_version + 1


def test_unchanged_pending_publish_does_not_create_baseline_events_or_bump_version(client, project, monkeypatch):
    db, runner, run, record_id, _, version = _three_generation_sequence(client, project, monkeypatch)
    before = db.one("SELECT COUNT(*) AS n FROM review_events WHERE record_id=? AND action='SYSTEM_BASELINE_UPDATED'",
                    (record_id,))['n']
    runner.publish(run)
    runner.publish(run)
    _, current, final_version = _record(db, run['id'])
    after = db.one("SELECT COUNT(*) AS n FROM review_events WHERE record_id=? AND action='SYSTEM_BASELINE_UPDATED'",
                   (record_id,))['n']
    assert current['review']['status'] == 'PENDING'
    assert final_version == version and after == before


def test_checkpoint_preserves_quantity_only_review_then_edited_c(client, project, monkeypatch):
    db, runner, run, record_id, current, version = _three_generation_sequence(client, project, monkeypatch)
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run['id'],))
    quantity = client.post(f'/api/records/{record_id}/review', json={
        'expected_version': version, 'quantity_action': 'REJECTED', 'note': 'synthetic quantity review',
    })
    assert quantity.status_code == 200, quantity.text
    _, quantity_reviewed, quantity_version = _record(db, run['id'])
    assert quantity_reviewed['review']['status'] == 'PENDING'
    assert quantity_reviewed['quantity_review'] == 'REJECTED'

    edited = deepcopy(quantity_reviewed['candidate'])
    edited['name'] = 'Synthetic edited after quantity review'
    response = client.post(f'/api/records/{record_id}/review', json={
        'action': 'EDITED', 'expected_version': quantity_version, 'candidate': edited, 'note': 'synthetic edit',
    })
    assert response.status_code == 200, response.text
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run['id'],))
    runner.publish(run)
    _, final, final_version = _record(db, run['id'])
    assert final['candidate']['name'] == 'Synthetic edited after quantity review'
    assert final['review']['status'] == 'EDITED'
    assert final_version == quantity_version + 1


def test_repeated_accepted_and_rejected_reviews_are_preserved_for_identical_c(client, project, monkeypatch):
    db, runner, run, record_id, current, version = _three_generation_sequence(client, project, monkeypatch)
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run['id'],))
    for action in ('ACCEPTED', 'ACCEPTED', 'REJECTED'):
        response = client.post(f'/api/records/{record_id}/review', json={
            'action': action, 'expected_version': version,
        })
        assert response.status_code == 200, response.text
        version += 1
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run['id'],))
    runner.publish(run)
    _, final, final_version = _record(db, run['id'])
    assert final['candidate']['design_properties'][0]['value'] == current['candidate']['design_properties'][0]['value']
    assert final['review']['status'] == 'REJECTED'
    assert final_version == version


def test_missing_human_provenance_fails_closed_without_overwriting_edited_candidate(client, project, monkeypatch):
    db, runner, run, record_id, current, version = _three_generation_sequence(client, project, monkeypatch)
    corrupted = deepcopy(current)
    corrupted['candidate']['name'] = 'Synthetic unknown edited name'
    corrupted['review'] = {'status': 'EDITED', 'event_id': 'missing', 'actor_id': 'local-engineer'}
    db.execute('DELETE FROM review_events WHERE record_id=?', (record_id,))
    db.execute('UPDATE records SET envelope=? WHERE id=?', (dumps(corrupted), record_id))
    runner.publish(run)
    _, final, final_version = _record(db, run['id'])
    assert final['candidate']['name'] == 'Synthetic unknown edited name'
    assert final['review']['status'] == 'EDITED'
    assert final_version == version


def test_legacy_unscoped_merged_material_splits_to_two_pending_current_facts(client, project, monkeypatch):
    upload(client, project['id'], 'synthetic-first.txt',
           b'DEMO_MATERIAL|M-1|Concrete|-|strength=4000 psi|location=Level 1|note=first')
    upload(client, project['id'], 'synthetic-second.txt',
           b'DEMO_MATERIAL|M-1|Concrete|-|strength=4000 psi|location=Level 1|note=second')
    run = client.post(f'/api/projects/{project["id"]}/analysis-runs').json()
    db = client.app.state.db
    runner = client.app.state.runner
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run['id'],))
    run = runner.get(run['id'])
    run['model'] = 'mock'
    monkeypatch.setattr(runner.verifier, 'refresh', lambda *args, **kwargs: {})
    for document_id in run['document_ids']:
        runner.parse_one(run, document_id)
    for row in db.all('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY id', (run['id'],)):
        evidence = json.loads(row['payload'])
        evidence['raw_text'] = f'DEMO_MATERIAL|M-1|Concrete|-|strength=4000 psi|location=Level 1|note={row["id"]}'
        db.execute("UPDATE evidence SET payload=?,status='EXTRACTED',extraction=? WHERE id=?", (
            dumps(evidence), dumps({'data': mock_extract(evidence), 'request_id': None, 'cached': False}), row['id']))
    runner.publish(run)
    old_id, merged, version = _record(db, run['id'])
    assert merged['candidate']['candidate_key'].startswith('MG-')
    assert len(merged['candidate']['evidence_ids']) == 2

    # Reproduce a pre-scope legacy MG row: it represents both source facts but
    # lacks the explicit scope now required for cross-document identity.
    legacy = deepcopy(merged)
    legacy['candidate']['candidate_key'] = 'MG-legacy-unscoped'
    legacy['candidate']['design_properties'] = [
        prop for prop in legacy['candidate']['design_properties']
        if prop['name'].casefold() != 'location'
    ]
    db.execute('UPDATE records SET envelope=?,logical_key=? WHERE id=?',
               (dumps(legacy), 'MG-legacy-unscoped', old_id))
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?", (run['id'],))
    assert client.post(f'/api/records/{old_id}/review', json={
        'action': 'ACCEPTED', 'expected_version': version,
    }).status_code == 200

    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run['id'],))
    for row in db.all('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY id', (run['id'],)):
        evidence = json.loads(row['payload'])
        evidence['raw_text'] = f'DEMO_MATERIAL|M-1|Concrete|-|strength=4000 psi|note={row["id"]}'
        db.execute("UPDATE evidence SET payload=?,status='EXTRACTED',extraction=? WHERE id=?", (
            dumps(evidence), dumps({'data': mock_extract(evidence), 'request_id': None, 'cached': False}), row['id']))
    runner.publish(run)

    rows = db.all("SELECT id,envelope,logical_key FROM records WHERE run_id=? AND kind='MATERIAL' ORDER BY id", (run['id'],))
    assert len(rows) == 2
    assert {row['id'] for row in rows} >= {old_id}
    assert all(not row['logical_key'].startswith('MG-') for row in rows)
    current = [json.loads(row['envelope']) for row in rows]
    assert all(item['review']['status'] == 'PENDING' for item in current)
    assert all(len(item['candidate']['evidence_ids']) == 1 for item in current)
    history = db.all('SELECT action,before_json FROM review_events WHERE record_id=? ORDER BY created_at,id', (old_id,))
    assert history[0]['action'] == 'ACCEPTED'
    assert json.loads(history[0]['before_json'])['candidate']['candidate_key'] == 'MG-legacy-unscoped'


def test_legacy_unscoped_split_with_two_matching_old_records_fails_closed(client, project, monkeypatch):
    db, runner, run, record_id, current, version = _three_generation_sequence(client, project, monkeypatch)
    legacy = deepcopy(current)
    legacy_id = 'REC-legacy-ambiguity'
    legacy['meta']['record_id'] = legacy_id
    legacy['candidate']['candidate_key'] = 'MG-legacy-ambiguity'
    db.execute('DELETE FROM review_events WHERE record_id=?', (record_id,))
    db.execute('UPDATE records SET id=?,envelope=?,logical_key=? WHERE id=?',
               (legacy_id, dumps(legacy), 'MG-legacy-ambiguity', record_id))
    duplicate = deepcopy(legacy)
    duplicate_id = 'REC-legacy-ambiguity-duplicate'
    duplicate['meta']['record_id'] = duplicate_id
    db.execute('''INSERT INTO records(id,run_id,project_id,kind,envelope,review_version,logical_key)
                  VALUES(?,?,?,?,?,?,?)''', (
        duplicate_id, run['id'], run['project_id'], 'MATERIAL', dumps(duplicate), version,
        'MG-legacy-ambiguity-duplicate'))
    with pytest.raises(DomainError, match='多条记录'):
        runner.publish(run)
    assert db.one('SELECT COUNT(*) AS n FROM records WHERE run_id=? AND kind=?',
                  (run['id'], 'MATERIAL'))['n'] == 2
