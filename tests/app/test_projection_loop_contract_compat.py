"""Frozen compatibility boundary for loop1 before a separate loop version exists."""
from __future__ import annotations

import hashlib

import pytest

from app.reference_projection_decision import CONTRACT_VERSION, SELECTOR_VERSION, prompt_contract
from app.reference_projection_loop_receipt import (
    CONTEXT_POLICY, LOOP_VERSION, RECEIPT_VERSION, build_loop_user_text,
)
from app.reference_projection_loop import ProjectProjectionLoop
from app.reference_projection_preview import projection_preview_proof
from app.reference_text_profiles import profile
from contracts.runtime_rules import validate_schema
from .test_reference_projection_loop import QUESTION, _ask, _preview, _setup


def _direct_legacy_no_new(content: dict) -> dict:
    """This is intentionally the existing loop1 provider shape, not a new path."""
    return {
        'contract_version': CONTRACT_VERSION,
        'projection_input_sha256': content['projection_input_sha256'],
        'status': 'CANNOT_ANSWER', 'reason_code': 'NO_NEW_EVIDENCE',
        'missing_facts': [{'part_ref': 'P1', 'gap_code': 'LOCAL_RETRIEVAL_EXHAUSTED'}],
        'requests': [], 'selections': [],
    }


def test_loop1_direct_provider_no_new_is_legacy_local_basis_without_supplement(
        client, project, tmp_path):
    """Record, rather than repair, the legacy ambiguity before loop2 exists."""
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, _direct_legacy_no_new)
    try:
        proof = _preview(loop, run)
        result = _ask(loop, run, proof)
    finally:
        gateway.close()
    assert result['status'] == 'CANNOT_ANSWER'
    assert result['reason_code'] == 'NO_NEW_EVIDENCE'
    assert result['answer_basis'] == 'PROJECTION_LOCAL_RETRIEVAL_EXHAUSTED'
    assert result['supplement_round_count'] == 0
    assert result['accepted_supplement_request_count'] == 0
    assert len(result['execution_receipts']) == len(sent) == 1


def test_loop1_proof2_receipt5_bind_real_prompt_and_user_identity(client, project, tmp_path):
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, _direct_legacy_no_new)
    route = profile('FLASH_NONE')
    try:
        proof = _preview(loop, run)
        result = _ask(loop, run, proof)
    finally:
        gateway.close()
    system_text, schema, prompt_hash = prompt_contract()
    receipt = result['execution_receipts'][0]
    stage, _selection = ProjectProjectionLoop(db, gateway, client.app.state.uploads)._initial(run, QUESTION)
    assert proof == projection_preview_proof(
        run,
        # `preview` itself supplied the trusted initial stage; this test only
        # freezes its public proof identity, never constructs one by hand.
        stage,
        route,
    )
    assert proof['selector_version'] == SELECTOR_VERSION
    assert proof['loop_version'] == LOOP_VERSION
    assert proof['prompt_contract_hash'] == prompt_hash
    assert receipt['receipt_version'] == RECEIPT_VERSION
    assert receipt['decision_contract_version'] == CONTRACT_VERSION
    assert receipt['selector_version'] == SELECTOR_VERSION
    assert receipt['loop_version'] == LOOP_VERSION
    assert receipt['context_policy'] == CONTEXT_POLICY
    assert receipt['prompt_contract_hash'] == prompt_hash
    assert receipt['user_text_bytes'] == len(build_loop_user_text(stage, []).encode('utf-8'))
    assert receipt['user_text_bytes'] == len(__import__('app.db', fromlist=['dumps']).dumps(sent[0]).encode('utf-8'))
    assert receipt['profile_neutral_input_sha256'] == hashlib.sha256(
        __import__('app.db', fromlist=['dumps']).dumps([
            'reference-profile-neutral-input-1', system_text, build_loop_user_text(stage, []), [],
        ]).encode('utf-8')).hexdigest()
    assert isinstance(schema, dict)
    validate_schema('reference-projection-loop-receipt', receipt)
