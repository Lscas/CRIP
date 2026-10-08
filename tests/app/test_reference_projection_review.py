"""Production source-review routes: synthetic uploads and settled MockTransport only."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image
import pytest

from app import reference_projection_execution as execution
from app import reference_projection_review as rendering
from app import reference_results as results
from app.reference_projection_loop import ProjectProjectionLoop
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1
from tests.app.test_reference_projection_consumers import _snapshot
from tests.app.test_reference_projection_results import _formal_store, _output, _review, _need


@pytest.fixture
def review(client, project, tmp_path):
    db, run, gateway, loop, sent, store = _formal_store(client, project, tmp_path, _review)
    try:
        route, output = _output(loop, run)
        saved = store.save_projection(run, 'alpha?', output, route)
        document = db.one('SELECT * FROM documents WHERE project_id=?', (project['id'],))
        yield db, run, saved, sent, store, client.app.state.uploads.object_path(document)
    finally:
        gateway.close()


def _url(saved, image=False):
    path = f"/api/reference-results/{saved['result_id']}/projection-review"
    return path + '/sources/1/image' if image else path


def _rehash_record(db, saved, mutate):
    row = db.one('SELECT * FROM reference_results WHERE id=?', (saved['result_id'],))
    record = json.loads(row['result_json'])
    mutate(record)
    raw = results._canonical_projection(record)
    key = hashlib.sha256(results._canonical_projection([
        results._PROJECTION_KEY_DOMAIN, row['run_id'], row['snapshot_id'], row['question_key'],
        row['provider'], row['model'], results._projection_sha256(results._projection_semantic(record)),
    ]).encode()).hexdigest()
    db.execute('UPDATE reference_results SET result_json=?,result_hash=?,result_key=? WHERE id=?',
               (raw, hashlib.sha256(raw.encode()).hexdigest(), key, row['id']))


def test_view_and_image_authenticate_once_each_without_writes_or_model_calls(client, review, monkeypatch):
    db, run, saved, sent, store, path = review
    before, calls = _snapshot(db), len(sent)
    authenticated = []
    original = execution._authenticate_chain
    def observe(*args, **kwargs):
        authenticated.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(execution, '_authenticate_chain', observe)
    response = client.get(_url(saved))
    assert response.status_code == 200, response.text
    assert len(authenticated) == 1
    view = response.json()
    assert view['result_id'] == saved['result_id'] and view['result_hash'] == saved['result_hash']
    assert view['status'] == 'REVIEW_REQUIRED'
    assert view['object_condition_relations_verified'] is False
    assert view['answer_completeness_verified'] is False
    source = view['selections'][0]
    assert source['text'] == 'alpha initial note'
    assert source['row_text_sha256'] == hashlib.sha256(source['text'].encode()).hexdigest()
    assert len(source['char_ids']) > 0 and source['part_refs'] == ['P1']
    assert 'object_key' not in response.text and str(path) not in response.text
    image = client.get(_url(saved, True))
    assert image.status_code == 200, image.text if image.status_code != 200 else ''
    assert len(authenticated) == 2
    assert image.headers['content-type'] == 'image/png'
    assert response.headers['cache-control'] == image.headers['cache-control'] == 'no-store'
    assert Image.open(BytesIO(image.content)).width > 0
    assert _snapshot(db) == before and len(sent) == calls
    detached = store.get_projection_review_view(saved['result_id'])
    detached['selections'][0]['text'] = 'client change'
    assert store.get_projection_review_view(saved['result_id']) == view


@pytest.mark.parametrize('tamper', [
    'raw_hash', 'metadata', 'key', 'kind', 'ledger', 'source', 'missing_source', 'packet', 'proof',
    'object_path', 'citation', 'review_event',
])
def test_every_trust_boundary_rejects_before_builder_and_renderer(client, review, monkeypatch, tamper):
    db, run, saved, sent, store, path = review
    if tamper == 'raw_hash':
        db.execute('UPDATE reference_results SET result_hash=? WHERE id=?', ('0'*64, saved['result_id']))
    elif tamper == 'metadata':
        db.execute('UPDATE reference_results SET provider=? WHERE id=?', ('other', saved['result_id']))
    elif tamper == 'key':
        db.execute('UPDATE reference_results SET result_key=? WHERE id=?', ('0'*64, saved['result_id']))
    elif tamper == 'kind':
        db.execute("UPDATE reference_results SET result_kind='REFERENCE_QA_RESULT',status='CANNOT_ANSWER' WHERE id=?",
                   (saved['result_id'],))
    elif tamper == 'ledger':
        db.execute('UPDATE model_calls SET response=?', ('{}',))
    elif tamper == 'source':
        path.write_bytes(b'changed synthetic original')
    elif tamper == 'missing_source':
        path.rename(path.with_suffix('.synthetic-backup'))
    elif tamper == 'packet':
        _rehash_record(db, saved, lambda value: value['review_packet']['source_bindings'][0].update(row_ref='X999'))
    elif tamper == 'proof':
        _rehash_record(db, saved, lambda value: value['preview_proof'].update(first_profile_neutral_input_sha256='0'*64))
    elif tamper == 'object_path':
        db.execute('UPDATE documents SET object_key=? WHERE project_id=?', ('../outside.pdf', run['project_id']))
    elif tamper == 'citation':
        document_id = db.one('SELECT id FROM documents WHERE project_id=?', (run['project_id'],))['id']
        db.execute('''INSERT INTO reference_result_citations(
            result_id,ordinal,claim_index,citation_index,citation_type,evidence_id,region_id,
            document_id,page_number,citation_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (saved['result_id'],0,0,0,'TEXT','synthetic',None,document_id,1,'{}'))
    elif tamper == 'review_event':
        db.execute('''INSERT INTO reference_result_review_events(
            id,result_id,actor,action,before_json,after_json,note,created_at) VALUES(?,?,?,?,?,?,?,?)''',
            ('QAREVIEW-'+'8'*32,saved['result_id'],'synthetic','ACCEPTED','{}','{}','','synthetic'))
    touched = []
    monkeypatch.setattr(results, '_build_review_view_from_authenticated_stage', lambda *a, **k: touched.append('builder'))
    monkeypatch.setattr(results, '_render_review_source_from_authenticated_bytes', lambda *a, **k: touched.append('renderer'))
    before, calls = _snapshot(db), len(sent)
    for image in (False, True):
        response = client.get(_url(saved, image))
        assert response.status_code == 409, response.text
        assert 'alpha initial note' not in response.text and str(path) not in response.text
    assert touched == [] and _snapshot(db) == before and len(sent) == calls


def test_authentication_then_source_swap_rejects_before_render(client, review, monkeypatch):
    db, run, saved, sent, store, path = review
    original = results._build_review_view_from_authenticated_stage
    built, rendered = [], []
    def replace_after_auth(*args, **kwargs):
        view = original(*args, **kwargs)
        built.append(True)
        path.write_bytes(b'synthetic replacement after authentication')
        return view
    monkeypatch.setattr(results, '_build_review_view_from_authenticated_stage', replace_after_auth)
    monkeypatch.setattr(rendering, 'render_visual_png', lambda *args: rendered.append(args))
    before, calls = _snapshot(db), len(sent)
    response = client.get(_url(saved, True))
    assert response.status_code == 409 and built == [True] and rendered == []
    assert _snapshot(db) == before and len(sent) == calls


def test_renderer_gets_one_retained_read_not_a_reopened_path(client, review, monkeypatch):
    db, run, saved, sent, store, path = review
    original_pdf = path.read_bytes()
    original_builder = results._build_review_view_from_authenticated_stage
    original_read = Path.read_bytes
    original_render = rendering.render_visual_png
    after_auth, reads, rendered = [], [], []
    def builder(*args, **kwargs):
        view = original_builder(*args, **kwargs)
        after_auth.append(True)
        return view
    def read(target):
        value = original_read(target)
        if after_auth and target == path:
            reads.append(value)
            path.write_bytes(b'replaced after bytes retained')
        return value
    def render(stream, *args):
        assert isinstance(stream, BytesIO)
        rendered.append(stream.getvalue())
        return original_render(stream, *args)
    monkeypatch.setattr(results, '_build_review_view_from_authenticated_stage', builder)
    monkeypatch.setattr(Path, 'read_bytes', read)
    monkeypatch.setattr(rendering, 'render_visual_png', render)
    response = client.get(_url(saved, True))
    assert response.status_code == 200
    assert reads == rendered == [original_pdf]


@pytest.mark.parametrize('ordinal', [0, -1, 2, 999])
def test_nonselected_source_ordinal_has_no_image(client, review, ordinal):
    db, run, saved, sent, store, path = review
    url = _url(saved) + f'/sources/{ordinal}/image'
    before, calls = _snapshot(db), len(sent)
    response = client.get(url)
    assert response.status_code == 404
    assert _snapshot(db) == before and len(sent) == calls


@pytest.mark.parametrize('image', [False, True])
def test_client_cannot_supply_source_geometry_or_path(client, review, monkeypatch, image):
    db, run, saved, sent, store, path = review
    touched = []
    monkeypatch.setattr(client.app.state.reference_results, '_authenticated_projection_review',
                        lambda *args: touched.append(True))
    response = client.get(_url(saved, image) + '?x0=1&path=other.pdf&document_id=another')
    assert response.status_code == 422 and touched == []


@pytest.mark.parametrize('variant', ['v1', 'nonreview'])
def test_unsupported_saved_terminal_has_no_source_view(client, project, tmp_path, variant, monkeypatch):
    responder = _review if variant == 'v1' else lambda envelope: _need(envelope, 'alpha')
    db, run, gateway, loop, sent, store = _formal_store(client, project, tmp_path, responder)
    if variant == 'v1':
        loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V1)
    try:
        route, output = _output(loop, run)
        saved = store.save_projection(run, 'alpha?', output, route)
        before, calls, touched = _snapshot(db), len(sent), []
        monkeypatch.setattr(results, '_build_review_view_from_authenticated_stage', lambda *a, **k: touched.append(True))
        for image in (False, True):
            response = client.get(_url(saved, image))
            assert response.status_code == 409, response.text
        assert touched == [] and _snapshot(db) == before and len(sent) == calls
    finally:
        gateway.close()


def test_source_review_schema_rejects_extra_fields_and_false_approval(client, review):
    from contracts.runtime_rules import validate_schema
    from jsonschema import ValidationError
    db, run, saved, sent, store, path = review
    view = store.get_projection_review_view(saved['result_id'])
    for mutate in (lambda value: value.update(object_key='private'),
                   lambda value: value.update(object_condition_relations_verified=True),
                   lambda value: value['selections'][0].update(claim='invented')):
        changed = deepcopy(view)
        mutate(changed)
        with pytest.raises(ValidationError):
            validate_schema('reference-projection-review-view', changed)


def test_review_capability_opens_saved_case_workflow_but_not_question_creation(client):
    capability = client.get('/api/settings').json()['capabilities']['reference_projection_review']
    assert capability == {'available': True, 'view_version': 'reference-projection-review-view-1',
                          'case_workflow_available': True, 'case_view_version': 'reference-case-view-2',
                          'saved_followup_link_available': True, 'question_creation_available': False}
