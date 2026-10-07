"""Closed projection decisions: reference selection is never an answer.

The model emits aliases and missing-source actions. Source authentication is a
separate PDF/snapshot boundary; neither schema validity nor P coverage proves
object/condition relationships. Historical E decisions/prompts are unchanged.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.db import dumps
from app.prompt_schema import expand_schema
from app.reference_extractive import _parts
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1, ProjectionProtocol, resolve_projection_protocol
from contracts.runtime_rules import validate_schema

CONTRACT_VERSION = 'project-projection-decision-1'
SELECTOR_VERSION = 'literal-page-selector-10'
CONTEXT_POLICY = 'COMPLETE_SELECTED_PDF_PROJECTION_V1'
_ROOT = Path(__file__).resolve().parents[1]
_REASONS = {
    'REVIEW_REQUIRED': {'OBJECT_CONDITION_REVIEW_REQUIRED'},
    'NEED_EVIDENCE': {'MISSING_SOURCE_TEXT', 'MISSING_IDENTIFIER'},
    'NEED_USER_INPUT': {'MISSING_PROJECT_FILE', 'UNREADABLE_PROJECT_SOURCE'},
    'CANNOT_ANSWER': {'CONFLICTING_SOURCES', 'UNSUPPORTED_TASK', 'NO_NEW_EVIDENCE',
                      'SOURCE_PROJECTION_INCOMPLETE'},
}
_GAPS = {
    'MISSING_SOURCE_TEXT': {'SOURCE_TEXT', 'OBJECT_CONDITION'},
    'MISSING_IDENTIFIER': {'IDENTIFIER'},
    'MISSING_PROJECT_FILE': {'PROJECT_FILE'},
    'UNREADABLE_PROJECT_SOURCE': {'READABLE_SOURCE', 'PROJECTION_GEOMETRY'},
    'CONFLICTING_SOURCES': {'SOURCE_CONFLICT'},
    'UNSUPPORTED_TASK': {'UNSUPPORTED_TASK'},
    'NO_NEW_EVIDENCE': {'LOCAL_RETRIEVAL_EXHAUSTED'},
    'SOURCE_PROJECTION_INCOMPLETE': {'PROJECTION_GEOMETRY'},
}
_V2_REASONS = {
    **_REASONS,
    'CANNOT_ANSWER': {'CONFLICTING_SOURCES', 'UNSUPPORTED_TASK', 'SOURCE_PROJECTION_INCOMPLETE',
                      'INSUFFICIENT_EVIDENCE'},
}
_V2_GAPS = {
    **{key: value for key, value in _GAPS.items() if key != 'NO_NEW_EVIDENCE'},
    'INSUFFICIENT_EVIDENCE': {'SOURCE_TEXT', 'OBJECT_CONDITION', 'IDENTIFIER'},
}
_CODES = frozenset({
    'projection_decision_schema', 'projection_decision_input', 'projection_decision_status',
    'projection_decision_gap', 'projection_decision_request', 'projection_decision_repeat',
    'projection_decision_round_limit', 'projection_decision_conflict',
    'projection_decision_incomplete', 'projection_decision_row',
    'projection_decision_duplicate', 'projection_decision_part',
    'projection_decision_coverage', 'projection_decision_not_review',
})


class ProjectionDecisionError(ValueError):
    """Closed diagnostic codes never include source text or provider output."""
    def __init__(self, code: str):
        if code not in _CODES:
            raise ValueError('Unknown projection decision diagnostic')
        super().__init__(code)
        self.validator = code


def prompt_contract_for(protocol: ProjectionProtocol | str) -> tuple[str, dict, str]:
    """Return the static system/schema identity for an explicitly selected protocol."""
    selected = resolve_projection_protocol(protocol)
    schema = expand_schema(json.loads((_ROOT / 'spec/schemas' / f'{selected.schema_name}.schema.json')
                                       .read_text(encoding='utf-8')))
    prompt = (_ROOT / selected.prompt_path).read_text(encoding='utf-8')
    system_text = prompt + '\nJSON Schema:\n' + dumps(schema)
    return system_text, schema, hashlib.sha256(system_text.encode('utf-8')).hexdigest()


def prompt_contract() -> tuple[str, dict, str]:
    """Historical V1 prompt identity; callers must opt into V2 explicitly."""
    return prompt_contract_for(PROJECTION_PROTOCOL_V1)


def _request_key(request: dict) -> tuple[str, str]:
    return request['tool'], ' '.join(request['query'].split()).casefold()


def validate_projection_decision_structure_for(protocol: ProjectionProtocol | str, data: dict, *,
                                               projection_input_sha256: str,
                                               required_parts: list[dict], source_conflicts=(),
                                               request_history=(), remaining_decisions: int = 3) -> dict:
    """Validate the closed response shape without authenticating projected rows.

    This is deliberately source-free: it validates only the provider contract,
    part references, supplement request syntax, and frozen protocol identity.
    Callers that publish or authenticate source evidence must additionally
    verify row membership, selectability, and projection completeness.
    """
    selected = resolve_projection_protocol(protocol)
    reasons = _REASONS if selected == PROJECTION_PROTOCOL_V1 else _V2_REASONS
    gaps = _GAPS if selected == PROJECTION_PROTOCOL_V1 else _V2_GAPS
    try:
        validate_schema(selected.schema_name, data)
    except Exception as exc:
        raise ProjectionDecisionError('projection_decision_schema') from exc
    if data['projection_input_sha256'] != projection_input_sha256:
        raise ProjectionDecisionError('projection_decision_input')
    status = data['status']
    if data['reason_code'] not in reasons[status]:
        raise ProjectionDecisionError('projection_decision_status')
    if source_conflicts and (
            status != 'CANNOT_ANSWER' or data['reason_code'] != 'CONFLICTING_SOURCES'):
        raise ProjectionDecisionError('projection_decision_conflict')
    if type(remaining_decisions) is not int or not 1 <= remaining_decisions <= 3:
        raise ProjectionDecisionError('projection_decision_round_limit')
    if selected != PROJECTION_PROTOCOL_V1 and data['reason_code'] == 'INSUFFICIENT_EVIDENCE':
        if remaining_decisions != 1:
            raise ProjectionDecisionError('projection_decision_round_limit')
    if status == 'REVIEW_REQUIRED':
        if data['requests'] or data['missing_facts'] or not data['selections']:
            raise ProjectionDecisionError('projection_decision_status')
        required = set(_parts(required_parts))
        covered = set(); seen = set()
        for selection in data['selections']:
            ref = selection['row_ref']
            if ref in seen:
                raise ProjectionDecisionError('projection_decision_duplicate')
            seen.add(ref)
            if not set(selection['part_refs']) <= required:
                raise ProjectionDecisionError('projection_decision_part')
            covered.update(selection['part_refs'])
        if covered != required:
            raise ProjectionDecisionError('projection_decision_coverage')
    else:
        if data['selections']:
            raise ProjectionDecisionError('projection_decision_status')
        if not data['missing_facts']:
            raise ProjectionDecisionError('projection_decision_gap')
        required = set(_parts(required_parts))
        if any(gap['part_ref'] not in required for gap in data['missing_facts']):
            raise ProjectionDecisionError('projection_decision_part')
        if any(gap['gap_code'] not in gaps[data['reason_code']] for gap in data['missing_facts']):
            raise ProjectionDecisionError('projection_decision_gap')
        if status == 'NEED_EVIDENCE':
            if remaining_decisions == 1:
                raise ProjectionDecisionError('projection_decision_round_limit')
            if not data['requests'] or any(len(r['query'].strip()) < 2 for r in data['requests']):
                raise ProjectionDecisionError('projection_decision_request')
            seen = {_request_key(request) for request in request_history}
            for request in data['requests']:
                from app.evidence_loop import _EXPLICIT_IDENTIFIER
                if request['tool'] == 'FIND_IDENTIFIER' and not _EXPLICIT_IDENTIFIER.search(request['query']):
                    raise ProjectionDecisionError('projection_decision_request')
                key = _request_key(request)
                if key in seen:
                    raise ProjectionDecisionError('projection_decision_repeat')
                seen.add(key)
        elif data['requests']:
            raise ProjectionDecisionError('projection_decision_request')
    return json.loads(dumps(data))


def validate_projection_decision_for(protocol: ProjectionProtocol | str, data: dict, bundle, *,
                                     source_conflicts=(), request_history=(),
                                     remaining_decisions: int = 3) -> dict:
    """Validate a provider decision against the exact authenticated input view.

    Callers authenticate bundle before use; this pure validator does not perform
    I/O and does not claim the referenced sources are semantically sufficient.
    remaining_decisions includes the current call (3, 2, 1).
    """
    selected = resolve_projection_protocol(protocol)
    decision = validate_projection_decision_structure_for(
        selected, data, projection_input_sha256=bundle.projection_input_sha256,
        required_parts=bundle.required_parts,
        source_conflicts=source_conflicts or bundle.context.get('source_conflicts'),
        request_history=request_history, remaining_decisions=remaining_decisions)
    if (selected != PROJECTION_PROTOCOL_V1 and decision['reason_code'] == 'INSUFFICIENT_EVIDENCE'
            and bundle.context.get('projection_complete') is not True):
        raise ProjectionDecisionError('projection_decision_incomplete')
    if decision['status'] == 'REVIEW_REQUIRED':
        context = bundle.context
        if context.get('projection_complete') is not True:
            raise ProjectionDecisionError('projection_decision_incomplete')
        indexed = {row['row_ref']: row for row in context['rows']}
        for selection in decision['selections']:
            if (selection['row_ref'] not in indexed
                    or indexed[selection['row_ref']].get('selectable') is not True):
                raise ProjectionDecisionError('projection_decision_row')
    return decision


def validate_projection_decision(data: dict, bundle, *, source_conflicts=(),
                                 request_history=(), remaining_decisions: int = 3) -> dict:
    """Historical V1 validator; V2 requires explicit protocol selection."""
    return validate_projection_decision_for(
        PROJECTION_PROTOCOL_V1, data, bundle, source_conflicts=source_conflicts,
        request_history=request_history, remaining_decisions=remaining_decisions)


def compile_projection_review(data: dict, bundle, *, db, uploads) -> dict:
    """Reauthenticate source and return a reference-only, never-approved handoff.

    This packet is not a public QA answer or a human review record. A future
    explicit UI action may create a case; this function writes nothing.
    """
    from app.reference_projection_input import authenticate_projection_input
    authenticate_projection_input(bundle, db, uploads)
    decision = validate_projection_decision(data, bundle)
    if decision['status'] != 'REVIEW_REQUIRED':
        raise ProjectionDecisionError('projection_decision_not_review')
    indexed = {row['row_ref']: row for row in bundle.context['rows']}
    bindings = [{**indexed[selection['row_ref']]['binding'],
                 'row_ref': selection['row_ref'], 'part_refs': selection['part_refs']}
                for selection in decision['selections']]
    return {
        'packet_version': 'projection-review-packet-1', 'status': 'REVIEW_REQUIRED',
        'answer_basis': 'PROJECTION_REVIEW_REQUIRED',
        'projection_input_sha256': bundle.projection_input_sha256,
        'projection_context_sha256': bundle.projection_context_sha256,
        'source_bindings': bindings,
        'source_text_included': False, 'chain_of_thought_included': False,
        'verification': {
            'source_projection_integrity': True, 'part_selection_coverage_complete': True,
            'object_condition_relations_verified': False, 'answer_completeness_verified': False,
        },
    }
