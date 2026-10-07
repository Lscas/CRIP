"""Recomputable initial-input proof for the internal append-only projection loop."""
from __future__ import annotations

import hashlib

from app.db import DomainError, dumps
from app.reference_projection_decision import prompt_contract_for
from app.reference_projection_loop_receipt import build_loop_user_text
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1, ProjectionProtocol, resolve_projection_protocol
from app.reference_text_profiles import route as canonical_route


def projection_preview_proof(run: dict, stage, route: dict, *,
                             protocol: ProjectionProtocol | str = PROJECTION_PROTOCOL_V1) -> dict:
    selected = resolve_projection_protocol(protocol)
    canonical = canonical_route(route)
    if canonical is None or canonical != route or stage.round_index != 0:
        raise DomainError('Projection preview requires the initial stage and a canonical profile.', 409)
    if (stage.run_id != run.get('id') or stage.project_id != run.get('project_id')
            or stage.snapshot_id != run.get('snapshot_id')):
        raise DomainError('Projection preview is outside the frozen run.', 409)
    system_text, _, prompt_hash = prompt_contract_for(selected)
    sha = lambda text: hashlib.sha256(text.encode('utf-8')).hexdigest()
    base = stage.initial_bundle
    return {
        'proof_version': selected.proof_version,
        'project_id': stage.project_id, 'run_id': stage.run_id, 'snapshot_id': stage.snapshot_id,
        'question_sha256': sha(stage.question), 'selector_version': selected.selector_version,
        'loop_version': selected.loop_version, 'context_policy': selected.context_policy,
        'profile_version': canonical['profile_version'], 'profile_id': canonical['profile_id'],
        'route_sha256': sha(dumps(canonical)), 'prompt_contract_hash': prompt_hash,
        'initial_projection_input_sha256': base.projection_input_sha256,
        'initial_projection_context_sha256': base.projection_context_sha256,
        'initial_ordered_projection_manifest_sha256': base.ordered_projection_manifest_sha256,
        'first_profile_neutral_input_sha256': sha(dumps([
            'reference-profile-neutral-input-1', system_text,
            build_loop_user_text(stage, [], protocol=selected), []])),
    }
