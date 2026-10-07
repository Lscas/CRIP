"""Synthetic-only candidate tests. No service, database or model calls."""
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import ValidationError
from reportlab.pdfgen.canvas import Canvas

from app.reference_projection_candidate import (
    compile_projection_candidate, projection_candidate_input,
)


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / 'notes.pdf'
    canvas = Canvas(str(path), pagesize=(700, 700), invariant=1)
    canvas.setFont('Helvetica', 10)
    canvas.drawString(50, 600, 'BRACKET A: 2 ANCHORS')
    canvas.drawString(50, 586, 'IF EXPOSED ONLY')
    canvas.drawString(420, 600, 'BRACKET B: 2 ANCHORS')
    canvas.drawString(420, 586, 'AT EACH LEG')
    canvas.save()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    parts = [{'part_ref': 'P1', 'text': 'Show the two complete notes.'}]
    manifest, view = projection_candidate_input(path, expected_sha256=sha,
                                                page_numbers=[1], question='Show the notes.',
                                                required_parts=parts)
    data = {'contract_version': 'reference-projection-candidate-1',
            'manifest_sha256': manifest['manifest_sha256'],
            'input_sha256': view['input_sha256'],
            'selections': [{'row_ref': row['row_ref'], 'part_refs': ['P1']}
                           for row in view['rows']]}
    return path, sha, parts, manifest, view, data


def compile_sample(sample, *, data=None, manifest=None, parts=None):
    path, sha, defaults, source, view, candidate = sample
    return compile_projection_candidate(data if data is not None else candidate,
        manifest if manifest is not None else source, path, expected_sha256=sha,
        page_numbers=[1], question='Show the notes.',
        required_parts=parts if parts is not None else defaults)


def test_copies_complete_rows_and_retains_condition_without_asserting_semantics(sample):
    result = compile_sample(sample)
    assert len(sample[4]['rows']) == 2
    assert result['status'] == 'REVIEW_REQUIRED'
    assert result['answer']['status'] == 'PARTIAL'
    assert result['verification'] == {
        'source_excerpt_integrity': True, 'text_direction_verified': True,
        'part_selection_coverage_complete': True, 'object_condition_relations_verified': False,
        'answer_completeness_verified': False,
    }
    for claim, selected in zip(result['answer']['claims'], sample[4]['rows']):
        assert claim['text'] == selected['text']
        assert len(claim['citations']) == 1
        assert claim['citations'][0]['quote'] == selected['text']
        assert claim['citations'][0]['evidence_id'].startswith('PX-')
    assert 'IF EXPOSED ONLY' in result['answer']['answer']
    assert all(len(b['char_ids']) > 4 for b in result['source_bindings'])


@pytest.mark.parametrize('field', ['text', 'quote', 'value', 'result', 'start', 'end'])
def test_model_cannot_rewrite_quote_or_select_substring(sample, field):
    data = deepcopy(sample[5]); data['selections'][0][field] = '2 at each leg unconditionally'
    with pytest.raises(ValidationError):
        compile_sample(sample, data=data)


@pytest.mark.parametrize('change', ['empty', 'five', 'duplicate', 'unknown', 'part', 'missing', 'hash'])
def test_invalid_selections_fail_closed(sample, change):
    data = deepcopy(sample[5]); parts = deepcopy(sample[2])
    if change == 'empty': data['selections'] = []
    elif change == 'five': data['selections'] = [data['selections'][0]] * 5
    elif change == 'duplicate': data['selections'] *= 2
    elif change == 'unknown': data['selections'][0]['row_ref'] = 'R999'
    elif change == 'part': data['selections'][0]['part_refs'] = ['P99']
    elif change == 'missing': parts.append({'part_ref': 'P2', 'text': 'Another part.'})
    elif change == 'hash': data['manifest_sha256'] = '0' * 64
    with pytest.raises((ValueError, ValidationError)):
        compile_sample(sample, data=data, parts=parts)


