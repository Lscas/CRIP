"""HTTP-only release boundary for the explicit, proof-bound v9 route."""
from copy import deepcopy
from contextlib import closing, contextmanager
import json
import hashlib
from pathlib import Path
import shutil
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.page_selector import LAYOUT_BOUND_SELECTOR_VERSION
from app.reference_text_profiles import profile
from app.settings import Settings
from .test_reference_v9_evaluations import _cannot_answer, _channel, _v9_run
from .test_page_selector import _raw_run, _spatial_text


QUESTION = 'What approved color applies to Finish key PT9?'


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(tmp_path, start_worker=False, reference_layout_enabled=True))
    with TestClient(app, headers={'X-CIRP-Client': 'browser'}) as current:
        yield current


@contextmanager
def disabled_client(client):
    # Restore a consistent synthetic SQLite backup to an isolated instance.
    # Do not mutate frozen settings or bypass the real single-instance lock.
    with tempfile.TemporaryDirectory(prefix='cirp-v9-disabled-') as directory:
        copied = Path(directory)
        database = client.app.state.db.path
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(copied / database.name)) as target:
            source.backup(target)
        objects = client.app.state.settings.data_dir / 'objects'
        shutil.copytree(objects, copied / 'objects')
        app = create_app(Settings(copied, start_worker=False))
        with TestClient(app, headers={'X-CIRP-Client': 'browser'}) as current:
            yield current


def _preview(client, project, run, profile_id='FLASH_NONE'):
    return client.post(f"/api/projects/{project['id']}/questions-v3/preview", json={
        'run_id': run['id'], 'question': QUESTION,
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': profile_id,
    })


def test_v9_preview_is_flagged_named_and_does_not_need_a_configured_channel(client, project):
    _, run, _ = _v9_run(client, project)
    with disabled_client(client) as disabled:
        capability = disabled.get('/api/settings').json()['capabilities']['reference_layout_v9']
        assert capability['enabled'] is False
        assert capability['evaluation_execute_preview_proof'] is True
        assert _preview(disabled, project, run).status_code == 409
        legacy = disabled.post(f"/api/projects/{project['id']}/questions-v3/preview", json={
            'run_id': run['id'], 'question': QUESTION})
        assert legacy.status_code == 200 and 'preview_proof' not in legacy.json()
    assert client.app.state.db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0
    missing = client.post(f"/api/projects/{project['id']}/questions-v3/preview", json={
        'run_id': run['id'], 'question': QUESTION,
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION,
    })
    assert missing.status_code == 409
    ready = _preview(client, project, run)
    assert ready.status_code == 200, ready.text
    body = ready.json()
    knowledge = client.get(f"/api/projects/{project['id']}/reference-knowledge").json()
    assert knowledge['run_id'] == run['id'] and knowledge['snapshot_id'] == run['snapshot_id']
    assert body['model_called'] is False
    assert body['execution_profile'] == profile('FLASH_NONE')
    assert set(body['preview_proof']) == {
        'proof_version', 'project_id', 'run_id', 'snapshot_id',
        'normalized_question_sha256', 'selector_version', 'context_policy',
        'profile_version', 'profile_id', 'route_sha256', 'selection_id',
        'initial_evidence_manifest_sha256', 'prompt_contract_hash',
    }


def test_v9_ask_rejects_strict_or_stale_proofs_before_reserve_or_http(client, project):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    proof = _preview(client, project, run).json()['preview_proof']
    payload = {'run_id': run['id'], 'question': QUESTION,
               'selector_version': LAYOUT_BOUND_SELECTOR_VERSION,
               'profile_id': 'FLASH_NONE', 'preview_proof': proof}

    malformed = deepcopy(payload)
    malformed['preview_proof']['unexpected'] = 'rejected'
    assert client.post(f"/api/projects/{project['id']}/questions-v3", json=malformed).status_code == 422

    literal_fields = {'proof_version', 'selector_version', 'context_policy', 'profile_version'}
    for key, value in proof.items():
        stale = deepcopy(payload)
        stale['preview_proof'][key] = ('0' * 64 if key.endswith('sha256') or key == 'prompt_contract_hash'
                                       else ('PRO' if key == 'profile_id' else value + '-stale'))
        response = client.post(f"/api/projects/{project['id']}/questions-v3", json=stale)
        assert response.status_code == (422 if key in literal_fields else 409), (key, response.text)
    assert calls == []
    assert client.app.state.db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0


