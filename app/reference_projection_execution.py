"""Source-text-free projection execution authentication, without persistence.

This module performs no HTTP, Settings access, writes, migration, or public
publication. A future store must authenticate a compiled record before saving.
"""
from __future__ import annotations

import hashlib
import json
import re

from jsonschema import ValidationError

from app.db import DomainError, dumps, paid_task_key
from app.evidence_loop import _answer_parts
from app.page_selector import select_pages
from app.reference_input_commitment import VERSION as COMMITMENT_VERSION
from app.reference_input_commitment import require_ledger_profile, sha256 as commitment_sha256
from app.reference_projection_decision import prompt_contract_for, validate_projection_decision_for
from app.reference_projection_input import prepare_projection_input
from app.reference_projection_loop_receipt import authenticate_projection_loop_receipt, build_loop_user_text, request_history
from app.reference_projection_preview import projection_preview_proof
from app.reference_projection_protocol import (
    PROJECTION_PROTOCOL_V1, PROJECTION_PROTOCOL_V2, ProjectionProtocol,
)
from app.reference_projection_stage import append_projection_stage, prepare_projection_stage
from app.reference_text_profiles import route as canonical_route
from contracts.runtime_rules import validate_schema


EXECUTION_VERSION = 'reference-projection-execution-1'
EXECUTION_VERSION_V2 = 'reference-projection-execution-2'
RESULT_KIND = 'PROJECTION_LOOP_OUTCOME'
_POLICY = 'RUN_SNAPSHOT_APPEND_ONLY'
_RECORD_KEYS = {
    'projection_execution_version', 'result_kind', 'qa_version', 'feature',
    'selector_version', 'loop_version', 'run_id', 'snapshot_id', 'question',
    'question_key', 'status', 'reason_code', 'answer', 'claims', 'calculations',
    'missing', 'answer_basis', 'source_scope', 'execution_profile', 'preview_proof',
    'execution_receipts', 'decision_count', 'supplement_round_count',
    'accepted_supplement_request_count', 'projection_row_count', 'review_packet',
    'source_text_included', 'provider_output_included', 'chain_of_thought_included',
}


def _protocol_for_execution(*, execution_version: object, loop_version: object,
                            receipt_version: str | None = None,
                            proof_version: object | None = None) -> ProjectionProtocol:
    """Resolve durable identity only; never select a protocol from Settings."""
    for protocol in (PROJECTION_PROTOCOL_V1, PROJECTION_PROTOCOL_V2):
        if (execution_version == protocol.execution_version
                and loop_version == protocol.loop_version
                and (receipt_version is None or receipt_version == protocol.receipt_version)
                and (proof_version is None or proof_version == protocol.proof_version)):
            return protocol
    raise DomainError('Projection execution protocol identities are mixed or unsupported.', 409)
_RUNTIME_KEYS = {
    'qa_version', 'feature', 'loop_version', 'run_id', 'question', 'status', 'reason_code',
    'answer', 'claims', 'calculations', 'missing', 'answer_basis', 'source_scope',
    'model_called', 'model_call_count', 'decision_count', 'supplement_round_count',
    'accepted_supplement_request_count', 'projection_row_count', 'execution_profile',
    'execution_receipts', 'decision_trace', 'review_packet', 'preview_proof',
}
_HASH = re.compile(r'^[0-9a-f]{64}$')
_PART = re.compile(r'^P[1-9][0-9]*$')
_CODE = re.compile(r'^[A-Z][A-Z0-9_]{1,63}$')
_GAP_CODES = {
    'SOURCE_TEXT', 'IDENTIFIER', 'PROJECT_FILE', 'READABLE_SOURCE', 'OBJECT_CONDITION',
    'SOURCE_CONFLICT', 'UNSUPPORTED_TASK', 'LOCAL_RETRIEVAL_EXHAUSTED', 'PROJECTION_GEOMETRY',
}
_TERMINAL_STATUSES = {'REVIEW_REQUIRED', 'CANNOT_ANSWER', 'NEED_USER_INPUT'}
_STATUS_REASONS = {
    'REVIEW_REQUIRED': {'OBJECT_CONDITION_REVIEW_REQUIRED'},
    'CANNOT_ANSWER': {'CONFLICTING_SOURCES', 'UNSUPPORTED_TASK', 'NO_NEW_EVIDENCE',
                      'SOURCE_PROJECTION_INCOMPLETE'},
    'NEED_USER_INPUT': {'MISSING_PROJECT_FILE', 'UNREADABLE_PROJECT_SOURCE'},
}


