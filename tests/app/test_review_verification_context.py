"""Regression coverage for semantic-verification context invalidation."""
from __future__ import annotations

from copy import deepcopy
import json

from app.db import dumps
from app.gateway import mock_extract
from app.verification import summarize
from .conftest import upload


def _published_inspection(client, project):
    """Publish one wholly synthetic inspection without contacting a provider."""
    upload(client, project['id'], 'synthetic-inspection.txt', (
        b'DEMO_TEST|PAD-01|Pre-pour inspection|-|'
        b'requirement=Verify reinforcing steel|'
        b'timing=Before placement|frequency=Each placement|'
        b'standard_reference=ASTM C94'))
    run_id = client.post(f'/api/projects/{project["id"]}/analysis-runs').json()['id']
    db = client.app.state.db
    runner = client.app.state.runner
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?", (run_id,))
    run = runner.get(run_id)
    run['model'] = 'mock'
    runner.parse_one(run, run['document_ids'][0])
    evidence_row = db.one('SELECT id,payload FROM evidence WHERE run_id=?', (run_id,))
    evidence = json.loads(evidence_row['payload'])
    db.execute('UPDATE evidence SET status=?,extraction=? WHERE id=?', (
        'EXTRACTED', dumps({'data': mock_extract(evidence), 'request_id': None, 'cached': False}),
        evidence_row['id']))
    runner.publish(run)
    record = db.one("SELECT id,envelope,review_version FROM records WHERE run_id=? AND kind='INSPECTION'", (run_id,))
    return db, runner.verifier, record['id'], json.loads(record['envelope']), record['review_version']


def test_inspection_requirement_change_invalidates_dependent_semantic_cache(client, project):
    db, service, record_id, envelope, review_version = _published_inspection(client, project)
    original_review = deepcopy(envelope['review'])
    report = service.refresh(record_id, allow_model=False)
    dependent = {'/frequency', '/timing', '/standard_reference'}
    assert dependent.issubset({field['path'] for field in report['fields']})
    for field in report['fields']:
        if field['path'] in dependent:
            field.update(status='SUPPORTED', method='CHEAP_MODEL_SEMANTIC', request_id='CALL-synthetic-old')
    summarize(report)
    assert service.save(report)

    changed = deepcopy(envelope)
    changed['candidate']['requirement'] = 'Verify reinforcing steel and embedded items'
    db.execute('UPDATE records SET envelope=? WHERE id=?', (dumps(changed), record_id))
    refreshed = service.refresh(record_id, allow_model=False)

    for field in refreshed['fields']:
        if field['path'] in dependent:
            assert field['status'] == 'NEEDS_SEMANTIC'
            assert field['request_id'] is None
    saved = json.loads(db.one('SELECT envelope FROM records WHERE id=?', (record_id,))['envelope'])
    assert saved['review'] == original_review
    assert db.one('SELECT review_version FROM records WHERE id=?', (record_id,))['review_version'] == review_version
    assert db.model_call_stats(envelope['meta']['project_id'])['calls'] == 0
