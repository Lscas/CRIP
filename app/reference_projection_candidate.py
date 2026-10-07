"""Dormant PDF-projection selection prototype; not a runtime answer route.

A projected row is derived text with character provenance, NOT a native PDF
callout or verified engineering relationship. Authentication re-extracts the
selected pages from the original PDF. No old evidence identity is reused.
"""
from __future__ import annotations

import hashlib
import json

from app.project_qa_v2 import validate_answer_v2
from app.reference_extractive import _parts
from app.reference_pdf_projection import authenticate_pdf_projection, build_pdf_projection
from contracts.runtime_rules import validate_schema

CONTRACT_VERSION = 'reference-projection-candidate-1'
_REVIEW = 'OBJECT_CONDITION_RELEVANCE_AND_COMPLETENESS_REQUIRE_REVIEW'
_CODES = frozenset({
    'projection_candidate_manifest_mismatch', 'projection_candidate_row_unknown',
    'projection_candidate_row_duplicate', 'projection_candidate_part_unknown',
    'projection_candidate_part_missing', 'projection_candidate_output_limit',
    'projection_candidate_input_mismatch', 'projection_candidate_question_invalid',
})


class ProjectionCandidateError(ValueError):
    """Safe diagnostics never include source text or provider output."""
    def __init__(self, code: str):
        if code not in _CODES:
            raise ValueError('Unknown projection candidate diagnostic')
        super().__init__(code)
        self.validator = code


def _rows(manifest: dict) -> dict:
    return {row['row_ref']: (page, row) for page in manifest['pages']
            for row in page['rows'] if row['status'] == 'BOUND'}


def _input_hash(manifest: dict, question: str, required_parts: list[dict]) -> str:
    if not isinstance(question, str) or not question.strip():
        raise ProjectionCandidateError('projection_candidate_question_invalid')
    body = {'contract_version': CONTRACT_VERSION,
            'manifest_sha256': manifest['manifest_sha256'],
            'question': question, 'required_parts': required_parts}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def projection_candidate_input(pdf_path, *, expected_sha256: str,
                               page_numbers: list[int], question: str,
                               required_parts: list[dict]):
    """Read the real selected pages; return a private manifest and selection view."""
    _parts(required_parts)
    manifest = build_pdf_projection(pdf_path, expected_sha256=expected_sha256,
                                    page_numbers=page_numbers)
    view = {
        'contract_version': CONTRACT_VERSION,
        'manifest_sha256': manifest['manifest_sha256'],
        'input_sha256': _input_hash(manifest, question, required_parts),
        'question': question,
        'required_parts': [dict(part) for part in required_parts],
        'rows': [{'row_ref': row['row_ref'], 'text': row['text'],
                  'page_number': page['page_number'], 'direction': row['direction']}
                 for page, row in _rows(manifest).values()],
        'limitations': [
            'DERIVED_READING_LANES_NOT_ENGINEERING_OBJECTS',
            'SELECT_WHOLE_ROWS_ONLY_MAX_FOUR',
            _REVIEW,
        ],
    }
    return manifest, view


def compile_projection_candidate(data: dict, manifest: dict, pdf_path, *,
                                 expected_sha256: str, page_numbers: list[int],
                                 question: str, required_parts: list[dict]) -> dict:
    """Copy up to four authenticated whole rows; always require human review.

    The PX evidence IDs belong only to this offline projection and bind the
    manifest and exact row text. They are not saved DB E IDs or model receipts.
    Existing public QA limits and numeric/entity checks remain unchanged.
    """
    validate_schema('reference-projection-candidate', data)
    parts = _parts(required_parts)
    authenticate_pdf_projection(manifest, pdf_path, expected_sha256=expected_sha256,
                                page_numbers=page_numbers)
    if data['manifest_sha256'] != manifest['manifest_sha256']:
        raise ProjectionCandidateError('projection_candidate_manifest_mismatch')
    if data['input_sha256'] != _input_hash(manifest, question, required_parts):
        raise ProjectionCandidateError('projection_candidate_input_mismatch')
    indexed = _rows(manifest)
    coverage = {part: {'part_ref': part, 'claim_indexes': [], 'calculation_indexes': []}
                for part in parts}
    claims = []; evidence = []; bindings = []; seen = set()
    for selection in data['selections']:
        ref = selection['row_ref']
        if ref in seen:
            raise ProjectionCandidateError('projection_candidate_row_duplicate')
        seen.add(ref)
        if ref not in indexed:
            raise ProjectionCandidateError('projection_candidate_row_unknown')
        page, row = indexed[ref]
        text = row['text']
        if not text.strip() or len(text) > 1000:
            raise ProjectionCandidateError('projection_candidate_output_limit')
        for part in selection['part_refs']:
            if part not in coverage:
                raise ProjectionCandidateError('projection_candidate_part_unknown')
            coverage[part]['claim_indexes'].append(len(claims))
        row_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
        evidence_id = f"PX-{manifest['manifest_sha256']}-{ref}-{row_hash[:16]}"
        citation = {'type': 'TEXT', 'evidence_id': evidence_id, 'quote': text}
        claims.append({'text': text, 'citations': [citation]})
        evidence.append({'evidence_id': evidence_id, 'raw_text': text})
        bindings.append({'evidence_id': evidence_id, 'row_ref': ref,
                         'row_text_sha256': row_hash, 'page_number': page['page_number'],
                         'char_ids': list(row['char_ids']), 'direction': row['direction'],
                         'source_basis': 'DERIVED_PDF_CHARACTER_PROJECTION'})
    if any(not item['claim_indexes'] for item in coverage.values()):
        raise ProjectionCandidateError('projection_candidate_part_missing')
    answer = {'status': 'PARTIAL', 'answer': ' '.join(c['text'] for c in claims),
              'claims': claims, 'calculations': [], 'missing': [_REVIEW]}
    validate_answer_v2(answer, evidence, question)
    return {
        'contract_version': CONTRACT_VERSION, 'status': 'REVIEW_REQUIRED',
        'input_sha256': data['input_sha256'],
        'manifest_sha256': manifest['manifest_sha256'], 'pdf_sha256': expected_sha256,
        'answer': answer, 'coverage': list(coverage.values()), 'source_bindings': bindings,
        'verification': {
            'source_excerpt_integrity': True, 'text_direction_verified': True,
            'part_selection_coverage_complete': True,
            'object_condition_relations_verified': False,
            'answer_completeness_verified': False,
        },
    }
