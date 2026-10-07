"""Public, mock-only projection follow-up case flow.

This deliberately uses a previously saved source case and a later frozen run;
it is not the same-run fixture used by the proof helper unit tests.
"""
from __future__ import annotations
import json
import httpx

from .test_reference_cases import _result
from .test_reference_cases import _followup_result
from .test_reference_results import _saved_run
from .test_reference_projection_followup import QUESTION, _review
from .test_reference_projection_identity import _saved
from .test_reference_projection_input import _pdf
from .test_projection_loop2_gateway import _need
from .test_gateway_projection import _gateway
from .conftest import upload
from app.reference_projection_loop import ProjectProjectionLoop
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V2
from app.reference_results import ReferenceResultStore
from app.reference_text_profiles import profile


def _later_projection(client, project, tmp_path, initial, supplement):
    """Freeze the already-attached documents, then run a real receipt-6 loop."""
    runner = client.app.state.runner; db = client.app.state.db
    run = runner.create(project['id'], analysis_mode='REFERENCE_QA')
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?", (run['id'],))
    rows = []; results = []
    for document, text in ((initial, 'alpha original material'), (supplement, 'bravo new supplemental material')):
        payload = {'evidence_id': f'EV-{document["document_id"]}', 'project_id': project['id'],
                   'input_snapshot_id': run['snapshot_id'], 'document_id': document['document_id'],
                   'raw_text': text, 'locator': {'page_number': 1, 'bbox': [0, 0, 100, 20]},
                   'extraction_method': 'TEXT_LAYER', 'content_basis': 'SOURCE_TEXT', 'text_map': []}
        rows.append((f'{run["id"]}:{document["document_id"]}:1', run['id'], project['id'], document['document_id'],
                     json.dumps(payload), 'PENDING', None, ''))
        results.append((run['id'], document['document_id'], 'SUCCESS', json.dumps({'status': 'SUCCESS', 'pages': []})))
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)', rows)
        connection.executemany('INSERT INTO document_results VALUES(?,?,?,?)', results)
    run = runner.get(run['id'])
    sent = []
    def handler(request):
        body = json.loads(request.content); envelope = json.loads(body['messages'][1]['content'])
        sent.append(envelope)
        answer = _need(envelope, 'bravo') if envelope['round'] == 1 else _review(envelope)
        return httpx.Response(200, json={'id': f'case-flow-{len(sent)}', 'choices': [
            {'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})
    # Do not use _setup here: it uploads its own fixture documents, which would
    # violate this already-frozen run's complete-snapshot boundary.
    gateway = _gateway(tmp_path, db, handler)
    try:
        loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V2)
        route = profile('FLASH_NONE'); preview = loop.preview(run, QUESTION, route)['preview_proof']
        output = loop.ask(run, QUESTION, route, preview_proof=preview)
        saved = ReferenceResultStore(db, client.app.state.uploads).save_projection(run, QUESTION, output, route)
        assert len(sent) == 2
        return db, runner.get(run['id']), db.one('SELECT * FROM reference_results WHERE id=?', (saved['result_id'],))
    finally: gateway.close()


def test_projection_followup_public_case_flow_is_idempotent_and_survives_missing_pdf(
        client, project, tmp_path):
    # The original human case exists before the later V2 projection run and
    # before its attachment is added.
    db, source_run, _source_doc, _ = _saved_run(client, project, 'REFERENCE_QA')
    source_result = _result(client.app.state.reference_results, source_run, QUESTION,
                            status='NEED_USER_INPUT')
    created = client.post(f'/api/projects/{project["id"]}/reference-cases', json={
        'run_id': source_run['id'], 'question': QUESTION, 'result_id': source_result,
        'note': 'Awaiting the supplemental projection document.'})
    assert created.status_code == 201, created.text
    case = created.json()

    # This is a new complete frozen run with a real receipt-6 two-round,
    # MockTransport projection result.  Its supplement was not attached when
    # the source case was created.
    initial = upload(client, project['id'], 'projection-initial.pdf', _pdf('alpha original material'))
    supplement = upload(client, project['id'], 'projection-supplement.pdf', _pdf('bravo new supplemental material'))
    attached = client.post(f'/api/reference-cases/{case["case_id"]}/update', json={
        'expected_version': case['version'], 'attachments': [supplement['document_id']],
        'note': 'Attached the later supplemental projection document.'})
    assert attached.status_code == 200, attached.text
    case = attached.json()
    db, followup_run, projection_result = _later_projection(client, project, tmp_path, initial, supplement)
    assert supplement['document_id'] in followup_run['document_ids']
    assert followup_run['id'] != source_run['id']

    body = {
        'expected_version': case['version'], 'run_id': followup_run['id'],
        'result_id': projection_result['id'], 'supplemental_document_ids': [supplement['document_id']],
        'note': 'Link the saved projection follow-up.'}
    linked = client.post(f'/api/reference-cases/{case["case_id"]}/follow-up-results', json=body)
    assert linked.status_code == 200, linked.text
    case = linked.json()
    assert case['status'] == 'IN_REVIEW' and len(case['followups']) == 1
    proof = case['followups'][0]['proof']
    assert proof['proof_version'] == 'reference-case-projection-followup-proof-1'
    assert proof['documents'][0]['document_id'] == supplement['document_id']
    assert proof['documents'][0]['text_input_rounds'] == [2]

    replay = client.post(f'/api/reference-cases/{case["case_id"]}/follow-up-results', json=body)
    assert replay.status_code == 200, replay.text
    assert len(replay.json()['followups']) == 1
    changed = client.post(f'/api/reference-cases/{case["case_id"]}/follow-up-results', json={
        **body, 'expected_version': case['version'], 'note': 'A changed note is not an idempotent replay.'})
    assert changed.status_code == 409

    # Retrieval is history-only: it remains valid after source removal and does
    # not claim to authenticate/reconstruct the deleted PDF again.
    client.app.state.uploads.object_path(db.one('SELECT * FROM documents WHERE id=?', (supplement['document_id'],))).unlink()
    read = client.get(f'/api/reference-cases/{case["case_id"]}')
    assert read.status_code == 200, read.text
    assert read.json()['followups'][0]['proof']['proof_sha256'] == proof['proof_sha256']

    resolved = client.post(f'/api/reference-cases/{case["case_id"]}/update', json={
        'expected_version': case['version'], 'status': 'RESOLVED',
        'resolution': 'Human reviewer completed the projected follow-up.'})
    assert resolved.status_code == 200, resolved.text
    reopened = client.post(f'/api/reference-cases/{case["case_id"]}/update', json={
        'expected_version': resolved.json()['version'], 'status': 'OPEN',
        'note': 'Human reviewer reopened the case.'})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()['status'] == 'OPEN' and reopened.json()['resolution'] == ''


def test_projection_source_case_accepts_existing_legacy_followup(client, project, tmp_path):
    """New projection consumers must not alter legacy follow-up proof bytes."""
    db, projection_run, gateway, _sent, projection_saved, _row = _saved(
        client, project, tmp_path, pages=['alpha? The approved color is blue.'])
    try:
        document = db.one('SELECT id,name FROM documents WHERE project_id=? ORDER BY id LIMIT 1', (project['id'],))
        created = client.post(f'/api/projects/{project["id"]}/reference-cases', json={
            'run_id': projection_run['id'], 'question': QUESTION, 'result_id': projection_saved['result_id'],
            'attachments': [document['id']], 'note': 'Projection case awaiting a conventional follow-up.'})
        assert created.status_code == 201, created.text
        case = created.json()
        legacy_run, legacy_result = _followup_result(db, client, project,
            {'document_id': document['id'], 'name': document['name']}, QUESTION)
        linked = client.post(f'/api/reference-cases/{case["case_id"]}/follow-up-results', json={
            'expected_version': case['version'], 'run_id': legacy_run['id'], 'result_id': legacy_result,
            'supplemental_document_ids': [document['id']], 'note': 'Link existing legacy result.'})
        assert linked.status_code == 200, linked.text
        proof = linked.json()['followups'][0]['proof']
        assert 'proof_version' not in proof and proof['documents'][0]['text_input_rounds'] == [1]
    finally:
        gateway.close()
