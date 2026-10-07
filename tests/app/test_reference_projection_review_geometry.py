"""Pure derivation/actual rendering tests; NOT end-to-end auth acceptance."""
from copy import deepcopy
import hashlib
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfdoc import PDFArray

from app.db import DomainError
from app.reference_pdf_projection import build_pdf_projection
from app.reference_projection_stage import _row_values
from app.visual_pipeline import render_visual_png


from app import reference_projection_review as module
build = module._build_review_view_from_authenticated_stage


def _fixture(rotation, cropped=True):
    buffer = BytesIO()
    drawing = canvas.Canvas(buffer, pagesize=(600, 800))
    if cropped:
        drawing.setCropBox([100, 100, 500, 700])
    drawing.setFillColorRGB(1, 0, 0)
    drawing.rect(145, 342, 175, 35, fill=1, stroke=0)
    drawing.setFillColorRGB(0, 0, 0)
    drawing.setFont('Helvetica', 16)
    drawing.drawString(150, 350, 'TARGET_ROW')
    drawing.setFillColorRGB(0, 0, 1)
    drawing.rect(145, 542, 175, 35, fill=1, stroke=0)
    drawing.setFillColorRGB(1, 1, 1)
    drawing.drawString(150, 550, 'DECOY_ROW')
    drawing.showPage()
    # Synthetic fixture explicitly freezes MediaBox before adding PDF /Rotate.
    page = drawing._doc.Pages.pages[-1]
    page.MediaBox = PDFArray([0, 0, 600, 800])
    page.Rotate = rotation
    drawing.save()
    raw = buffer.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    manifest = build_pdf_projection(raw, expected_sha256=sha, page_numbers=[1])
    ref = {'document_id': 'synthetic-doc', 'pdf_sha256': sha,
           'page_numbers': [1], 'manifest_sha256': manifest['manifest_sha256'],
           'object_key': 'not-returned'}
    rows, unused = _row_values(manifest, ref, 0)
    selected = next(row for row in rows if row['text'] == 'TARGET_ROW')
    assert selected['selectable'] and not unused
    stage = SimpleNamespace(run_id='run', snapshot_id='snapshot', question='Target condition?',
                            projection_input_sha256='a' * 64,
                            projection_context_sha256='b' * 64,
                            context={'rows': rows}, manifests=[manifest], rebuild_refs=[ref])
    record = {'result_kind': 'PROJECTION_LOOP_OUTCOME',
              'projection_execution_version': 'reference-projection-execution-2',
              'loop_version': 'project-projection-loop-2', 'status': 'REVIEW_REQUIRED',
              'run_id': stage.run_id, 'snapshot_id': stage.snapshot_id,
              'question': stage.question,
              'review_packet': {'packet_version': 'projection-review-packet-2',
                                'projection_input_sha256': stage.projection_input_sha256,
                                'projection_context_sha256': stage.projection_context_sha256,
                                'source_bindings': [{**selected['binding'],
                                                     'row_ref': selected['row_ref'],
                                                     'part_refs': ['P1']}]}}
    return raw, stage, record


def _view(stage, record):
    return build(record, stage, result_id='result', result_hash='c' * 64,
                 document_names={'synthetic-doc': 'synthetic.pdf'})


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('cropped', [False, True])
def test_actual_renderer_finds_target_not_decoy(tmp_path, rotation, cropped):
    raw, stage, record = _fixture(rotation, cropped)
    view = _view(stage, record)
    selection = view['selections'][0]
    geometry = selection['geometry']
    path = tmp_path / 'synthetic-rotation.pdf'
    path.write_bytes(raw)
    png, width, height, coordinate_system = render_visual_png(
        path, path.name, 1, geometry['render_crop_bbox'])
    image = Image.open(BytesIO(png)).convert('RGB')
    pixels = list(image.getdata())
    red = sum(r > 180 and g < 70 and b < 70 for r, g, b in pixels)
    blue = sum(b > 180 and r < 70 and g < 70 for r, g, b in pixels)
    assert red / len(pixels) > .55 and blue == 0
    assert (width, height) == (geometry['page_width'], geometry['page_height'])
    assert coordinate_system == geometry['coordinate_system']
    assert geometry['rotation'] == rotation
    assert selection['text'] == 'TARGET_ROW'
    assert 'object_key' not in str(view) and 'DECOY_ROW' not in str(view)
    assert view['object_condition_relations_verified'] is False
    assert view['answer_completeness_verified'] is False
    assert _view(stage, record) == view
    view['selections'][0]['char_ids'].append('mutated-client-copy')
    assert 'mutated-client-copy' not in str(stage)


@pytest.mark.parametrize('field', ['document_id', 'pdf_sha256', 'manifest_sha256',
                                   'page_number', 'source_row_ref', 'row_text_sha256',
                                   'direction', 'char_ids', 'row_ref'])
def test_binding_change_is_rejected(field):
    _, stage, record = _fixture(0)
    record['review_packet']['source_bindings'][0][field] = 'tampered'
    with pytest.raises(DomainError) as rejected:
        _view(stage, record)
    assert rejected.value.code == 409


