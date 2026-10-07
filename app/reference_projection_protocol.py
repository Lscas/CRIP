"""Static, dependency-free version identities for projection contracts.

This registry deliberately imports no decision, prompt, Gateway, database, or
source code.  Callers must opt into V2 explicitly; V1 remains the default of
the historical public decision helpers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectionProtocol:
    version: str
    decision_contract_version: str
    loop_version: str
    receipt_version: str
    proof_version: str
    execution_version: str
    failure_version: str
    chain_domain: str
    request_domain: str
    selector_version: str
    context_policy: str
    review_packet_version: str
    prompt_path: str
    schema_name: str


V1 = ProjectionProtocol(
    version='projection-protocol-1',
    decision_contract_version='project-projection-decision-1',
    loop_version='project-projection-loop-1',
    receipt_version='reference-model-input-receipt-5',
    proof_version='reference-preview-proof-2',
    execution_version='reference-projection-execution-1',
    failure_version='projection-failure-execution-1',
    chain_domain='projection-execution-chain-1',
    request_domain='projection-decision-v10-loop-1',
    selector_version='literal-page-selector-10',
    context_policy='COMPLETE_SELECTED_PDF_PROJECTION_APPEND_ONLY_V1',
    review_packet_version='projection-review-packet-2',
    prompt_path='prompts/project-projection-loop/system.md',
    schema_name='project-projection-decision',
)

V2 = ProjectionProtocol(
    version='projection-protocol-2',
    decision_contract_version='project-projection-decision-2',
    loop_version='project-projection-loop-2',
    receipt_version='reference-model-input-receipt-6',
    proof_version='reference-preview-proof-3',
    execution_version='reference-projection-execution-2',
    failure_version='projection-failure-execution-2',
    chain_domain='projection-execution-chain-2',
    request_domain='projection-decision-v10-loop-2',
    selector_version='literal-page-selector-10',
    context_policy='COMPLETE_SELECTED_PDF_PROJECTION_APPEND_ONLY_V1',
    review_packet_version='projection-review-packet-2',
    prompt_path='prompts/project-projection-loop-v2/system.md',
    schema_name='project-projection-decision-v2',
)

PROJECTION_PROTOCOL_V1 = V1
PROJECTION_PROTOCOL_V2 = V2
_BY_VERSION = {V1.version: V1, V2.version: V2}


def resolve_projection_protocol(value: ProjectionProtocol | str) -> ProjectionProtocol:
    """Resolve only one of the frozen protocol identities; never infer a version."""
    if isinstance(value, ProjectionProtocol) and value in _BY_VERSION.values():
        return value
    if isinstance(value, str) and value in _BY_VERSION:
        return _BY_VERSION[value]
    raise ValueError('Unsupported projection protocol version')
