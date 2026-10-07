"""Receipt5: append-only input identity, separate from the frozen receipt4 slice.

These are pure commitment helpers, not model-chain authorization. The Gateway
must authenticate the sources and every prior settled ledger call separately.
"""
from __future__ import annotations

import hashlib
import json

from app.db import DomainError, dumps
from app.reference_pdf_projection import PROJECTION_VERSION
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1, ProjectionProtocol, resolve_projection_protocol
from app.reference_text_profiles import route as canonical_route
from contracts.runtime_rules import validate_schema

LOOP_VERSION = 'project-projection-loop-1'
CONTEXT_POLICY = 'COMPLETE_SELECTED_PDF_PROJECTION_APPEND_ONLY_V1'
RECEIPT_VERSION = 'reference-model-input-receipt-5'


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _validate_loop_receipt(protocol: ProjectionProtocol, receipt: dict) -> None:
    try:
        validate_schema('reference-projection-loop-receipt' if protocol == PROJECTION_PROTOCOL_V1
                        else 'reference-projection-loop-receipt-v2', receipt)
    except Exception as exc:
        raise DomainError('Projection loop receipt protocol is inconsistent.', 409) from exc


def chain_sha256(prior_chain: list[dict], *, protocol: ProjectionProtocol | str = PROJECTION_PROTOCOL_V1) -> str:
    selected = resolve_projection_protocol(protocol)
    if not isinstance(prior_chain, list) or len(prior_chain) > 2:
        raise DomainError('Projection loop prior chain is malformed.', 409)
    leaves = []
    for index, item in enumerate(prior_chain):
        if (not isinstance(item, dict) or set(item) != {'receipt', 'decision'}
                or not isinstance(item['receipt'], dict) or not isinstance(item['decision'], dict)):
            raise DomainError('Projection loop prior chain is malformed.', 409)
        receipt = item['receipt']
        _validate_loop_receipt(selected, receipt)
        if receipt.get('receipt_version') != selected.receipt_version:
            raise DomainError('Projection loop receipt protocol is inconsistent.', 409)
        if receipt['round'] != index + 1:
            raise DomainError('Projection loop prior chain is not contiguous.', 409)
        leaves.append({
            'round': receipt['round'], 'model_call_id': receipt['model_call_id'],
            'request_hash': receipt['request_hash'],
            'receipt_sha256': _sha(_canonical({key: value for key, value in receipt.items() if key != 'cached'})),
            'decision_sha256': _sha(_canonical(item['decision'])),
        })
    return _sha(dumps([selected.chain_domain, leaves]))


def request_history(prior_chain: list[dict], *, protocol: ProjectionProtocol | str = PROJECTION_PROTOCOL_V1) -> list[dict]:
    chain_sha256(prior_chain, protocol=protocol)
    history = []
    for item in prior_chain:
        decision = item['decision']
        if decision.get('status') != 'NEED_EVIDENCE' or not isinstance(decision.get('requests'), list):
            raise DomainError('Projection loop prior decision is not an evidence request.', 409)
        history.extend(decision['requests'])
    return history


def build_loop_user_text(stage, prior_chain: list[dict], *, protocol: ProjectionProtocol | str = PROJECTION_PROTOCOL_V1) -> str:
    selected = resolve_projection_protocol(protocol)
    if (type(stage.round_index) is not int or not 0 <= stage.round_index <= 2
            or len(prior_chain) != stage.round_index):
        raise DomainError('Projection loop round and prior chain differ.', 409)
    return dumps({
        'contract_version': selected.decision_contract_version, 'loop_version': selected.loop_version,
        'projection_input_sha256': stage.projection_input_sha256,
        'round': stage.round_index + 1, 'question': stage.question,
        'remaining_model_decisions': 3 - stage.round_index,
        'required_answer_parts': stage.required_parts, 'projection_context': stage.context,
        'effective_source_conflicts': stage.context['source_conflicts'],
        'request_history': request_history(prior_chain, protocol=selected),
        'prior_chain_sha256': chain_sha256(prior_chain, protocol=selected),
    })


