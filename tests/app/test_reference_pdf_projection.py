"""Synthetic contract tests for the opt-in native PDF character projection.

These tests deliberately exercise only generated PDFs.  A projected row remains
mechanical provenance, never an engineering object or a verified answer.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas

import app.reference_pdf_projection as projection
from app.reference_pdf_projection import (
    PdfProjectionError,
    authenticate_pdf_projection,
    build_pdf_projection,
)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(path, *, pages=(1,)):
    return build_pdf_projection(path, expected_sha256=_sha(path), page_numbers=list(pages))


def _rows(manifest):
    return [row for page in manifest['pages'] for row in page['rows']]


def _texts(manifest):
    return [row['text'] for row in _rows(manifest)]


def _all_character_ids(manifest):
    bound = [char_id for row in _rows(manifest) for char_id in row['char_ids']]
    unusable = [item['char_id'] for page in manifest['pages'] for item in page['unusable']]
    return bound + unusable


def _self_hash(value):
    body = {key: item for key, item in value.items() if key != 'manifest_sha256'}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _assert_character_accounting(manifest):
    for page in manifest['pages']:
        # Character-level source identity is part of the sidecar, not merely
        # an opaque row membership list: a reviewer must be able to bind each
        # derived row back to its native CTM and CropBox-local geometry.
        characters = page['native_chars']
        assert len(characters) == page['native_char_count']
        assert {item['char_id'] for item in characters} == {
            f'P{page["page_number"]}C{ordinal}' for ordinal in range(1, page['native_char_count'] + 1)
        }
        for character in characters:
            if character['status'] == 'BOUND':
                assert isinstance(character['text'], str)
                assert len(character['matrix']) == 6
                assert len(character['bbox']) == 4
            else:
                assert character['reason'] == 'GEOMETRY_REQUIRES_DIRECTION_METADATA'
        character_text = {item['char_id']: item['text'] for item in characters}
        for row in page['rows']:
            pieces = row['provenance']['pieces']
            assert ''.join(piece['text'] for piece in pieces) == row['text']
            native_ids = [char_id for piece in pieces if piece['kind'] == 'native_chars'
                          for char_id in piece['char_ids']]
            assert native_ids == row['char_ids']
            assert len(native_ids) == len(set(native_ids))
            assert ''.join(character_text[char_id] for char_id in native_ids) == ''.join(
                piece['text'] for piece in pieces if piece['kind'] == 'native_chars')
        ids = [char_id for row in page['rows'] for char_id in row['char_ids']]
        ids.extend(item['char_id'] for item in page['unusable'])
        assert len(ids) == len(set(ids))
        assert len(ids) == page['native_char_count']


def _draw_rotated(drawing, text, x, y, degrees):
    drawing.saveState()
    drawing.translate(x, y)
    drawing.rotate(degrees)
    drawing.drawString(0, 0, text)
    drawing.restoreState()


def _direction_pdf(path):
    drawing = canvas.Canvas(str(path), pagesize=(600, 800))
    # CropBox deliberately excludes this label; it must not leak into a row.
    drawing.setCropBox([100, 100, 500, 700])
    drawing.setFont('Helvetica', 10)
    drawing.drawString(120, 760, 'HIDDEN_OUTSIDE_CROP')
    drawing.drawString(130, 620, 'LEFT_OBJECT 100')
    drawing.drawString(160, 602, 'ONLY_WHEN_EXPOSED')
    drawing.drawString(380, 620, 'RIGHT_OBJECT 100')
    drawing.drawString(380, 602, 'RIGHT_CONDITION_TAIL')
    _draw_rotated(drawing, 'ROT_90', 140, 420, 90)
    _draw_rotated(drawing, 'ROT_180', 300, 390, 180)
    _draw_rotated(drawing, 'ROT_270', 460, 420, 270)
    # These are valid PDF text chars but must fail the narrow cardinal CTM gate.
    drawing.saveState(); drawing.translate(250, 300)
    drawing.transform(-1, 0, 0, 1, 0, 0); drawing.drawString(0, 0, 'MIRROR')
    drawing.restoreState()
    drawing.saveState(); drawing.translate(250, 250)
    drawing.transform(1, 0.25, 0, 1, 0, 0); drawing.drawString(0, 0, 'SHEAR')
    drawing.restoreState()
    drawing.save()


def test_projection_keeps_cardinal_directions_crop_lanes_and_native_character_accounting(tmp_path):
    path = tmp_path / 'cardinal-columns.pdf'
    _direction_pdf(path)

    manifest = _build(path)
    assert manifest['projection_version'] == 'pdf-char-lane-projection-1'
    assert manifest['pdf_sha256'] == _sha(path)
    assert manifest['page_numbers'] == [1]
    assert manifest == _build(path), 'the same source and settings must be byte-deterministic'
    page = manifest['pages'][0]
    assert page['cropbox'] == pytest.approx([100, 100, 500, 700])
    assert 'HIDDEN_OUTSIDE_CROP' not in '\n'.join(_texts(manifest))

    rows = _rows(manifest)
    assert {row['direction_degrees'] for row in rows if row['status'] == 'BOUND'} >= {0, 90, 180, 270}
    # Cardinal direction handling must reconstruct native reading order, not
    # merely tag the geometry with a cardinal degree.
    for label in ('ROT_90', 'ROT_180', 'ROT_270'):
        assert any(label in row['text'] for row in rows), label
    assert any('LEFT_OBJECT 100' in row['text'] and 'ONLY_WHEN_EXPOSED' in row['text'] for row in rows)
    assert any('RIGHT_OBJECT 100' in row['text'] and 'RIGHT_CONDITION_TAIL' in row['text'] for row in rows)
    assert not any('LEFT_OBJECT' in row['text'] and 'RIGHT_OBJECT' in row['text'] for row in rows)
    hundred_rows = [row for row in rows if '100' in row['text']]
    assert len(hundred_rows) >= 2
    assert set(hundred_rows[0]['char_ids']).isdisjoint(hundred_rows[1]['char_ids'])
    assert {item['reason'] for item in page['unusable']} >= {'GEOMETRY_REQUIRES_DIRECTION_METADATA'}
    _assert_character_accounting(manifest)
    assert manifest['semantic_verified'] is False
    assert manifest['object_verified'] is False
    assert manifest['condition_verified'] is False
    assert manifest['answer_complete'] is False


@pytest.mark.parametrize('character_count', [1001, 1801])
def test_long_complete_lane_is_unusable_never_silently_clipped(tmp_path, character_count):
    path = tmp_path / f'long-{character_count}.pdf'
    drawing = canvas.Canvas(str(path), pagesize=(max(2500, character_count * 2), 300))
    drawing.setFont('Helvetica', 1)
    drawing.drawString(10, 200, 'X' * character_count)
    drawing.save()

    manifest = _build(path)
    rows = _rows(manifest)
    assert len(rows) == 1
    assert rows[0]['status'] == 'UNUSABLE'
    assert rows[0]['text'] == 'X' * character_count
    assert rows[0]['reason'] in {'CLAIM_TEXT_LIMIT', 'QUOTE_TEXT_LIMIT'}
    _assert_character_accounting(manifest)


def test_separated_native_tokens_get_an_explicit_synthetic_space_not_a_hidden_text_rewrite(tmp_path):
    path = tmp_path / 'token-gap.pdf'
    drawing = canvas.Canvas(str(path))
    drawing.drawString(72, 700, 'TOKEN')
    drawing.drawString(130, 700, 'BREAK')
    drawing.save()

    manifest = _build(path)
    row = next(row for row in _rows(manifest) if 'TOKEN' in row['text'])
    assert row['text'] == 'TOKEN BREAK'
    assert {'kind': 'synthetic_space', 'text': ' '} in row['provenance']['pieces']
    native_pieces = [piece for piece in row['provenance']['pieces'] if piece['kind'] == 'native_chars']
    assert ''.join(piece['text'] for piece in native_pieces) == 'TOKENBREAK'
    _assert_character_accounting(manifest)


def test_missing_or_nonfinite_character_matrix_is_fail_closed_and_accounted(tmp_path, monkeypatch):
    path = tmp_path / 'matrix.pdf'
    drawing = canvas.Canvas(str(path)); drawing.drawString(72, 700, 'MATRIX'); drawing.save()
    original_open = projection.pdfplumber.open

    def fake_pdf(matrix):
        with original_open(path) as source:
            chars = deepcopy(source.pages[0].chars)
            chars[0]['matrix'] = matrix
            page = SimpleNamespace(chars=chars, cropbox=source.pages[0].cropbox,
                                   rotation=source.pages[0].rotation,
                                   width=source.pages[0].width, height=source.pages[0].height)
        class Pdf:
            pages = [page]
            def __enter__(self): return self
            def __exit__(self, *_args): return False
        return Pdf()

    for matrix in (None, (math.inf, 0, 0, 1, 0, 0)):
        monkeypatch.setattr(projection.pdfplumber, 'open', lambda _input, matrix=matrix: fake_pdf(matrix))
        manifest = _build(path)
        page = manifest['pages'][0]
        assert page['unusable'][0]['reason'] == 'GEOMETRY_REQUIRES_DIRECTION_METADATA'
        _assert_character_accounting(manifest)
    monkeypatch.setattr(projection.pdfplumber, 'open', original_open)


def test_authentication_reextracts_and_rejects_pdf_manifest_row_lane_and_config_tampering(tmp_path):
    path = tmp_path / 'auth.pdf'
    drawing = canvas.Canvas(str(path)); drawing.drawString(72, 700, 'AUTHENTICATED ROW'); drawing.save()
    manifest = _build(path)
    authenticate_pdf_projection(manifest, path, expected_sha256=_sha(path), page_numbers=[1])

    mutations = {
        'character matrix': lambda value: value['pages'][0]['native_chars'][0]['matrix'].__setitem__(0, 99.0),
        'row text': lambda value: value['pages'][0]['rows'][0].__setitem__('text', 'MUTATED'),
        'native character identity': lambda value: value['pages'][0]['rows'][0]['char_ids'].__setitem__(0, 'C999'),
        'derived lane': lambda value: value['pages'][0]['rows'][0]['provenance']['lines'][0].__setitem__('text', 'MUTATED'),
        'config': lambda value: value['algorithm']['config'].__setitem__('line_tolerance_points', 99.0),
    }
    for mutate in mutations.values():
        changed = deepcopy(manifest); mutate(changed); changed['manifest_sha256'] = _self_hash(changed)
        with pytest.raises(PdfProjectionError):
            authenticate_pdf_projection(changed, path, expected_sha256=_sha(path), page_numbers=[1])

    changed_pdf = tmp_path / 'changed.pdf'
    changed_pdf.write_bytes(path.read_bytes() + b'\n% synthetic byte mutation\n')
    with pytest.raises(PdfProjectionError):
        authenticate_pdf_projection(manifest, changed_pdf, expected_sha256=_sha(changed_pdf), page_numbers=[1])


def test_projection_manifest_config_is_not_a_mutable_alias_of_global_extraction_settings(tmp_path):
    path = tmp_path / 'config-isolation.pdf'
    drawing = canvas.Canvas(str(path)); drawing.drawString(72, 700, 'CONFIG ISOLATION'); drawing.save()
    baseline = _build(path)
    original_manifest = deepcopy(baseline)
    original = baseline['algorithm']['config']['line_tolerance_points']
    try:
        # A caller-side sidecar mutation must not alter a later source extraction.
        baseline['algorithm']['config']['line_tolerance_points'] = 999.0
        rebuilt = _build(path)
        assert rebuilt['algorithm']['config']['line_tolerance_points'] == original
        assert rebuilt == original_manifest
    finally:
        baseline['algorithm']['config']['line_tolerance_points'] = original


def test_internal_projection_config_is_immutable_and_current_version_authentication_still_succeeds(tmp_path):
    path = tmp_path / 'immutable-config.pdf'
    drawing = canvas.Canvas(str(path)); drawing.drawString(72, 700, 'IMMUTABLE CONFIG'); drawing.save()
    manifest = _build(path)
    original = projection._CONFIG['line_tolerance_points']
    with pytest.raises(TypeError):
        projection._CONFIG['line_tolerance_points'] = 99.0
    assert projection._CONFIG['line_tolerance_points'] == original
    assert _build(path) == manifest
    authenticate_pdf_projection(manifest, path, expected_sha256=_sha(path), page_numbers=[1])


def test_json_roundtrip_manifest_uses_only_native_chars_public_contract_and_reauthenticates(tmp_path):
    path = tmp_path / 'json-roundtrip.pdf'
    drawing = canvas.Canvas(str(path)); drawing.drawString(72, 700, 'JSON ROUNDTRIP'); drawing.save()
    manifest = _build(path)
    page = manifest['pages'][0]
    assert 'native_chars' in page
    assert 'characters' not in page
    restored = json.loads(json.dumps(manifest, ensure_ascii=False, allow_nan=False))
    authenticate_pdf_projection(restored, path, expected_sha256=_sha(path), page_numbers=[1])


@pytest.mark.parametrize('kind', ['missing_matrix', 'nonfinite_matrix', 'invalid_text'])
def test_bad_middle_native_character_rejects_its_entire_derived_component(tmp_path, monkeypatch, kind):
    path = tmp_path / f'bad-middle-{kind}.pdf'
    drawing = canvas.Canvas(str(path)); drawing.drawString(72, 700, 'ABCDE'); drawing.save()
    original_open = projection.pdfplumber.open

    with original_open(path) as source:
        chars = deepcopy(source.pages[0].chars)
        if kind == 'missing_matrix':
            chars[2]['matrix'] = None
        elif kind == 'nonfinite_matrix':
            chars[2]['matrix'] = (math.inf, 0, 0, 1, 0, 0)
        else:
            chars[2]['text'] = None
        source_page = source.pages[0]
        page = SimpleNamespace(chars=chars, cropbox=source_page.cropbox,
                               rotation=source_page.rotation, width=source_page.width,
                               height=source_page.height)

    class Pdf:
        pages = [page]
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    monkeypatch.setattr(projection.pdfplumber, 'open', lambda _input: Pdf())
    manifest = _build(path)
    affected = {f'P1C{ordinal}' for ordinal in range(1, 6)}
    bound = {char_id for row in _rows(manifest) if row['status'] == 'BOUND' for char_id in row['char_ids']}
    unusable = {item['char_id'] for item in manifest['pages'][0]['unusable']}
    assert affected.isdisjoint(bound)
    assert affected <= unusable
    _assert_character_accounting(manifest)


@pytest.mark.parametrize('matrix', [
    (-1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
    (1.0, 0.25, 0.0, 1.0, 0.0, 0.0),
    (math.sqrt(.5), math.sqrt(.5), -math.sqrt(.5), math.sqrt(.5), 0.0, 0.0),
    (2.0, 0.0, 0.0, 1.0, 0.0, 0.0),
], ids=['mirror', 'shear', 'arbitrary_angle', 'nonuniform_scale'])
def test_unsupported_finite_middle_matrix_rejects_only_its_full_component(tmp_path, monkeypatch, matrix):
    path = tmp_path / 'finite-unsupported-middle.pdf'
    drawing = canvas.Canvas(str(path))
    drawing.drawString(72, 700, 'ABCDE')
    drawing.drawString(72, 650, 'REMOTE_OK')
    drawing.save()
    original_open = projection.pdfplumber.open

    with original_open(path) as source:
        chars = deepcopy(source.pages[0].chars)
        chars[2]['matrix'] = matrix
        source_page = source.pages[0]
        page = SimpleNamespace(chars=chars, cropbox=source_page.cropbox,
                               rotation=source_page.rotation, width=source_page.width,
                               height=source_page.height)

    class Pdf:
        pages = [page]
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    monkeypatch.setattr(projection.pdfplumber, 'open', lambda _input: Pdf())
    manifest = _build(path)
    affected = {f'P1C{ordinal}' for ordinal in range(1, 6)}
    remote = {f'P1C{ordinal}' for ordinal in range(6, 6 + len('REMOTE_OK'))}
    bound = {char_id for row in _rows(manifest) if row['status'] == 'BOUND' for char_id in row['char_ids']}
    unusable = {item['char_id'] for item in manifest['pages'][0]['unusable']}
    assert affected.isdisjoint(bound)
    assert affected <= unusable
    assert remote <= bound
    assert any(row['status'] == 'BOUND' and row['text'] == 'REMOTE_OK' for row in _rows(manifest))
    _assert_character_accounting(manifest)


def test_overlapping_same_direction_columns_are_unusable_not_merged_into_a_bound_lane(tmp_path):
    path = tmp_path / 'ambiguous-columns.pdf'
    drawing = canvas.Canvas(str(path))
    # These painted labels overlap in one horizontal reading band.  It is not
    # safe to invent either a single line or two authoritative lanes.
    drawing.drawString(100, 700, 'OVERLAP_LEFT')
    drawing.drawString(125, 700, 'OVERLAP_RIGHT')
    drawing.save()

    manifest = _build(path)
    rows = _rows(manifest)
    page = manifest['pages'][0]
    overlap_ids = {item['char_id'] for item in page['native_chars'][:len('OVERLAP_LEFTOVERLAP_RIGHT')]}
    bound_ids = {char_id for row in rows if row['status'] == 'BOUND' for char_id in row['char_ids']}
    unusable_ids = {item['char_id'] for item in page['unusable']}
    # Dropping only the physical overlap is unsafe: the unpainted tail would
    # become a fabricated BOUND word.  The whole ambiguous component is out.
    assert overlap_ids.isdisjoint(bound_ids)
    assert overlap_ids <= unusable_ids
    _assert_character_accounting(manifest)


def test_near_same_direction_columns_do_not_remerge_after_line_projection(tmp_path):
    path = tmp_path / 'near-columns.pdf'
    drawing = canvas.Canvas(str(path))
    # The two starts are closer than the lane tolerance, while individual
    # printed lines have a large gap.  It may be split or rejected, but must
    # never become one BOUND cross-column row during a later lane step.
    for y in (700, 680):
        drawing.drawString(100, y, 'L')
        drawing.drawString(150, y, 'R')
    drawing.save()

    manifest = _build(path)
    bound = [row['text'] for row in _rows(manifest) if row['status'] == 'BOUND']
    assert not any('L R' in text or 'LR' in text for text in bound)
    _assert_character_accounting(manifest)


def test_staggered_near_columns_never_merge_after_each_cardinal_transform(tmp_path):
    path = tmp_path / 'staggered-cardinal-columns.pdf'
    drawing = canvas.Canvas(str(path), pagesize=(1000, 1000))
    for degrees in (0, 90, 180, 270):
        drawing.saveState()
        drawing.translate(500, 500)
        drawing.rotate(degrees)
        # Same local geometry in every direction: separate reading-axis bands
        # must not later fuse solely because their lane starts are nearby.
        drawing.drawString(-100, 0, 'LEFT')
        drawing.drawString(-50, -20, 'RIGHT')
        drawing.restoreState()
        drawing.showPage()
    drawing.save()

    manifest = _build(path, pages=(1, 2, 3, 4))
    assert {row['direction_degrees'] for row in _rows(manifest) if row['status'] == 'BOUND'} == {0, 90, 180, 270}
    assert not any(row['status'] == 'BOUND' and 'LEFT' in row['text'] and 'RIGHT' in row['text']
                   for row in _rows(manifest))
    _assert_character_accounting(manifest)