def _status_reasons(protocol: ProjectionProtocol) -> dict[str, set[str]]:
    if protocol == PROJECTION_PROTOCOL_V1:
        return _STATUS_REASONS
    return {
        **_STATUS_REASONS,
        'CANNOT_ANSWER': {'CONFLICTING_SOURCES', 'UNSUPPORTED_TASK',
                          'SOURCE_PROJECTION_INCOMPLETE', 'INSUFFICIENT_EVIDENCE',
                          # This one is application-produced only and is checked
                          # against an authenticated zero-page append below.
                          'NO_NEW_EVIDENCE'},
    }


def _canonical(value: object) -> str:
    """Strict JSON comparison: keys are semantic-neutral; bool is never int."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DomainError('Projection execution value is not strict JSON.', 409) from exc


def _copy(value: object):
    return json.loads(_canonical(value))


def _strict_equal(left: object, right: object) -> bool:
    return _canonical(left) == _canonical(right)


def _question_key(question: str) -> str:
    if not isinstance(question, str):
        raise DomainError('Projection execution question is malformed.', 409)
    normalized = ' '.join(question.split())
    if not 3 <= len(normalized) <= 1000:
        raise DomainError('Projection execution question is malformed.', 409)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def _initial_stage(db, uploads, run: dict, question: str):
    if not isinstance(run, dict) or not isinstance(run.get('id'), str):
        raise DomainError('Projection execution run is malformed.', 409)
    stored = db.one('SELECT * FROM runs WHERE id=?', (run['id'],), False)
    if (stored is None or stored.get('project_id') != run.get('project_id')
            or stored.get('snapshot_id') != run.get('snapshot_id')
            or stored.get('status') not in {'PARTIAL', 'COMPLETED'}):
        raise DomainError('Projection execution run is not a persisted reference run.', 409)
    try:
        capabilities = json.loads(stored['capabilities'])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DomainError('Projection execution run capabilities are malformed.', 409) from exc
    if not isinstance(capabilities, dict) or capabilities.get('analysis_mode') != 'REFERENCE_QA':
        raise DomainError('Projection execution run is outside reference QA.', 409)
    selection = select_pages(db, stored, question, selector_version='literal-page-selector-9')
    bundle = prepare_projection_input(
        db, uploads, stored, selection, question, _answer_parts(question))
    return prepare_projection_stage(bundle, db, uploads)


def _source_scope(stage) -> dict:
    return {
        'snapshot_id': stage.snapshot_id,
        'policy': _POLICY,
        'conflicts': stage.context['source_conflicts'],
    }


def _review_packet(stage, decision: dict) -> dict:
    rows = {row['row_ref']: row for row in stage.context['rows']}
    return {
        'packet_version': 'projection-review-packet-2',
        'status': 'REVIEW_REQUIRED',
        'answer_basis': 'PROJECTION_REVIEW_REQUIRED',
        'projection_input_sha256': stage.projection_input_sha256,
        'projection_context_sha256': stage.projection_context_sha256,
        'source_bindings': [{**rows[item['row_ref']]['binding'], **item}
                            for item in decision['selections']],
        'source_text_included': False,
        'chain_of_thought_included': False,
        'verification': {
            'source_projection_integrity': True,
            'part_selection_coverage_complete': True,
            'object_condition_relations_verified': False,
            'answer_completeness_verified': False,
        },
    }


def _canonical_profile(route: dict) -> dict:
    canonical = canonical_route(route)
    if canonical is None or not _strict_equal(canonical, route):
        raise DomainError('Projection execution requires a canonical named profile.', 409)
    return canonical


def _safe_terminal_diagnostic(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    allowed = {'kind', 'class', 'exception', 'path', 'validator', 'semantic_detail'}
    if set(value) - allowed or value.get('kind') != 'CONTRACT_ERROR' or value.get('class') != 'PROJECT_PROJECTION_DECISION':
        return False
    import re
    from app.answer_diagnostics import valid_numeric_diagnostic
    safe_code = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
    safe_path = re.compile(r'^[A-Za-z0-9_.-]{0,160}$')
    return (all(isinstance(value[key], str)
                and (safe_path if key == 'path' else safe_code).fullmatch(value[key])
                for key in ('exception', 'path', 'validator') if key in value)
            and ('semantic_detail' not in value or valid_numeric_diagnostic(value)))


def _ledger_call(db, run: dict, receipt: dict, route: dict, *, task: str, failure: bool) -> dict:
    call = db.one('''SELECT project_id,run_id,task_key,request_hash,model,state,actual_units,
                            response,error,reference_input_commitment_version,
                            reference_input_commitment_sha256
                     FROM model_calls WHERE id=?''', (receipt['model_call_id'],), False)
    if (call is None or call['project_id'] != run['project_id'] or call['run_id'] != run['id']
            or call['task_key'] != task or call['request_hash'] != receipt['request_hash']
            or call['model'] != receipt['model'] or call['actual_units'] is None):
        raise DomainError('Projection execution receipt ledger identity is inconsistent.', 409)
    require_ledger_profile(call, route['profile_id'])
    if (call['reference_input_commitment_version'] != COMMITMENT_VERSION
            or call['reference_input_commitment_sha256'] != commitment_sha256(route, receipt)):
        raise DomainError('Projection execution receipt commitment is inconsistent.', 409)
    if not failure:
        if call['state'] != 'SETTLED' or call['response'] is None or call['error'] is not None:
            raise DomainError('Projection execution decision is not a settled response.', 409)
        try:
            return json.loads(call['response'])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DomainError('Projection execution settled response is malformed.', 409) from exc
    if call['state'] != 'SETTLED_ERROR' or call['response'] is not None:
        raise DomainError('Projection execution failure terminal is inconsistent.', 409)
    try:
        diagnostic = json.loads(call['error'] or '')
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DomainError('Projection execution failure diagnostic is malformed.', 409) from exc
    if not _safe_terminal_diagnostic(diagnostic):
        raise DomainError('Projection execution failure diagnostic is unsafe.', 409)
    return diagnostic


def _authenticate_chain(db, uploads, run: dict, question: str, route: dict, receipts: list[dict],
                        *, protocol: ProjectionProtocol, final_failure: bool = False):
    """Rebuild local stages and ledger-authorize every persisted receipt."""
    if not isinstance(receipts, list) or not 1 <= len(receipts) <= 3:
        raise DomainError('Projection execution receipt chain is malformed.', 409)
    stage = _initial_stage(db, uploads, run, question)
    proof = projection_preview_proof(run, stage, route, protocol=protocol)
    chain: list[dict] = []
    accepted = 0
    positive_rounds = 0
    terminal_decision = None
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict) or type(receipt.get('cached')) is not bool:
            raise DomainError('Projection execution receipt cache observation is malformed.', 409)
        validate_schema('reference-projection-loop-receipt' if protocol == PROJECTION_PROTOCOL_V1
                        else 'reference-projection-loop-receipt-v2', receipt)
        if receipt.get('receipt_version') != protocol.receipt_version:
            raise DomainError('Projection execution receipt protocol is inconsistent.', 409)
        if receipt.get('round') != index + 1:
            raise DomainError('Projection execution receipt rounds are not contiguous.', 409)
        system_text, _, _ = prompt_contract_for(protocol)
        user_text = build_loop_user_text(stage, chain, protocol=protocol)
        authenticate_projection_loop_receipt(
            receipt, stage, prior_chain=chain, system_text=system_text, user_text=user_text,
            route=route, protocol=protocol)
        task = paid_task_key(f'answer-v3-r{stage.round_index}:{receipt["request_hash"]}', 0)
        is_failure = final_failure and index == len(receipts) - 1
        payload = _ledger_call(db, run, receipt, route, task=task, failure=is_failure)
        if is_failure:
            return stage, chain, accepted, positive_rounds, proof, payload
        decision = validate_projection_decision_for(
            protocol, payload, stage, source_conflicts=stage.context['source_conflicts'],
            request_history=request_history(chain, protocol=protocol),
            remaining_decisions=3 - stage.round_index)
        terminal_decision = decision
        if decision['status'] == 'NEED_EVIDENCE':
            if index == len(receipts) - 1:
                next_stage = append_projection_stage(stage, decision['requests'], db, uploads)
                accepted += len(decision['requests'])
                # A final no-new batch was accepted and locally executed even
                # though it selected zero pages, so it remains a supplement
                # round.  It is never reclassified as newly acquired evidence.
                return next_stage, chain + [{'receipt': receipt, 'decision': decision}], accepted, positive_rounds + 1, proof, decision
            next_stage = append_projection_stage(stage, decision['requests'], db, uploads)
            if next_stage.added_page_count <= 0:
                raise DomainError('Projection execution has an unauthorised no-new intermediate stage.', 409)
            accepted += len(decision['requests'])
            positive_rounds += 1
            chain.append({'receipt': receipt, 'decision': decision})
            stage = next_stage
            continue
        if index != len(receipts) - 1:
            raise DomainError('Projection execution terminal decision has trailing receipts.', 409)
        return stage, chain, accepted, positive_rounds, proof, decision
    raise DomainError('Projection execution chain has no terminal decision.', 409)


def _check_missing(value: object) -> None:
    if not isinstance(value, list) or len(value) > 12:
        raise DomainError('Projection execution missing facts are malformed.', 409)
    seen = set()
    for item in value:
        if (not isinstance(item, dict) or set(item) != {'part_ref', 'gap_code'}
                or not isinstance(item.get('part_ref'), str) or not _PART.fullmatch(item['part_ref'])
                or item.get('gap_code') not in _GAP_CODES):
            raise DomainError('Projection execution missing facts are malformed.', 409)
        key = (item['part_ref'], item['gap_code'])
        if key in seen:
            raise DomainError('Projection execution missing facts are malformed.', 409)
        seen.add(key)


def _check_source_scope(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {'snapshot_id', 'policy', 'conflicts'}:
        raise DomainError('Projection execution source scope is malformed.', 409)
    if (not isinstance(value['snapshot_id'], str) or not value['snapshot_id']
            or value['policy'] != _POLICY or not isinstance(value['conflicts'], list)
            # Conflict labels are existing selector diagnostics, not closed model
            # codes; authority comes from the source replay below.
            or any(not isinstance(item, str) or not item or len(item) > 512
                   for item in value['conflicts'])):
        raise DomainError('Projection execution source scope is malformed.', 409)


def _check_proof(value: object, run: dict, question: str, route: dict,
                 protocol: ProjectionProtocol) -> None:
    required = {
        'proof_version', 'project_id', 'run_id', 'snapshot_id', 'question_sha256', 'selector_version',
        'loop_version', 'context_policy', 'profile_version', 'profile_id', 'route_sha256',
        'prompt_contract_hash', 'initial_projection_input_sha256',
        'initial_projection_context_sha256', 'initial_ordered_projection_manifest_sha256',
        'first_profile_neutral_input_sha256',
    }
    hashes = {'question_sha256', 'route_sha256', 'prompt_contract_hash',
              'initial_projection_input_sha256', 'initial_projection_context_sha256',
              'initial_ordered_projection_manifest_sha256', 'first_profile_neutral_input_sha256'}
    if not isinstance(value, dict) or set(value) != required:
        raise DomainError('Projection execution proof is malformed.', 409)
    if any(not isinstance(item, str) or not item for item in value.values()):
        raise DomainError('Projection execution proof is malformed.', 409)
    if any(not _HASH.fullmatch(value[key]) for key in hashes):
        raise DomainError('Projection execution proof is malformed.', 409)
    _system_text, _, prompt_hash = prompt_contract_for(protocol)
    expected = {
        'proof_version': 'reference-preview-proof-2', 'project_id': run['project_id'],
        'run_id': run['id'], 'snapshot_id': run['snapshot_id'],
        'question_sha256': hashlib.sha256(question.encode('utf-8')).hexdigest(),
        'selector_version': protocol.selector_version, 'loop_version': protocol.loop_version,
        'context_policy': protocol.context_policy, 'profile_version': route['profile_version'],
        'profile_id': route['profile_id'],
        'route_sha256': hashlib.sha256(dumps(route).encode('utf-8')).hexdigest(),
        'prompt_contract_hash': prompt_hash,
    }
    expected['proof_version'] = protocol.proof_version
    if any(value[key] != expected[key] for key in expected):
        raise DomainError('Projection execution proof identity is inconsistent.', 409)


def _check_receipts(value: object, protocol: ProjectionProtocol) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise DomainError('Projection execution receipts are malformed.', 409)
    for receipt in value:
        if not isinstance(receipt, dict) or type(receipt.get('cached')) is not bool:
            raise DomainError('Projection execution receipts are malformed.', 409)
        try:
            validate_schema('reference-projection-loop-receipt' if protocol == PROJECTION_PROTOCOL_V1
                            else 'reference-projection-loop-receipt-v2', receipt)
        except ValidationError as exc:
            raise DomainError('Projection execution receipts are malformed.', 409) from exc


def _check_review_packet(value: object) -> None:
    if value is None:
        return
    required = {
        'packet_version', 'status', 'answer_basis', 'projection_input_sha256',
        'projection_context_sha256', 'source_bindings', 'source_text_included',
        'chain_of_thought_included', 'verification',
    }
    binding_keys = {
        'document_id', 'pdf_sha256', 'page_number', 'manifest_sha256', 'projection_version',
        'source_row_ref', 'row_text_sha256', 'direction', 'char_ids', 'row_ref', 'part_refs',
    }
    verification = {
        'source_projection_integrity': True, 'part_selection_coverage_complete': True,
        'object_condition_relations_verified': False, 'answer_completeness_verified': False,
    }
    if (not isinstance(value, dict) or set(value) != required
            or value.get('packet_version') != 'projection-review-packet-2'
            or value.get('status') != 'REVIEW_REQUIRED'
            or value.get('answer_basis') != 'PROJECTION_REVIEW_REQUIRED'
            or type(value.get('source_text_included')) is not bool
            or value['source_text_included'] is not False
            or type(value.get('chain_of_thought_included')) is not bool
            or value['chain_of_thought_included'] is not False
            or not _strict_equal(value.get('verification'), verification)
            or not isinstance(value.get('projection_input_sha256'), str)
            or not _HASH.fullmatch(value['projection_input_sha256'])
            or not isinstance(value.get('projection_context_sha256'), str)
            or not _HASH.fullmatch(value['projection_context_sha256'])
            or not isinstance(value.get('source_bindings'), list)):
        raise DomainError('Projection execution review packet is malformed.', 409)
    for binding in value['source_bindings']:
        if not isinstance(binding, dict) or set(binding) != binding_keys:
            raise DomainError('Projection execution review packet is malformed.', 409)
        direction = binding.get('direction')
        if (not all(isinstance(binding[key], str) and binding[key]
                    for key in ('document_id', 'projection_version', 'source_row_ref', 'row_ref'))
                or not all(_HASH.fullmatch(binding[key])
                           for key in ('pdf_sha256', 'manifest_sha256', 'row_text_sha256'))
                or type(binding['page_number']) is not int or binding['page_number'] < 1
                or not isinstance(direction, dict)
                or set(direction) != {'degrees', 'reading_axis', 'line_axis'}
                or type(direction.get('degrees')) is not int
                or direction['degrees'] not in {0, 90, 180, 270}
                or not all(isinstance(direction[key], str) and direction[key]
                           for key in ('reading_axis', 'line_axis'))
                or not isinstance(binding['char_ids'], list)
                or any(not isinstance(item, str) or not item for item in binding['char_ids'])
                or not isinstance(binding['part_refs'], list)
                or any(not isinstance(item, str) or not _PART.fullmatch(item) for item in binding['part_refs'])):
            raise DomainError('Projection execution review packet is malformed.', 409)


def _check_runtime_output(run: dict, question: str, route: dict, output: object,
                          protocol: ProjectionProtocol) -> None:
    if not isinstance(output, dict) or set(output) != _RUNTIME_KEYS:
        raise DomainError('Projection execution runtime output has an unsupported shape.', 409)
    if (output.get('qa_version') != '3' or output.get('feature') != 'APPEND_ONLY_PROJECTION_LOOP'
            or output.get('loop_version') != protocol.loop_version or output.get('run_id') != run.get('id')
            or output.get('question') != question or not _strict_equal(output.get('execution_profile'), route)
            or output.get('status') == 'MODEL_DISABLED' or output.get('answer') != ''
            or not isinstance(output.get('status'), str) or output['status'] not in _TERMINAL_STATUSES
            or not isinstance(output.get('reason_code'), str)
            or output['reason_code'] not in _status_reasons(protocol)[output['status']]
            or output.get('claims') != [] or output.get('calculations') != []):
        raise DomainError('Projection execution runtime output identity is inconsistent.', 409)
    if (output['reason_code'] not in _status_reasons(protocol)[output['status']]
            or output.get('answer_basis') != (
                'PROJECTION_REVIEW_REQUIRED' if output['status'] == 'REVIEW_REQUIRED'
                else 'PROJECTION_LOCAL_RETRIEVAL_EXHAUSTED'
                if output['reason_code'] == 'NO_NEW_EVIDENCE'
                else 'PROJECTION_LOOP_TERMINAL')):
        raise DomainError('Projection execution runtime terminal is inconsistent.', 409)
    if type(output.get('model_called')) is not bool:
        raise DomainError('Projection execution runtime output telemetry is malformed.', 409)
    for key in ('model_call_count', 'decision_count', 'supplement_round_count',
                'accepted_supplement_request_count', 'projection_row_count'):
        if type(output.get(key)) is not int or output[key] < 0:
            raise DomainError('Projection execution runtime output counters are malformed.', 409)
    _check_missing(output.get('missing'))
    _check_source_scope(output.get('source_scope'))
    if output['source_scope']['snapshot_id'] != run.get('snapshot_id'):
        raise DomainError('Projection execution runtime source scope is inconsistent.', 409)
    _check_proof(output.get('preview_proof'), run, question, route, protocol)
    _check_receipts(output.get('execution_receipts'), protocol)
    _check_review_packet(output.get('review_packet'))
    trace_keys = {'round', 'status', 'reason_code', 'missing_facts', 'requests', 'cached'}
    trace_reasons = {reason for values in _status_reasons(protocol).values() for reason in values}
    trace_reasons.update({'MISSING_SOURCE_TEXT', 'MISSING_IDENTIFIER'})
    if not isinstance(output.get('decision_trace'), list):
        raise DomainError('Projection execution decision trace is malformed.', 409)
    for item in output['decision_trace']:
        if (not isinstance(item, dict) or set(item) != trace_keys
                or type(item.get('round')) is not int or not 1 <= item['round'] <= 3
                or type(item.get('cached')) is not bool
                or not isinstance(item.get('status'), str)
                or item['status'] not in _TERMINAL_STATUSES | {'NEED_EVIDENCE'}
                or not isinstance(item.get('reason_code'), str)
                or item['reason_code'] not in trace_reasons
                ):
            raise DomainError('Projection execution decision trace is malformed.', 409)
        _check_missing(item.get('missing_facts'))
        if not isinstance(item.get('requests'), list):
            raise DomainError('Projection execution decision trace is malformed.', 409)
        for request in item['requests']:
            if (not isinstance(request, dict) or set(request) != {'tool', 'query'}
                    or request.get('tool') not in {'FIND_IDENTIFIER', 'SEARCH_TEXT'}
                    or not isinstance(request.get('query'), str)
                    or not 2 <= len(request['query'].strip()) <= 200):
                raise DomainError('Projection execution decision trace is malformed.', 409)


def _check_record(record: dict, run: dict, question: str, route: dict,
                  protocol: ProjectionProtocol) -> None:
    if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
        raise DomainError('Projection execution record has an unsupported shape.', 409)
    if (record['projection_execution_version'] != protocol.execution_version
            or record['result_kind'] != RESULT_KIND or record['qa_version'] != '3'
            or record['feature'] != 'APPEND_ONLY_PROJECTION_LOOP'
            or record['selector_version'] != protocol.selector_version or record['loop_version'] != protocol.loop_version
            or record['run_id'] != run['id'] or record['snapshot_id'] != run['snapshot_id']
            or record['question'] != question or record['question_key'] != _question_key(question)
            or not _strict_equal(record['execution_profile'], route) or record['answer'] != ''
            or record['claims'] != [] or record['calculations'] != []
            or any(record[key] is not False for key in (
                'source_text_included', 'provider_output_included', 'chain_of_thought_included'))):
        raise DomainError('Projection execution record identity is inconsistent.', 409)
    for key in ('decision_count', 'supplement_round_count',
                'accepted_supplement_request_count', 'projection_row_count'):
        if type(record[key]) is not int or record[key] < 0:
            raise DomainError('Projection execution record counter is malformed.', 409)
    if (not isinstance(record.get('status'), str) or record['status'] not in _TERMINAL_STATUSES
            or not isinstance(record.get('reason_code'), str)
            or record['reason_code'] not in _status_reasons(protocol)[record['status']]):
        raise DomainError('Projection execution record terminal status is unsupported.', 409)
    expected_basis = ('PROJECTION_REVIEW_REQUIRED' if record['status'] == 'REVIEW_REQUIRED'
                      else 'PROJECTION_LOCAL_RETRIEVAL_EXHAUSTED'
                      if record['reason_code'] == 'NO_NEW_EVIDENCE'
                      else 'PROJECTION_LOOP_TERMINAL')
    if record['answer_basis'] != expected_basis:
        raise DomainError('Projection execution record terminal basis is unsupported.', 409)
    _check_missing(record['missing'])
    _check_source_scope(record['source_scope'])
    _check_proof(record['preview_proof'], run, question, route, protocol)
    _check_receipts(record['execution_receipts'], protocol)
    _check_review_packet(record['review_packet'])


def compile_projection_execution(run: dict, question: str, route: dict, runtime_output: dict) -> dict:
    """Create a closed candidate record; caller must authenticate it before saving."""
    route = _canonical_profile(route)
    _question_key(question)
    if not isinstance(runtime_output, dict) or runtime_output.get('status') == 'MODEL_DISABLED':
        raise DomainError('Projection execution requires a completed model terminal.', 409)
    receipts = runtime_output.get('execution_receipts')
    first = receipts[0] if isinstance(receipts, list) and receipts else {}
    protocol = _protocol_for_execution(
        execution_version=(PROJECTION_PROTOCOL_V1.execution_version
                           if runtime_output.get('loop_version') == PROJECTION_PROTOCOL_V1.loop_version
                           else PROJECTION_PROTOCOL_V2.execution_version),
        loop_version=runtime_output.get('loop_version'),
        receipt_version=first.get('receipt_version') if isinstance(first, dict) else None,
        proof_version=(runtime_output.get('preview_proof') or {}).get('proof_version')
                      if isinstance(runtime_output.get('preview_proof'), dict) else None)
    _check_runtime_output(run, question, route, runtime_output, protocol)
    record = {
        'projection_execution_version': protocol.execution_version,
        'result_kind': RESULT_KIND, 'qa_version': '3', 'feature': 'APPEND_ONLY_PROJECTION_LOOP',
        'selector_version': protocol.selector_version, 'loop_version': protocol.loop_version,
        'run_id': run['id'], 'snapshot_id': run['snapshot_id'], 'question': question,
        'question_key': _question_key(question), 'status': runtime_output.get('status'),
        'reason_code': runtime_output.get('reason_code'), 'answer': '', 'claims': [], 'calculations': [],
        'missing': runtime_output.get('missing'), 'answer_basis': runtime_output.get('answer_basis'),
        'source_scope': runtime_output.get('source_scope'), 'execution_profile': route,
        'preview_proof': runtime_output.get('preview_proof'),
        'execution_receipts': runtime_output.get('execution_receipts'),
        'decision_count': runtime_output.get('decision_count'),
        'supplement_round_count': runtime_output.get('supplement_round_count'),
        'accepted_supplement_request_count': runtime_output.get('accepted_supplement_request_count'),
        'projection_row_count': runtime_output.get('projection_row_count'),
        'review_packet': runtime_output.get('review_packet'),
        'source_text_included': False, 'provider_output_included': False,
        'chain_of_thought_included': False,
    }
    _check_record(record, run, question, route, protocol)
    return _copy(record)


def authenticate_projection_execution(db, uploads, run: dict, question: str, route: dict, record: dict) -> dict:
    """Return the locally rebuilt durable projection terminal, or reject it."""
    return _authenticate_projection_execution_stage(db, uploads, run, question, route, record)[0]


def _authenticate_projection_execution_stage(db, uploads, run: dict, question: str,
                                             route: dict, record: dict) -> tuple:
    """Keep the fully authenticated final stage private for source-review derivation."""
    route = _canonical_profile(route)
    if not isinstance(record, dict):
        raise DomainError('Projection execution record has an unsupported shape.', 409)
    receipts = record.get('execution_receipts')
    first = receipts[0] if isinstance(receipts, list) and receipts else {}
    protocol = _protocol_for_execution(
        execution_version=record.get('projection_execution_version'), loop_version=record.get('loop_version'),
        receipt_version=first.get('receipt_version') if isinstance(first, dict) else None,
        proof_version=(record.get('preview_proof') or {}).get('proof_version')
                      if isinstance(record.get('preview_proof'), dict) else None)
    _check_record(record, run, question, route, protocol)
    stage, chain, accepted, positive, proof, terminal = _authenticate_chain(
        db, uploads, run, question, route, record['execution_receipts'], protocol=protocol)
    if (not _strict_equal(record['preview_proof'], proof)
            or not _strict_equal(record['source_scope'], _source_scope(stage))):
        raise DomainError('Projection execution preview or source scope is stale.', 409)
    expected = {
        'decision_count': len(record['execution_receipts']),
        'supplement_round_count': positive,
        'accepted_supplement_request_count': accepted,
        'projection_row_count': len(stage.context['rows']),
    }
    if any(record[key] != value for key, value in expected.items()):
        raise DomainError('Projection execution counters are inconsistent.', 409)
    if terminal['status'] == 'REVIEW_REQUIRED':
        if (record['status'] != 'REVIEW_REQUIRED'
                or record['reason_code'] != 'OBJECT_CONDITION_REVIEW_REQUIRED'
                or record['answer_basis'] != 'PROJECTION_REVIEW_REQUIRED'
                or record['missing'] != []
                or not _strict_equal(record['review_packet'], _review_packet(stage, terminal))):
            raise DomainError('Projection execution review terminal is inconsistent.', 409)
    elif terminal['status'] == 'NEED_EVIDENCE':
        if stage.added_page_count != 0:
            raise DomainError('Projection execution final evidence request did not terminate locally.', 409)
        missing = [{'part_ref': part_ref, 'gap_code': 'LOCAL_RETRIEVAL_EXHAUSTED'}
                   for part_ref in dict.fromkeys(value['part_ref'] for value in terminal['missing_facts'])]
        if (record['status'] != 'CANNOT_ANSWER' or record['reason_code'] != 'NO_NEW_EVIDENCE'
                or record['answer_basis'] != 'PROJECTION_LOCAL_RETRIEVAL_EXHAUSTED'
                or record['missing'] != missing or record['review_packet'] is not None):
            raise DomainError('Projection execution no-new terminal is inconsistent.', 409)
    else:
        if terminal['status'] == 'CANNOT_ANSWER' and terminal['reason_code'] == 'NO_NEW_EVIDENCE':
            raise DomainError('Projection execution local no-new terminal lacks an accepted append.', 409)
        if (record['status'] != terminal['status'] or record['reason_code'] != terminal['reason_code']
                or record['answer_basis'] != 'PROJECTION_LOOP_TERMINAL'
                or record['missing'] != terminal['missing_facts'] or record['review_packet'] is not None):
            raise DomainError('Projection execution terminal is inconsistent.', 409)
    return _copy(record), stage


def authenticate_projection_failure(db, uploads, run: dict, question: str, route: dict,
                                    terminal_receipt: dict, failure_execution: dict) -> dict:
    """Authenticate receipt-only contract failure history without provider output."""
    route = _canonical_profile(route)
    required = {'failure_execution_version', 'receipt_scope', 'execution_receipts',
                'supplement_round_count', 'accepted_supplement_request_count'}
    if (not isinstance(failure_execution, dict) or set(failure_execution) != required
            or failure_execution.get('failure_execution_version') not in {
                PROJECTION_PROTOCOL_V1.failure_version, PROJECTION_PROTOCOL_V2.failure_version}
            or failure_execution.get('receipt_scope') != 'COMPLETE_CHAIN'
            or not isinstance(failure_execution.get('execution_receipts'), list)
            or not failure_execution['execution_receipts']
            or type(failure_execution.get('supplement_round_count')) is not int
            or type(failure_execution.get('accepted_supplement_request_count')) is not int
            or failure_execution['supplement_round_count'] < 0
            or failure_execution['accepted_supplement_request_count'] < 0
            or not _strict_equal(failure_execution['execution_receipts'][-1], terminal_receipt)):
        raise DomainError('Projection failure execution is malformed.', 409)
    receipts = failure_execution['execution_receipts']
    first = receipts[0] if receipts else {}
    protocol = _protocol_for_execution(
        execution_version=(PROJECTION_PROTOCOL_V1.execution_version
                           if failure_execution['failure_execution_version'] == PROJECTION_PROTOCOL_V1.failure_version
                           else PROJECTION_PROTOCOL_V2.execution_version),
        loop_version=first.get('loop_version') if isinstance(first, dict) else None,
        receipt_version=first.get('receipt_version') if isinstance(first, dict) else None,
        proof_version=(PROJECTION_PROTOCOL_V1.proof_version
                       if failure_execution['failure_execution_version'] == PROJECTION_PROTOCOL_V1.failure_version
                       else PROJECTION_PROTOCOL_V2.proof_version))
    stage, chain, accepted, positive, proof, _diagnostic = _authenticate_chain(
        db, uploads, run, question, route, receipts, protocol=protocol, final_failure=True)
    if (failure_execution['supplement_round_count'] != positive
            or failure_execution['accepted_supplement_request_count'] != accepted):
        raise DomainError('Projection failure execution counters are inconsistent.', 409)
    return _copy(failure_execution)