def build_projection_loop_receipt(*, call_id: str, round_index: int, request_hash: str,
                                  question: str, stage, prior_chain: list[dict], route: dict,
                                  system_text: str, user_text: str, upper: int, limit: int,
                                  cached: bool, prompt_contract_hash: str,
                                  protocol: ProjectionProtocol | str = PROJECTION_PROTOCOL_V1) -> dict:
    selected = resolve_projection_protocol(protocol)
    profile = canonical_route(route)
    if profile is None or profile != route or limit != profile['max_output_tokens']:
        raise DomainError('Projection loop receipt requires a canonical named profile.', 409)
    if (question != stage.question or prompt_contract_hash != _sha(system_text)
            or type(round_index) is not int or round_index != stage.round_index
            or type(cached) is not bool or type(upper) is not int
            or upper != len(system_text.encode('utf-8')) + len(user_text.encode('utf-8')) + 256
            or user_text != build_loop_user_text(stage, prior_chain, protocol=selected)):
        raise DomainError('Projection loop receipt does not match the exact sent input.', 409)
    base = stage.initial_bundle
    receipt = {
        'receipt_version': selected.receipt_version, 'model_call_id': call_id,
        'round': round_index + 1, 'request_hash': request_hash,
        'prompt_contract_hash': prompt_contract_hash,
        'question_hash': _sha(' '.join(question.split())),
        'provider': profile['provider'], 'model': profile['text_model'],
        'api_protocol': profile['api_protocol'], 'structured_output_mode': profile['structured_output_mode'],
        'inference_mode': profile['inference_mode'], 'profile_version': profile['profile_version'],
        'profile_id': profile['profile_id'], 'cached': cached,
        'source_text_included': False, 'prompt_content_included': False, 'chain_of_thought_included': False,
        'evidence_count': 0, 'evidence_inputs': [], 'visual_inputs': [],
        'system_text_bytes': len(system_text.encode('utf-8')),
        'user_text_bytes': len(user_text.encode('utf-8')), 'image_bytes': 0,
        'request_upper_bound_bytes': upper, 'max_output_tokens': limit,
        'selector_version': selected.selector_version, 'context_policy': selected.context_policy,
        'loop_version': selected.loop_version, 'source_text_clipped': False,
        'source_text_bytes': sum(len(row['text'].encode('utf-8')) for row in stage.context['rows']),
        'decision_contract_version': selected.decision_contract_version, 'projection_version': PROJECTION_VERSION,
        'projection_input_sha256': stage.projection_input_sha256,
        'projection_context_sha256': stage.projection_context_sha256,
        'ordered_projection_manifest_sha256': stage.ordered_projection_manifest_sha256,
        'initial_projection_input_sha256': base.projection_input_sha256,
        'initial_projection_context_sha256': base.projection_context_sha256,
        'initial_ordered_projection_manifest_sha256': base.ordered_projection_manifest_sha256,
        'prior_chain_sha256': chain_sha256(prior_chain, protocol=selected),
        'request_history_sha256': _sha(dumps(request_history(prior_chain, protocol=selected))),
        'projection_sources': stage.projection_sources,
        'projection_row_count': len(stage.context['rows']),
        'profile_neutral_input_sha256': _sha(dumps([
            'reference-profile-neutral-input-1', system_text, user_text, []])),
    }
    _validate_loop_receipt(selected, receipt)
    return receipt


def authenticate_projection_loop_receipt(receipt: dict, stage, *, prior_chain: list[dict],
                                         system_text: str, user_text: str, route: dict,
                                         protocol: ProjectionProtocol | str = PROJECTION_PROTOCOL_V1) -> None:
    selected = resolve_projection_protocol(protocol)
    _validate_loop_receipt(selected, receipt)
    expected = build_projection_loop_receipt(
        call_id=receipt['model_call_id'], round_index=receipt['round'] - 1,
        request_hash=receipt['request_hash'], question=stage.question, stage=stage,
        prior_chain=prior_chain, route=route, system_text=system_text, user_text=user_text,
        upper=len(system_text.encode('utf-8')) + len(user_text.encode('utf-8')) + 256,
        limit=route['max_output_tokens'], cached=receipt['cached'], prompt_contract_hash=_sha(system_text),
        protocol=selected)
    if receipt != expected:
        raise DomainError('Projection loop receipt input identity is inconsistent.', 409)
