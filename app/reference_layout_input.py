"""Canonical v9 model navigation identity and source reauthentication.

Only the navigation actually sent to the model is committed here. This is not
an authentication of every parser bbox or a proof of semantic support.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.db import DomainError, dumps
from app.page_selector import LAYOUT_BOUND_SELECTOR_VERSION, model_layout_navigation
from app.prompt_schema import expand_schema

RECEIPT_VERSION = 'reference-model-input-receipt-3'
LAYOUT_CONTEXT_POLICY = 'COMPLETE_SELECTED_SCOPE_WITH_LAYOUT_BINDING_V1'
_ROOT = Path(__file__).resolve().parents[1]
_PROMPT_SEPARATOR = '\n\n---\n\n'
PREVIEW_PROOF_VERSION = 'reference-preview-proof-1'


def navigation_and_identity(rows: list[dict]) -> tuple[dict, dict]:
    navigation = model_layout_navigation(rows)
    encoded = dumps(navigation).encode('utf-8')
    return navigation, {
        'layout_navigation_version': navigation['navigation_version'],
        'layout_navigation_sha256': hashlib.sha256(encoded).hexdigest(),
        'layout_navigation_bytes': len(encoded),
    }


def navigation_identity(rows: list[dict]) -> dict:
    return navigation_and_identity(rows)[1]


def ordered_manifest_sha256(evidence_inputs: list[dict], identity: dict) -> str:
    if any(not isinstance(item, dict) or set(item) != {'evidence_id', 'text_sha256'}
           for item in evidence_inputs):
        raise DomainError('Reference layout manifest evidence input is malformed.', 409)
    inputs = [{'evidence_id': item['evidence_id'], 'text_sha256': item['text_sha256']}
              for item in evidence_inputs]
    keys = ('layout_navigation_version', 'layout_navigation_sha256', 'layout_navigation_bytes')
    if set(identity) != set(keys):
        raise DomainError('Reference layout manifest navigation identity is malformed.', 409)
    value = ['reference-ordered-evidence-manifest-3', inputs,
             {key: identity[key] for key in keys}]
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def prompt_contract() -> tuple[str, str]:
    directory = _ROOT / 'prompts/project-evidence-loop'
    prompt = ((directory / 'system.md').read_text(encoding='utf-8')
              + _PROMPT_SEPARATOR
              + (directory / 'layout-supplement.md').read_text(encoding='utf-8'))
    schema = expand_schema(json.loads(
        (_ROOT / 'spec/schemas/project-evidence-decision-complete.schema.json').read_text(encoding='utf-8')))
    system_text = prompt + '\nJSON Schema:\n' + dumps(schema)
    return system_text, hashlib.sha256(system_text.encode('utf-8')).hexdigest()


def initial_manifest_sha256(selection_id: str, rows: list[dict], source_groups: list,
                            source_conflicts: list[str]) -> str:
    """Commit the complete initial source scope, including UNBOUND sources."""
    inputs = [{'evidence_id': row['evidence_id'],
               'text_sha256': hashlib.sha256(row['raw_text'].encode('utf-8')).hexdigest()}
              for row in rows]
    scope = [(row['evidence_id'], row['document_id'], row.get('locator'), item['text_sha256'])
             for row, item in zip(rows, inputs)]
    value = ['reference-initial-evidence-manifest-3', selection_id,
             ordered_manifest_sha256(inputs, navigation_identity(rows)), scope,
             source_groups, source_conflicts]
    return hashlib.sha256(dumps(value).encode('utf-8')).hexdigest()


def preview_proof(run: dict, question: str, route: dict, selection_id: str,
                  initial_manifest: str) -> dict:
    """Public recomputable identity, not a signature or permission grant."""
    from app.reference_text_profiles import route as canonical_route
    canonical = canonical_route(route)
    if canonical is None:
        raise DomainError('Reference layout preview requires a canonical named text profile.', 409)
    return {
        'proof_version': PREVIEW_PROOF_VERSION,
        'project_id': run['project_id'], 'run_id': run['id'], 'snapshot_id': run['snapshot_id'],
        'normalized_question_sha256': hashlib.sha256(' '.join(question.split()).encode('utf-8')).hexdigest(),
        'selector_version': LAYOUT_BOUND_SELECTOR_VERSION, 'context_policy': LAYOUT_CONTEXT_POLICY,
        'profile_version': canonical['profile_version'], 'profile_id': canonical['profile_id'],
        'route_sha256': hashlib.sha256(dumps(canonical).encode('utf-8')).hexdigest(),
        'selection_id': selection_id, 'initial_evidence_manifest_sha256': initial_manifest,
        'prompt_contract_hash': prompt_contract()[1],
    }


def authenticate_receipt_sources(receipt: dict, sources: dict[str, dict]) -> None:
    """Rebuild in receipt E order from caller-authenticated run/snapshot sources.

    Callers must separately validate the schema, canonical named profile and
    settled ledger commitment. Both successful and failed calls use this check.
    """
    if (receipt.get('receipt_version') != RECEIPT_VERSION
            or receipt.get('selector_version') != LAYOUT_BOUND_SELECTOR_VERSION
            or receipt.get('context_policy') != LAYOUT_CONTEXT_POLICY
            or receipt.get('source_text_clipped') is not False
            or receipt.get('visual_inputs') != [] or receipt.get('image_bytes') != 0):
        raise DomainError('Reference layout receipt source policy is inconsistent.', 409)
    inputs = receipt.get('evidence_inputs')
    if any(not isinstance(receipt.get(key), str)
           or not re.fullmatch(r'[0-9a-f]{64}', receipt[key])
           for key in ('initial_evidence_manifest_sha256', 'profile_neutral_input_sha256')):
        raise DomainError('Reference layout receipt named input identity is malformed.', 409)
    if not isinstance(inputs, list) or receipt.get('evidence_count') != len(inputs):
        raise DomainError('Reference layout receipt input counts are inconsistent.', 409)
    rows = []
    seen = set()
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {'evidence_id', 'text_sha256'}:
            raise DomainError('Reference layout receipt evidence input is malformed.', 409)
        evidence_id = item['evidence_id']
        if not isinstance(evidence_id, str) or evidence_id in seen or evidence_id not in sources:
            raise DomainError('Reference layout receipt evidence scope is inconsistent.', 409)
        seen.add(evidence_id)
        source = sources[evidence_id]
        if (source.get('evidence_id') != evidence_id
                or not isinstance(source.get('raw_text'), str)
                or hashlib.sha256(source['raw_text'].encode('utf-8')).hexdigest() != item['text_sha256']):
            raise DomainError('Reference layout receipt evidence hash is inconsistent.', 409)
        rows.append(source)
    try:
        identity = navigation_identity(rows)
    except (ValueError, TypeError, KeyError) as exc:
        raise DomainError('Reference layout receipt navigation cannot be rebuilt.', 409) from exc
    if any(receipt.get(key) != value for key, value in identity.items()):
        raise DomainError('Reference layout receipt navigation is inconsistent.', 409)
    if (receipt.get('ordered_evidence_manifest_sha256') != ordered_manifest_sha256(inputs, identity)
            or receipt.get('source_text_bytes') != sum(len(row['raw_text'].encode('utf-8')) for row in rows)):
        raise DomainError('Reference layout receipt complete-source manifest is inconsistent.', 409)
    system_text, contract_hash = prompt_contract()
    if (receipt.get('prompt_contract_hash') != contract_hash
            or receipt.get('system_text_bytes') != len(system_text.encode('utf-8'))):
        raise DomainError('Reference layout receipt prompt contract is inconsistent.', 409)
