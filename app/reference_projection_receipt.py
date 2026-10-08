"""Receipt 4 commits PDF-derived inputs without reusing legacy E identities."""
from __future__ import annotations

import hashlib
import json

from app.db import DomainError, dumps
from app.reference_pdf_projection import PROJECTION_VERSION
from app.reference_projection_decision import CONTEXT_POLICY, CONTRACT_VERSION, SELECTOR_VERSION
from app.reference_text_profiles import route as canonical_route
from contracts.runtime_rules import validate_schema

RECEIPT_VERSION = 'reference-model-input-receipt-4'


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_projection_receipt(*, call_id: str, round_index: int, request_hash: str,
                             question: str, bundle, route: dict, system_text: str,
                             user_text: str, upper: int, limit: int, cached: bool,
                             prompt_contract_hash: str,
                             initial_projection_context_sha256: str) -> dict:
    profile = canonical_route(route)
    if profile is None or profile != route or limit != profile['max_output_tokens']:
        raise DomainError('Projection receipt requires a canonical named profile.', 409)
    if question != bundle.question or prompt_contract_hash != _sha(system_text):
        raise DomainError('Projection receipt input identity is inconsistent.', 409)
    # Later rounds require a separately authenticated acquisition/receipt chain.
    # Until that entry exists, this internal slice cannot self-attest a first hash.
    if (type(round_index) is not int or round_index != 0 or type(cached) is not bool
            or type(upper) is not int
            or upper != len(system_text.encode('utf-8')) + len(user_text.encode('utf-8')) + 256):
        raise DomainError('Projection receipt dispatch accounting is inconsistent.', 409)
    if initial_projection_context_sha256 != bundle.projection_context_sha256:
        raise DomainError('Projection receipt initial context is inconsistent.', 409)
    # Require that the actual serialized message, not a parallel helper view,
    # contains the exact context and question/parts committed by the bundle.
    try:
        content = json.loads(user_text)
    except (TypeError, ValueError) as exc:
        raise DomainError('Projection receipt message is malformed.', 409) from exc
    expected_content = {
        'contract_version': CONTRACT_VERSION,
        'projection_input_sha256': bundle.projection_input_sha256,
        'round': 1, 'question': bundle.question, 'remaining_model_decisions': 1,
        'required_answer_parts': bundle.required_parts, 'projection_context': bundle.context,
        'effective_source_conflicts': bundle.context['source_conflicts'], 'request_history': [],
    }
    if content != expected_content or user_text != dumps(expected_content):
        raise DomainError('Projection receipt does not match the sent message.', 409)
    receipt = {
        'receipt_version': RECEIPT_VERSION, 'model_call_id': call_id,
        'round': round_index + 1, 'request_hash': request_hash,
        'prompt_contract_hash': prompt_contract_hash,
        'question_hash': _sha(' '.join(question.split())),
        'provider': profile['provider'], 'model': profile['text_model'],
        'api_protocol': profile['api_protocol'],
        'structured_output_mode': profile['structured_output_mode'],
        'inference_mode': profile['inference_mode'],
        'profile_version': profile['profile_version'], 'profile_id': profile['profile_id'],
        'cached': cached, 'source_text_included': False,
        'prompt_content_included': False, 'chain_of_thought_included': False,
        'evidence_count': 0, 'evidence_inputs': [], 'visual_inputs': [],
        'system_text_bytes': len(system_text.encode('utf-8')),
        'user_text_bytes': len(user_text.encode('utf-8')), 'image_bytes': 0,
        'request_upper_bound_bytes': upper, 'max_output_tokens': limit,
        'selector_version': SELECTOR_VERSION, 'context_policy': CONTEXT_POLICY,
        'source_text_clipped': False,
        'source_text_bytes': sum(len(row['text'].encode('utf-8')) for row in bundle.context['rows']),
        'decision_contract_version': CONTRACT_VERSION, 'projection_version': PROJECTION_VERSION,
        'projection_input_sha256': bundle.projection_input_sha256,
        'projection_context_sha256': bundle.projection_context_sha256,
        'ordered_projection_manifest_sha256': bundle.ordered_projection_manifest_sha256,
        'initial_projection_context_sha256': initial_projection_context_sha256,
        'projection_sources': bundle.projection_sources,
        'projection_row_count': len(bundle.context['rows']),
        'profile_neutral_input_sha256': _sha(dumps([
            'reference-profile-neutral-input-1', system_text, user_text, []])),
    }
    validate_schema('reference-model-input-receipt', receipt)
    return receipt


def authenticate_projection_receipt(receipt: dict, bundle, *, system_text: str,
                                    user_text: str, route: dict) -> None:
    """Recompute content-derived fields; callers also check PDF and ledger auth."""
    validate_schema('reference-projection-input-receipt', receipt)
    expected = build_projection_receipt(
        call_id=receipt['model_call_id'], round_index=receipt['round'] - 1,
        request_hash=receipt['request_hash'], question=bundle.question, bundle=bundle,
        route=route, system_text=system_text, user_text=user_text,
        upper=len(system_text.encode('utf-8')) + len(user_text.encode('utf-8')) + 256,
        limit=route['max_output_tokens'], cached=receipt['cached'],
        prompt_contract_hash=_sha(system_text),
        initial_projection_context_sha256=receipt['initial_projection_context_sha256'])
    if receipt != expected:
        raise DomainError('Projection receipt does not match its authenticated input.', 409)
