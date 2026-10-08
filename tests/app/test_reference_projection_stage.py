"""Synthetic append-only projection-stage coverage."""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from app.reference_projection_stage import (
    ProjectionStageError,
    append_projection_stage,
    authenticate_projection_stage,
    prepare_projection_stage,
)
from .test_reference_projection_input import _input


def _base(client, project):
    _db, _run, _documents, _selection, bundle = _input(client, project, [
        ('source.pdf', ['SHEET A1 alpha initial source', 'beta supplemental source']),
    ], question='alpha initial')
    return _db, bundle


def test_append_keeps_initial_rows_and_same_document_new_page_uses_a_new_manifest(client, project):
    db, bundle = _base(client, project)
    initial = prepare_projection_stage(bundle, db, client.app.state.uploads)
    before = initial.context
    assert before['projection_input_version'] == 'reference-projection-stage-1'
    assert before['initial_projection_input_version'] == bundle.projection_input_version
    appended = append_projection_stage(
        initial, [{'tool': 'SEARCH_TEXT', 'query': 'beta supplemental'}], db, client.app.state.uploads)

    assert appended.round_index == 1 and appended.added_page_count == 1
    assert appended.context['rows'][:len(before['rows'])] == before['rows']
    assert appended.context['rows'][-1]['row_ref'] == f'X{len(before["rows"]) + 1}'
    assert len(appended.manifests) == 2
    assert appended.manifests[0]['page_numbers'] == [1]
    assert appended.manifests[1]['page_numbers'] == [2]
    assert appended.manifests[0]['pages'][0]['rows'][0]['row_ref'] == 'R1'
    assert appended.manifests[1]['pages'][0]['rows'][0]['row_ref'] == 'R1'
    assert appended.context['rows'][-1]['binding']['manifest_sha256'] != before['rows'][0]['binding']['manifest_sha256']
    authenticate_projection_stage(appended, db, client.app.state.uploads)


def test_second_batch_with_seen_page_is_detectable_noop_and_bounds_requests(client, project):
    db, bundle = _base(client, project)
    stage = prepare_projection_stage(bundle, db, client.app.state.uploads)
    stage = append_projection_stage(stage, [{'tool': 'SEARCH_TEXT', 'query': 'beta supplemental'}],
                                    db, client.app.state.uploads)
    no_new = append_projection_stage(stage, [{'tool': 'FIND_IDENTIFIER', 'query': 'SHEET A1'}],
                                     db, client.app.state.uploads)
    assert stage.added_page_count == 1
    assert no_new.round_index == 2 and no_new.added_page_count == 0
    assert no_new.request_batches[-1]['requests'][0]['new_page_keys'] == []
    with pytest.raises(ProjectionStageError):
        append_projection_stage(no_new, [{'tool': 'SEARCH_TEXT', 'query': 'another'}], db, client.app.state.uploads)
    with pytest.raises(ProjectionStageError):
        append_projection_stage(stage, [{'tool': 'UNKNOWN', 'query': 'x'}], db, client.app.state.uploads)
    with pytest.raises(ProjectionStageError):
        append_projection_stage(stage, [{'tool': 'FIND_IDENTIFIER', 'query': 'alpha'}],
                                db, client.app.state.uploads)
    with pytest.raises(ProjectionStageError):
        append_projection_stage(stage, [{'tool': 'SEARCH_TEXT', 'query': ' BETA   SUPPLEMENTAL '}],
                                db, client.app.state.uploads)


def test_stage_authentication_replays_ordered_request_selections_exactly(client, project):
    db, bundle = _base(client, project)
    stage = append_projection_stage(
        prepare_projection_stage(bundle, db, client.app.state.uploads),
        [{'tool': 'SEARCH_TEXT', 'query': 'beta supplemental'}], db, client.app.state.uploads)
    authenticate_projection_stage(stage, db, client.app.state.uploads)
    assert (stage.run_id, stage.project_id, stage.snapshot_id) == (
        bundle.run_id, bundle.project_id, bundle.snapshot_id)

    changed = stage.request_batches
    changed[0]['requests'][0]['selection_id'] = 'SEL-forged'
    with pytest.raises(ProjectionStageError):
        authenticate_projection_stage(
            replace(stage, _request_batches_json=json.dumps(changed)), db, client.app.state.uploads)
    with pytest.raises(ProjectionStageError):
        authenticate_projection_stage(
            replace(stage, _request_batches_json=json.dumps([{'requests': ['bad']}])) ,
            db, client.app.state.uploads)
    with pytest.raises(ProjectionStageError):
        authenticate_projection_stage(replace(stage, round_index=True), db, client.app.state.uploads)
