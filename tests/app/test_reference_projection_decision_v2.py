"""Pure V2 decision contract: explicit opt-in, never a loop1 replacement."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.reference_projection_decision import (
    ProjectionDecisionError, prompt_contract, prompt_contract_for,
    validate_projection_decision, validate_projection_decision_for,
)
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1, PROJECTION_PROTOCOL_V2, resolve_projection_protocol
from app.reference_projection_loop_receipt import CONTEXT_POLICY as LOOP1_CONTEXT_POLICY


@pytest.fixture
def bundle():
    return SimpleNamespace(
        projection_input_sha256='a' * 64,
        required_parts=[{'part_ref': 'P1', 'text': 'Object?'}],
        context={'projection_complete': True, 'source_conflicts': [],
                 'rows': [{'row_ref': 'X1', 'selectable': True}]},
    )


def _decision(protocol, *, status='CANNOT_ANSWER', reason='UNSUPPORTED_TASK', gap='UNSUPPORTED_TASK'):
    return {
        'contract_version': protocol.decision_contract_version,
        'projection_input_sha256': 'a' * 64, 'status': status, 'reason_code': reason,
        'missing_facts': [{'part_ref': 'P1', 'gap_code': gap}], 'requests': [], 'selections': [],
    }


def test_protocol_registry_is_static_and_fail_closed():
    assert resolve_projection_protocol('projection-protocol-1') is PROJECTION_PROTOCOL_V1
    assert resolve_projection_protocol(PROJECTION_PROTOCOL_V2) is PROJECTION_PROTOCOL_V2
    assert PROJECTION_PROTOCOL_V1.context_policy == LOOP1_CONTEXT_POLICY
    with pytest.raises(ValueError):
        resolve_projection_protocol('project-projection-loop-2')


def test_v1_default_prompt_hash_and_schema_remain_frozen():
    system, schema, digest = prompt_contract()
    assert digest == '952ad7890602a0a2073df634c2b96975f1f8830c8cc3f76d0579021d9e40dc2f'
    assert schema['properties']['contract_version']['const'] == 'project-projection-decision-1'
    assert 'NO_NEW_EVIDENCE' in schema['properties']['reason_code']['enum']
    assert 'NO_NEW_EVIDENCE' in system


def test_v2_is_explicit_and_removes_provider_no_new(bundle):
    system, schema, digest = prompt_contract_for(PROJECTION_PROTOCOL_V2)
    assert digest and schema['properties']['contract_version']['const'] == 'project-projection-decision-2'
    assert 'NO_NEW_EVIDENCE' not in schema['properties']['reason_code']['enum']
    assert 'LOCAL_RETRIEVAL_EXHAUSTED' not in str(schema)
    assert 'never emit it' in system
    legal = _decision(PROJECTION_PROTOCOL_V2)
    assert validate_projection_decision_for(PROJECTION_PROTOCOL_V2, legal, bundle) == legal
    forbidden = _decision(PROJECTION_PROTOCOL_V2, reason='NO_NEW_EVIDENCE', gap='LOCAL_RETRIEVAL_EXHAUSTED')
    with pytest.raises(ProjectionDecisionError, match='projection_decision_schema'):
        validate_projection_decision_for(PROJECTION_PROTOCOL_V2, forbidden, bundle)
    with pytest.raises(ProjectionDecisionError, match='projection_decision_schema'):
        validate_projection_decision(forbidden, bundle)


@pytest.mark.parametrize('remaining', [3, 2])
def test_v2_insufficient_evidence_is_only_a_final_budget_terminal(bundle, remaining):
    decision = _decision(PROJECTION_PROTOCOL_V2, reason='INSUFFICIENT_EVIDENCE', gap='SOURCE_TEXT')
    with pytest.raises(ProjectionDecisionError, match='projection_decision_round_limit'):
        validate_projection_decision_for(
            PROJECTION_PROTOCOL_V2, decision, bundle, remaining_decisions=remaining)


def test_v2_final_insufficient_evidence_neither_claims_missing_file_nor_local_no_new(bundle):
    decision = _decision(PROJECTION_PROTOCOL_V2, reason='INSUFFICIENT_EVIDENCE', gap='IDENTIFIER')
    assert validate_projection_decision_for(
        PROJECTION_PROTOCOL_V2, decision, bundle, remaining_decisions=1) == decision


@pytest.mark.parametrize('projection_complete', [False, None, 1])
def test_v2_final_insufficient_evidence_requires_complete_projection(bundle, projection_complete):
    """Evidence insufficiency must not mask an incomplete PDF projection."""
    bundle.context['projection_complete'] = projection_complete
    decision = _decision(PROJECTION_PROTOCOL_V2, reason='INSUFFICIENT_EVIDENCE', gap='SOURCE_TEXT')
    with pytest.raises(ProjectionDecisionError, match='projection_decision_incomplete'):
        validate_projection_decision_for(
            PROJECTION_PROTOCOL_V2, decision, bundle, remaining_decisions=1)
