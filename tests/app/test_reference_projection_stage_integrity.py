"""Adversarial replay coverage for the append-only projection stage."""
from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import json

import pytest
from reportlab.pdfgen import canvas

from app.reference_projection_decision import ProjectionDecisionError, validate_projection_decision

from app.reference_projection_stage import (
    ProjectionStageError,
    append_projection_stage,
    authenticate_projection_stage,
    prepare_projection_stage,
)
from .conftest import upload
from .test_reference_projection_input import _input, _pdf


def _pdf_with_unsupported_second_page() -> bytes:
    output = BytesIO(); drawing = canvas.Canvas(output, pagesize=(400, 400))
    drawing.drawString(40, 300, 'alpha initial')
    drawing.showPage(); drawing.setFont('Helvetica', 9)
    drawing.drawString(40, 300, 'beta AB')
    drawing.saveState(); drawing.transform(1, 0.25, 0, 1, 0, 0)
    drawing.drawString(83, 300, 'C'); drawing.restoreState()
    drawing.drawString(90, 300, 'DE'); drawing.save()
    return output.getvalue()


def _stage(client, project):
    db, _run, _documents, _selection, bundle = _input(client, project, [
        ('source.pdf', ['alpha initial', 'beta appended source']),
    ], question='alpha initial')
    stage = append_projection_stage(
        prepare_projection_stage(bundle, db, client.app.state.uploads),
        [{'tool': 'SEARCH_TEXT', 'query': 'beta appended'}], db, client.app.state.uploads)
    return db, bundle, stage


@pytest.mark.parametrize('target', [
    'appended_text', 'appended_binding', 'manifest_hash', 'manifest_page_list',
    'request_order', 'request_conflicts',
])
def test_append_replay_rejects_every_forged_source_boundary(client, project, target):
    db, _bundle, stage = _stage(client, project)
    fields = {}
    if target == 'appended_text':
        value = stage.context; value['rows'][-1]['text'] = 'forged'; fields['_context_json'] = json.dumps(value)
    elif target == 'appended_binding':
        value = stage.context; value['rows'][-1]['binding']['pdf_sha256'] = '0' * 64; fields['_context_json'] = json.dumps(value)
    elif target == 'manifest_hash':
        value = stage.rebuild_refs; value[-1]['manifest_sha256'] = '0' * 64; fields['_rebuild_refs_json'] = json.dumps(value)
    elif target == 'manifest_page_list':
        value = stage.projection_sources; value[-1]['page_numbers'] = [99]; fields['_projection_sources_json'] = json.dumps(value)
    elif target == 'request_order':
        value = stage.request_batches; value[0]['requests'][0]['request_ordinal'] = 2; fields['_request_batches_json'] = json.dumps(value)
    else:
        value = stage.request_batches; value[0]['requests'][0]['source_conflicts'] = ['hidden']; fields['_request_batches_json'] = json.dumps(value)
    with pytest.raises(ProjectionStageError):
        authenticate_projection_stage(replace(stage, **fields), db, client.app.state.uploads)


def test_same_batch_queries_selecting_same_page_are_deduplicated(client, project):
    db, _run, _documents, _selection, bundle = _input(client, project, [
        ('source.pdf', ['alpha initial', 'beta gamma appended']),
    ], question='alpha initial')
    result = append_projection_stage(
        prepare_projection_stage(bundle, db, client.app.state.uploads),
        [{'tool': 'SEARCH_TEXT', 'query': 'beta'}, {'tool': 'SEARCH_TEXT', 'query': 'gamma'}],
        db, client.app.state.uploads)
    requests = result.request_batches[0]['requests']
    assert result.added_page_count == 1
    assert len(requests[0]['new_page_keys']) == 1
    assert requests[1]['new_page_keys'] == []
    authenticate_projection_stage(result, db, client.app.state.uploads)


def test_multi_pdf_append_assigns_unique_x_rows_after_base_prefix(client, project):
    db, _run, _documents, _selection, bundle = _input(client, project, [
        ('a.pdf', ['alpha initial', 'beta a']),
        ('b.pdf', ['unrelated', 'beta b']),
    ], question='alpha initial')
    base = prepare_projection_stage(bundle, db, client.app.state.uploads)
    result = append_projection_stage(base, [{'tool': 'SEARCH_TEXT', 'query': 'beta'}],
                                     db, client.app.state.uploads)
    appended = result.context['rows'][len(base.context['rows']):]
    assert [row['row_ref'] for row in result.context['rows']] == [f'X{i}' for i in range(1, len(result.context['rows']) + 1)]
    assert len(appended) == 2
    assert len({row['binding']['document_id'] for row in appended}) == 2
    authenticate_projection_stage(result, db, client.app.state.uploads)


def test_later_upload_is_outside_frozen_stage_snapshot(client, project):
    db, _bundle, stage = _stage(client, project)
    later = upload(client, project['id'], 'later.pdf', _pdf('beta later source'))
    authenticate_projection_stage(stage, db, client.app.state.uploads)
    assert later['document_id'] not in {source['document_id'] for source in stage.projection_sources}


@pytest.mark.parametrize('kind', ['no_native_text', 'unsupported_geometry'])
def test_append_projection_incompleteness_blocks_review(client, project, kind):
    if kind == 'no_native_text':
        raw = _pdf('alpha initial', '')
    else:
        raw = _pdf_with_unsupported_second_page()
    pages = ['alpha initial', 'beta expected text'] if kind == 'no_native_text' else ['alpha initial', 'beta ABCDE']
    db, _run, _documents, _selection, bundle = _input(
        client, project, [('source.pdf', pages, raw)], question='alpha initial')
    stage = append_projection_stage(prepare_projection_stage(bundle, db, client.app.state.uploads),
                                    [{'tool': 'SEARCH_TEXT', 'query': 'beta'}],
                                    db, client.app.state.uploads)
    assert stage.context['projection_complete'] is False
    assert stage.context['page_unusable_summary']
    with pytest.raises(ProjectionDecisionError, match='projection_decision_incomplete'):
        validate_projection_decision({
            'contract_version': 'project-projection-decision-1',
            'projection_input_sha256': stage.projection_input_sha256,
            'status': 'REVIEW_REQUIRED', 'reason_code': 'OBJECT_CONDITION_REVIEW_REQUIRED',
            'missing_facts': [], 'requests': [],
            'selections': [{'row_ref': 'X1', 'part_refs': ['P1']}],
        }, stage)


def test_long_complete_appended_row_stays_selectable(client, project):
    long_text = 'beta ' + ('Z' * 1001)
    db, _run, _documents, _selection, bundle = _input(client, project, [
        ('source.pdf', ['alpha initial', long_text]),
    ], question='alpha initial')
    stage = append_projection_stage(prepare_projection_stage(bundle, db, client.app.state.uploads),
                                    [{'tool': 'SEARCH_TEXT', 'query': 'beta'}],
                                    db, client.app.state.uploads)
    appended = stage.context['rows'][-1]
    assert appended['reason'] == 'CLAIM_TEXT_LIMIT'
    assert appended['selectable'] is True and appended['text'] == long_text
    assert stage.context['projection_complete'] is True