@pytest.mark.parametrize('target', ['text', 'text_sha256', 'direction', 'char_ids', 'bbox', 'cropbox'])
def test_manifest_or_row_change_is_rejected(target):
    _, stage, record = _fixture(90)
    page = stage.manifests[0]['pages'][0]
    selected = next(row for row in page['rows'] if row['text'] == 'TARGET_ROW')
    if target == 'cropbox':
        page['cropbox'] = [0, 0, 1, 1]
    elif target == 'bbox':
        selected['bbox'] = [0, float('nan'), 100, 200]
    else:
        selected[target] = 'tampered'
    with pytest.raises(DomainError) as rejected:
        _view(stage, record)
    assert rejected.value.code == 409


def test_duplicate_row_or_manifest_is_rejected():
    _, stage, record = _fixture(270)
    selected = next(row for row in stage.context['rows'] if row['text'] == 'TARGET_ROW')
    stage.context['rows'].append(deepcopy(selected))
    with pytest.raises(DomainError):
        _view(stage, record)
    stage.context['rows'].pop()
    stage.manifests.append(deepcopy(stage.manifests[0]))
    stage.rebuild_refs.append(deepcopy(stage.rebuild_refs[0]))
    with pytest.raises(DomainError):
        _view(stage, record)


def test_view_identity_binds_full_text_and_result():
    _, stage, record = _fixture(0)
    before = _view(stage, record)
    after = build(record, stage, result_id='different-result', result_hash='d' * 64,
                  document_names={'synthetic-doc': 'synthetic.pdf'})
    assert before['review_view_sha256'] != after['review_view_sha256']


def test_source_swap_is_rejected_before_any_render(monkeypatch):
    raw, stage, record = _fixture(0)
    source = _view(stage, record)['selections'][0]
    other, _, _ = _fixture(90)
    called = []
    monkeypatch.setattr(module, 'render_visual_png', lambda *args: called.append(args))
    with pytest.raises(DomainError, match='source bytes changed'):
        module._render_review_source_from_authenticated_bytes(other, source)
    assert called == []
    with pytest.raises(DomainError, match='source bytes changed'):
        module._render_review_source_from_authenticated_bytes(bytearray(raw), source)
    assert called == []


def test_render_uses_same_bytes_without_reopening_replaced_path(tmp_path, monkeypatch):
    raw, stage, record = _fixture(270)
    source = _view(stage, record)['selections'][0]
    path = tmp_path / 'synthetic-toctou.pdf'
    path.write_bytes(raw)
    retained = path.read_bytes()
    other, _, _ = _fixture(0)
    path.write_bytes(other)
    seen = []
    original = module.render_visual_png
    def observe(stream, *args):
        assert isinstance(stream, BytesIO)
        seen.append(stream.getvalue())
        return original(stream, *args)
    monkeypatch.setattr(module, 'render_visual_png', observe)
    image = module._render_review_source_from_authenticated_bytes(retained, source)
    assert seen == [raw] and Image.open(BytesIO(image[0])).width > 0
    with pytest.raises(DomainError, match='source bytes changed'):
        module._render_review_source_from_authenticated_bytes(path.read_bytes(), source)
    assert seen == [raw]


def test_render_cannot_return_wrong_coordinate_extent(monkeypatch):
    raw, stage, record = _fixture(90)
    source = _view(stage, record)['selections'][0]
    monkeypatch.setattr(module, 'render_visual_png', lambda *args: (b'png', 1, 1, 'wrong'))
    with pytest.raises(DomainError):
        module._render_review_source_from_authenticated_bytes(raw, source)


def test_complete_long_source_row_is_not_clipped():
    # This is a pure-derivation test, not a substitute for source-authentication.
    # Use a real native PDF row past historical display/quote limits.
    text = 'COMPLETE_CONDITION_' + 'X' * 2001
    buffer = BytesIO()
    drawing = canvas.Canvas(buffer, pagesize=(len(text) * 7 + 100, 200))
    drawing.setFont('Helvetica', 9)
    drawing.drawString(20, 100, text)
    drawing.save()
    raw = buffer.getvalue()
    _, stage, record = _fixture(0)
    sha = hashlib.sha256(raw).hexdigest()
    manifest = build_pdf_projection(raw, expected_sha256=sha, page_numbers=[1])
    ref = {'document_id': 'synthetic-doc', 'pdf_sha256': sha, 'page_numbers': [1],
           'manifest_sha256': manifest['manifest_sha256'], 'object_key': 'not-returned'}
    rows, unused = _row_values(manifest, ref, 0)
    assert len(rows) == 1 and rows[0]['selectable'] and not unused
    stage.context = {'rows': rows}
    stage.manifests, stage.rebuild_refs = [manifest], [ref]
    record['review_packet']['source_bindings'] = [
        {**rows[0]['binding'], 'row_ref': rows[0]['row_ref'], 'part_refs': ['P1']}]
    source = _view(stage, record)['selections'][0]
    assert source['text'] == text
    assert len(source['char_ids']) == len(text)
    assert source['row_text_sha256'] == hashlib.sha256(text.encode()).hexdigest()