def test_authentication_uses_original_pdf_not_self_hash(sample):
    manifest = deepcopy(sample[3])
    manifest['pages'][0]['rows'][0]['text'] = '2 ANCHORS AT BRACKET B IN ALL CONDITIONS'
    with pytest.raises(ValueError):
        compile_sample(sample, manifest=manifest)
    sample[0].write_bytes(sample[0].read_bytes() + b'changed')
    with pytest.raises(ValueError):
        compile_sample(sample)


@pytest.mark.parametrize('change', ['question', 'part_text', 'part_order', 'input_hash'])
def test_candidate_cannot_replay_for_different_question_or_required_parts(sample, change):
    path, sha, parts, manifest, view, data = sample
    parts = deepcopy(parts); data = deepcopy(data); question = 'Show the notes.'
    if change == 'question': question = 'Completely different question.'
    elif change == 'part_text': parts[0]['text'] = 'Apply 2 anchors to an unrelated bracket.'
    elif change == 'part_order':
        # Ordering is identity too, even when the same P references exist.
        parts.append({'part_ref': 'P2', 'text': 'Another selected part.'})
        manifest, view = projection_candidate_input(path, expected_sha256=sha,
            page_numbers=[1], question=question, required_parts=parts)
        data['input_sha256'] = view['input_sha256']
        data['selections'][0]['part_refs'] = ['P1', 'P2']
        parts.reverse()
    else: data['input_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='projection_candidate_input_mismatch'):
        compile_projection_candidate(data, manifest, path, expected_sha256=sha,
            page_numbers=[1], question=question, required_parts=parts)


def test_unrelated_selection_never_claims_semantic_part_mapping(sample):
    path, sha, _, _, _, _ = sample
    parts = [{'part_ref': 'P1', 'text': 'An unrelated object and unknown installation condition.'}]
    manifest, view = projection_candidate_input(path, expected_sha256=sha,
        page_numbers=[1], question='Show evidence for an unrelated object.', required_parts=parts)
    data = {'contract_version': view['contract_version'],
            'manifest_sha256': view['manifest_sha256'], 'input_sha256': view['input_sha256'],
            'selections': [{'row_ref': view['rows'][0]['row_ref'], 'part_refs': ['P1']}]}
    result = compile_projection_candidate(data, manifest, path, expected_sha256=sha,
        page_numbers=[1], question=view['question'], required_parts=parts)
    assert result['verification']['part_selection_coverage_complete'] is True
    assert 'part_mapping_complete' not in result['verification']
    assert result['verification']['object_condition_relations_verified'] is False
    assert result['verification']['answer_completeness_verified'] is False


def test_valid_input_commitment_still_requires_every_part_to_be_selected(sample):
    path, sha, parts, _, _, _ = sample
    parts = parts + [{'part_ref': 'P2', 'text': 'A second requested selection.'}]
    manifest, view = projection_candidate_input(path, expected_sha256=sha,
        page_numbers=[1], question='Show both notes.', required_parts=parts)
    data = {'contract_version': view['contract_version'],
            'manifest_sha256': view['manifest_sha256'], 'input_sha256': view['input_sha256'],
            'selections': [{'row_ref': view['rows'][0]['row_ref'], 'part_refs': ['P1']}]}
    with pytest.raises(ValueError, match='projection_candidate_part_missing'):
        compile_projection_candidate(data, manifest, path, expected_sha256=sha,
            page_numbers=[1], question=view['question'], required_parts=parts)


def test_default_runtime_never_imports_projection_or_candidate():
    root = Path(__file__).resolve().parents[2]
    for name in ('main.py', 'gateway.py', 'evidence_loop.py', 'parsers.py', 'runner.py',
                 'reference_evaluation_jobs.py', 'page_selector.py'):
        code = (root / 'app' / name).read_text(encoding='utf-8')
        assert 'reference_pdf_projection' not in code
        assert 'reference_projection_candidate' not in code