@pytest.mark.parametrize('profile_id', ['FLASH_NONE', 'FLASH_LOW', 'PRO'])
def test_v9_ask_uses_each_named_profile_and_one_frozen_selection(client, project, profile_id, monkeypatch):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    preview = _preview(client, project, run, profile_id)
    assert preview.status_code == 200
    import app.evidence_loop as loop_module
    original_select = loop_module.select_pages
    selections = []
    def counted_select(*args, **kwargs):
        selections.append(1)
        return original_select(*args, **kwargs)
    monkeypatch.setattr(loop_module, 'select_pages', counted_select)
    answer = client.post(f"/api/projects/{project['id']}/questions-v3", json={
        'run_id': run['id'], 'question': QUESTION,
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': profile_id,
        'preview_proof': preview.json()['preview_proof'],
    })
    assert answer.status_code == 200, answer.text
    assert answer.json()['execution_profile']['profile_id'] == profile_id
    assert len(calls) == 1
    assert selections == [1]
    receipt = answer.json()['execution_receipts'][0]
    assert receipt['initial_evidence_manifest_sha256'] == preview.json()['preview_proof']['initial_evidence_manifest_sha256']


def test_v9_evaluation_creation_preview_pending_gate_and_historical_replay(client, project):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    body = {'run_id': run['id'], 'name': 'v9 HTTP', 'questions': [QUESTION],
            'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE'}
    with disabled_client(client) as disabled:
        assert disabled.post(f"/api/projects/{project['id']}/reference-evaluations", json=body).status_code == 409
    created = client.post(f"/api/projects/{project['id']}/reference-evaluations", json=body)
    assert created.status_code == 201, created.text
    evaluation = created.json()
    item_id = evaluation['items'][0]['item_id']
    preview = client.post(f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/preview")
    assert preview.status_code == 200 and 'preview_proof' in preview.json()
    assert preview.json()['preview_proof'] == _preview(client, project, run).json()['preview_proof']

    with disabled_client(client) as disabled:
        pending = disabled.post(f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/execute")
        assert pending.status_code == 409
        assert disabled.post(f"/api/reference-evaluations/{evaluation['evaluation_id']}/clone", json={'profile_id': 'PRO'}).status_code == 409
        assert disabled.post(f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs", json={'confirmed': True}).status_code == 409
    assert calls == []

    first = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/execute",
        json={'preview_proof': preview.json()['preview_proof']})
    assert first.status_code == 200 and first.json()['replayed'] is False
    assert len(calls) == 1
    with disabled_client(client) as disabled:
        replay = disabled.post(f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/execute")
        assert replay.status_code == 200 and replay.json()['replayed'] is True
    assert len(calls) == 1


@pytest.mark.parametrize('missing', ['run_id', 'profile_id', 'preview_proof'])
def test_direct_v9_requires_explicit_inputs_before_call(client, project, missing):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    body = {'run_id': run['id'], 'question': QUESTION,
            'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE',
            'preview_proof': _preview(client, project, run).json()['preview_proof']}
    body.pop(missing)
    assert client.post(f"/api/projects/{project['id']}/questions-v3", json=body).status_code == 409
    assert calls == []
    assert client.app.state.db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0


def test_disabled_api_rejects_direct_v9_even_with_a_previously_valid_proof(client, project):
    _, run, _ = _v9_run(client, project)
    proof = _preview(client, project, run).json()['preview_proof']
    with disabled_client(client) as disabled:
        calls = _channel(disabled, lambda *_: _cannot_answer())
        result = disabled.post(f"/api/projects/{project['id']}/questions-v3", json={
            'run_id': run['id'], 'question': QUESTION, 'selector_version': LAYOUT_BOUND_SELECTOR_VERSION,
            'profile_id': 'FLASH_NONE', 'preview_proof': proof})
        assert result.status_code == 409 and calls == []
        assert disabled.app.state.db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0


def test_disabled_api_authenticates_saved_failure_without_dispatch(client, project):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: 'not json')
    created = client.post(f"/api/projects/{project['id']}/reference-evaluations", json={
        'run_id': run['id'], 'name': 'Synthetic failure replay', 'questions': [QUESTION],
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE'}).json()
    path = f"/api/reference-evaluations/{created['evaluation_id']}/items/{created['items'][0]['item_id']}/execute"
    proof = client.post(
        f"/api/reference-evaluations/{created['evaluation_id']}/items/{created['items'][0]['item_id']}/preview").json()['preview_proof']
    first = client.post(path, json={'preview_proof': proof})
    assert first.status_code == 200 and first.json()['failure'] and len(calls) == 1
    with disabled_client(client) as disabled:
        replay = disabled.post(path, json={'preview_proof': proof, 'replay_only': True})
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['failure'] == first.json()['failure']
    client.app.state.db.execute(
        'UPDATE reference_evaluation_failures SET execution_receipt_json=? WHERE id=?',
        ('{}', first.json()['failure']['failure_id']))
    assert client.post(path).status_code == 409
    assert len(calls) == 1


def test_v9_evaluation_execute_proof_is_bound_before_dispatch_and_replay_only_is_local(client, project):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    evaluation = client.post(f"/api/projects/{project['id']}/reference-evaluations", json={
        'run_id': run['id'], 'name': 'Proof-bound evaluation', 'questions': [QUESTION],
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE'}).json()
    item_id = evaluation['items'][0]['item_id']
    path = f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/execute"
    proof = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/preview").json()['preview_proof']
    import app.evidence_loop as loop_module
    original_select = loop_module.select_pages
    selections = []
    def counted_select(*args, **kwargs):
        selections.append(1)
        return original_select(*args, **kwargs)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(loop_module, 'select_pages', counted_select)
    assert client.post(path, json={'replay_only': True}).status_code == 409
    assert selections == []
    literal_fields = {'proof_version', 'selector_version', 'context_policy', 'profile_version'}
    for key, value in proof.items():
        stale = deepcopy(proof)
        stale[key] = ('0' * 64 if key.endswith('sha256') or key == 'prompt_contract_hash'
                      else ('PRO' if key == 'profile_id' else value + '-stale'))
        response = client.post(path, json={'preview_proof': stale})
        assert response.status_code == (422 if key in literal_fields else 409), (key, response.text)
    assert calls == []
    assert client.app.state.db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0
    selections.clear()
    first = client.post(path, json={'preview_proof': proof})
    assert (first.status_code == 200 and first.json()['replayed'] is False
            and len(calls) == 1 and selections == [1])
    monkeypatch.undo()
    replay = client.post(path, json={'preview_proof': proof, 'replay_only': True})
    assert replay.status_code == 200 and replay.json()['replayed'] is True and len(calls) == 1


def test_v8_evaluation_rejects_a_v9_proof_without_dispatch(client, project):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    v8 = client.app.state.reference_evaluations.create(
        project['id'], run['id'], 'Legacy v8 control', [QUESTION], profile('FLASH_NONE'))
    v9 = client.post(f"/api/projects/{project['id']}/reference-evaluations", json={
        'run_id': run['id'], 'name': 'Proof source', 'questions': [QUESTION],
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE'}).json()
    proof = client.post(
        f"/api/reference-evaluations/{v9['evaluation_id']}/items/{v9['items'][0]['item_id']}/preview").json()['preview_proof']
    path = f"/api/reference-evaluations/{v8['evaluation_id']}/items/{v8['items'][0]['item_id']}/execute"
    assert client.post(path, json={'preview_proof': proof}).status_code == 409
    assert calls == []


def test_v9_evaluation_execute_rejects_real_source_drift_before_dispatch(client, project):
    db, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    evaluation = client.post(f"/api/projects/{project['id']}/reference-evaluations", json={
        'run_id': run['id'], 'name': 'Source drift', 'questions': [QUESTION],
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE'}).json()
    item_id = evaluation['items'][0]['item_id']
    proof = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/preview").json()['preview_proof']
    row = db.one('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY id LIMIT 1', (run['id'],))
    payload = json.loads(row['payload'])
    payload['raw_text'] += ' drifted after preview.'
    db.execute('UPDATE evidence SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
    response = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/execute",
        json={'preview_proof': proof})
    assert response.status_code == 409
    assert calls == []
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0


def test_v9_evaluation_execute_reports_current_cached_runtime_telemetry(client, project):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    def execute(name):
        evaluation = client.post(f"/api/projects/{project['id']}/reference-evaluations", json={
            'run_id': run['id'], 'name': name, 'questions': [QUESTION],
            'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE'}).json()
        item_id = evaluation['items'][0]['item_id']
        proof = client.post(
            f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/preview").json()['preview_proof']
        return client.post(
            f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/execute",
            json={'preview_proof': proof})
    first = execute('First cache identity')
    second = execute('Second cache identity')
    assert first.status_code == second.status_code == 200
    assert len(calls) == 1 and second.json()['replayed'] is False
    assert second.json()['execution_telemetry'] == {
        'scope': 'CURRENT_EXECUTE', 'model_call_count': 0,
        'decision_count': 1, 'cached_decision_count': 1,
    }


def test_v9_proof_rejects_prompt_contract_drift_before_first_reservation(client, project, monkeypatch):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    evaluation = client.post(f"/api/projects/{project['id']}/reference-evaluations", json={
        'run_id': run['id'], 'name': 'Prompt drift first round', 'questions': [QUESTION],
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE'}).json()
    item_id = evaluation['items'][0]['item_id']
    proof = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/preview").json()['preview_proof']
    import app.reference_layout_input as layout_input
    original = layout_input.prompt_contract
    reads = []
    def drifted_prompt():
        reads.append(1)
        text, digest = original()
        return (text, digest) if len(reads) == 1 else (text + '\nchanged', '0' * 64)
    monkeypatch.setattr(layout_input, 'prompt_contract', drifted_prompt)
    response = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/execute",
        json={'preview_proof': proof})
    assert response.status_code == 409 and calls == [] and len(reads) == 2
    assert client.app.state.db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0


def test_v9_proof_rechecks_prompt_contract_before_a_supplemental_round(client, project, monkeypatch):
    first, first_map = _spatial_text([(10, [('Finish', 10, 48), ('PT9', 52, 76)])])
    db, run, _ = _raw_run(client, project, [
        {'page': 1, 'sheet': 'A5.01', 'text': first, 'text_map': first_map},
        {'page': 2, 'sheet': 'A5.02', 'text': 'SupplementUnique local source.'},
    ])
    changed = [False]
    def decision(_, content):
        if content['round'] == 1:
            changed[0] = True
            return {'status': 'NEED_EVIDENCE', 'reason_code': 'MISSING_SOURCE_TEXT',
                    'missing_facts': ['SupplementUnique local source.'],
                    'requests': [{'tool': 'SEARCH_TEXT', 'query': 'SupplementUnique'}],
                    'answer': {'claims': [], 'calculations': [], 'coverage': []}}
        return _cannot_answer()
    calls = _channel(client, decision)
    evaluation = client.post(f"/api/projects/{project['id']}/reference-evaluations", json={
        'run_id': run['id'], 'name': 'Prompt drift supplement', 'questions': [QUESTION],
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE'}).json()
    item_id = evaluation['items'][0]['item_id']
    proof = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/preview").json()['preview_proof']
    import app.reference_layout_input as layout_input
    original = layout_input.prompt_contract
    def drift_after_first_round():
        text, digest = original()
        return (text + '\nchanged', '0' * 64) if changed[0] else (text, digest)
    monkeypatch.setattr(layout_input, 'prompt_contract', drift_after_first_round)
    response = client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/execute",
        json={'preview_proof': proof})
    assert response.status_code == 409 and len(calls) == 1
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 1


def test_terminal_v9_result_source_and_receipt_tampering_reject_without_new_call(client, project):
    _, run, _ = _v9_run(client, project)
    calls = _channel(client, lambda *_: _cannot_answer())
    def terminal(terminal_run, name):
        evaluation = client.post(f"/api/projects/{project['id']}/reference-evaluations", json={
            'run_id': terminal_run['id'], 'name': name, 'questions': [QUESTION],
            'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE'}).json()
        item_id = evaluation['items'][0]['item_id']
        path = f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/execute"
        proof = client.post(
            f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item_id}/preview").json()['preview_proof']
        response = client.post(path, json={'preview_proof': proof})
        assert response.status_code == 200
        return path, response.json()['result']['result_id']

    source_path, _ = terminal(run, 'Terminal source')
    row = client.app.state.db.one('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY id LIMIT 1', (run['id'],))
    payload = json.loads(row['payload']); payload['raw_text'] += ' terminal drift.'
    client.app.state.db.execute('UPDATE evidence SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
    assert client.post(source_path).status_code == 409

    # Use independent data after the source mutation so each negative path is
    # authenticated for its own terminal result rather than masked by drift.
    _, clean_run, _ = _v9_run(client, project)
    result_path, result_id = terminal(clean_run, 'Terminal result')
    client.app.state.db.execute('UPDATE reference_results SET result_hash=? WHERE id=?', ('0' * 64, result_id))
    assert client.post(result_path).status_code == 409

    # A receipt mutation with a matching outer result hash still fails receipt3
    # source/prompt authentication before any new provider dispatch.
    _, receipt_run, _ = _v9_run(client, project)
    receipt_path, receipt_id = terminal(receipt_run, 'Terminal receipt')
    row = client.app.state.db.one('SELECT result_json FROM reference_results WHERE id=?', (receipt_id,))
    value = json.loads(row['result_json'])
    value['execution_receipts'][0]['prompt_contract_hash'] = '0' * 64
    raw = json.dumps(value)
    client.app.state.db.execute(
        'UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?',
        (raw, hashlib.sha256(raw.encode('utf-8')).hexdigest(), receipt_id))
    assert client.post(receipt_path).status_code == 409
    assert len(calls) == 3


def test_disabled_managed_callback_halts_previously_queued_v9_without_dispatch(client, project):
    _, run, _ = _v9_run(client, project)
    _channel(client, lambda *_: _cannot_answer())
    evaluation = client.post(f"/api/projects/{project['id']}/reference-evaluations", json={
        'run_id': run['id'], 'name': 'Synthetic queued v9', 'questions': [QUESTION],
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'profile_id': 'FLASH_NONE'}).json()
    queued = client.post(f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs", json={'confirmed': True})
    assert queued.status_code == 202, queued.text
    job = queued.json()
    with disabled_client(client) as disabled:
        calls = _channel(disabled, lambda *_: _cannot_answer())
        disabled.app.state.reference_evaluation_jobs.process(job['job_id'])
        ended = disabled.get(f"/api/reference-evaluation-jobs/{job['job_id']}").json()
        assert ended['state'] == 'HALTED', ended
        unchanged = disabled.get(f"/api/reference-evaluations/{evaluation['evaluation_id']}").json()
        assert unchanged['items'] == evaluation['items']
        assert calls == []
        assert disabled.app.state.db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0
