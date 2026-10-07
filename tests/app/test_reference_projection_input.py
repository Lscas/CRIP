"""Synthetic coverage for the disconnected PDF-projection input bundle."""
from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import json

import pytest
from reportlab.pdfgen import canvas

from app.db import DomainError
from app.page_selector import select_pages
from app.reference_projection_input import (
    ProjectionInputError,
    authenticate_projection_input,
    prepare_projection_input,
)
from .conftest import upload


def _pdf(*pages: str) -> bytes:
    output = BytesIO()
    drawing = canvas.Canvas(output, pagesize=(max(2400, max(map(len, pages)) * 7), 400))
    drawing.setFont('Helvetica', 9)
    for index, text in enumerate(pages):
        drawing.drawString(40, 300, text)
        if index + 1 < len(pages):
            drawing.showPage(); drawing.setFont('Helvetica', 9)
    drawing.save()
    return output.getvalue()


def _pdf_with_unsupported_middle_char() -> bytes:
    output = BytesIO(); drawing = canvas.Canvas(output, pagesize=(400, 400))
    drawing.drawString(40, 300, 'alpha AB')
    drawing.saveState(); drawing.translate(0, 0)
    drawing.transform(1, 0.25, 0, 1, 0, 0); drawing.drawString(83, 300, 'C')
    drawing.restoreState(); drawing.drawString(90, 300, 'DE')
    drawing.save()
    return output.getvalue()


def _run_with_pages(client, project, entries):
    """Create a frozen run whose parser rows authorize the synthetic PDFs."""
    documents = []
    for entry in entries:
        name, pages = entry[:2]
        raw = entry[2] if len(entry) == 3 else _pdf(*pages)
        documents.append((upload(client, project['id'], name, raw)['document_id'], pages))
    runner = client.app.state.runner; db = client.app.state.db
    run = runner.create(project['id'], analysis_mode='REFERENCE_QA')
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?", (run['id'],))
    rows = []
    results = []
    for document_id, pages in documents:
        for page_number, text in enumerate(pages, 1):
            payload = {
                'evidence_id': f'EV-{document_id}-{page_number}', 'project_id': project['id'],
                'input_snapshot_id': run['snapshot_id'], 'document_id': document_id,
                'raw_text': text, 'locator': {'page_number': page_number, 'bbox': [0, 0, 100, 20]},
                'extraction_method': 'TEXT_LAYER', 'content_basis': 'SOURCE_TEXT', 'text_map': [],
            }
            rows.append((f'{run["id"]}:{document_id}:{page_number}', run['id'], project['id'], document_id,
                         json.dumps(payload), 'PENDING', None, ''))
        results.append((run['id'], document_id, 'SUCCESS', json.dumps({'status': 'SUCCESS', 'pages': []})))
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)', rows)
        connection.executemany('INSERT INTO document_results VALUES(?,?,?,?)', results)
    return db, runner.get(run['id']), documents


def _input(client, project, entries, question='alpha scope'):
    from app.evidence_loop import _answer_parts
    db, run, documents = _run_with_pages(client, project, entries)
    selection = select_pages(db, run, question, selector_version='literal-page-selector-9')
    bundle = prepare_projection_input(
        db, client.app.state.uploads, run, selection, question,
        _answer_parts(question))
    return db, run, documents, selection, bundle


def test_prepare_and_authenticate_multi_pdf_bundle_is_snapshot_bound_and_detached(client, project):
    db, run, documents, _selection, bundle = _input(client, project, [
        ('left.pdf', ['alpha left object with complete condition']),
        ('right.pdf', ['alpha right object with complete condition']),
    ])

    assert bundle.projection_input_version == 'reference-projection-input-1'
    assert bundle.context['projection_complete'] is True
    assert [row['row_ref'] for row in bundle.context['rows']] == ['X1', 'X2']
    assert {row['binding']['document_id'] for row in bundle.context['rows']} == {item[0] for item in documents}
    assert all(row['selectable'] and row['text'] for row in bundle.context['rows'])
    assert set(bundle.context['rows'][0]['binding']) == {
        'document_id', 'pdf_sha256', 'page_number', 'manifest_sha256', 'projection_version',
        'source_row_ref', 'row_text_sha256', 'direction', 'char_ids'}
    assert len(bundle.projection_sources) == len(bundle.manifests) == 2
    original = bundle.context
    original['rows'][0]['text'] = 'mutated caller copy'
    assert bundle.context['rows'][0]['text'] != 'mutated caller copy'
    authenticate_projection_input(bundle, db, client.app.state.uploads)


def test_long_complete_text_limit_stays_selectable_for_review_input(client, project):
    long_text = 'alpha ' + ('X' * 1001)
    db, _run, _documents, _selection, bundle = _input(client, project, [
        ('long.pdf', [long_text]), ('other.pdf', ['alpha second complete source']),
    ])

    row = next(row for row in bundle.context['rows'] if row.get('reason') == 'CLAIM_TEXT_LIMIT')
    assert row['status'] == 'UNUSABLE'
    assert row['reason'] == 'CLAIM_TEXT_LIMIT'
    assert row['selectable'] is True
    assert row['text'].endswith('X' * 1001)
    authenticate_projection_input(bundle, db, client.app.state.uploads)


def test_prepare_rejects_cross_run_or_project_selection_and_auth_rejects_source_or_identity_drift(client, project):
    db, run, documents, selection, bundle = _input(client, project, [('one.pdf', ['alpha one'])])
    other_project = client.post('/api/projects', json={'name': 'Other'}).json()
    other_db, other_run, _docs = _run_with_pages(client, other_project, [('other.pdf', ['alpha other'])])
    other_selection = select_pages(other_db, other_run, 'alpha scope', selector_version='literal-page-selector-9')
    with pytest.raises(ProjectionInputError):
        prepare_projection_input(db, client.app.state.uploads, run, other_selection, 'alpha scope', [{'part_ref': 'P1', 'text': 'one'}])

    with pytest.raises(ProjectionInputError):
        prepare_projection_input(db, client.app.state.uploads, run, selection, 'different question', [{'part_ref': 'P1', 'text': 'one'}])
    with pytest.raises(ProjectionInputError):
        prepare_projection_input(db, client.app.state.uploads, run, selection, 'alpha scope', [])
    with pytest.raises(ProjectionInputError):
        authenticate_projection_input(replace(bundle, projection_input_sha256='0' * 64), db, client.app.state.uploads)
    with pytest.raises(ProjectionInputError):
        authenticate_projection_input(replace(bundle, question='alpha changed'), db, client.app.state.uploads)
    with pytest.raises(ProjectionInputError):
        authenticate_projection_input(
            replace(bundle, _required_parts_json='[{"part_ref":"P2","text":"changed"}]'), db, client.app.state.uploads)
    with pytest.raises(ProjectionInputError):
        authenticate_projection_input(replace(bundle, snapshot_id='SN-' + ('0' * 32)), db, client.app.state.uploads)
    forged_selection = bundle.selection; forged_selection['selection_id'] = 'SEL-forged'
    with pytest.raises(ProjectionInputError):
        authenticate_projection_input(replace(bundle, _selection_json=json.dumps(forged_selection)), db, client.app.state.uploads)
    forged_context = bundle.context; forged_context['rows'][0]['text'] = 'forged text'
    with pytest.raises(ProjectionInputError):
        authenticate_projection_input(replace(bundle, _context_json=json.dumps(forged_context)), db, client.app.state.uploads)

    document = db.one('SELECT * FROM documents WHERE id=?', (documents[0][0],))
    client.app.state.uploads.object_path(document).write_bytes(b'not the frozen PDF')
    with pytest.raises(ProjectionInputError):
        authenticate_projection_input(bundle, db, client.app.state.uploads)


def test_true_projection_geometry_unusable_blocks_review_completeness(client, project):
    db, _run, _documents, _selection, bundle = _input(
        client, project, [('bad.pdf', ['alpha ABCDE'], _pdf_with_unsupported_middle_char())])

    assert bundle.context['projection_complete'] is False
    assert bundle.context['page_unusable_summary']
    authenticate_projection_input(bundle, db, client.app.state.uploads)


def test_coherent_self_hashed_unselected_page_is_not_authority(client, project):
    from app.reference_projection_input import _bundle, _source_projection
    db, run, documents, selection, bundle = _input(client, project, [
        ('two-pages.pdf', ['alpha authorized page'], _pdf('alpha authorized page', 'unselected hidden page'))])
    document = db.one('SELECT * FROM documents WHERE id=?', (documents[0][0],))
    manifest, ref = _source_projection(client.app.state.uploads, document, [2])
    forged = _bundle(run, bundle.question, bundle.required_parts, [(manifest, ref)], selection)
    assert forged.projection_sources[0]['page_numbers'] == [2]
    with pytest.raises(ProjectionInputError, match='trusted selection'):
        authenticate_projection_input(forged, db, client.app.state.uploads)


def test_changing_original_and_document_sha_together_cannot_rewrite_snapshot(client, project):
    import hashlib
    db, run, documents, selection, bundle = _input(client, project, [('original.pdf', ['alpha original'])])
    document = db.one('SELECT * FROM documents WHERE id=?', (documents[0][0],))
    replacement = _pdf('alpha silently changed condition')
    client.app.state.uploads.object_path(document).write_bytes(replacement)
    db.execute('UPDATE documents SET sha256=? WHERE id=?',
               (hashlib.sha256(replacement).hexdigest(), document['id']))
    with pytest.raises(ProjectionInputError, match='snapshot is inconsistent'):
        prepare_projection_input(db, client.app.state.uploads, run, selection,
                                 bundle.question, bundle.required_parts)
    with pytest.raises(ProjectionInputError):
        authenticate_projection_input(bundle, db, client.app.state.uploads)


def test_later_uploaded_document_does_not_expand_or_invalidate_old_snapshot(client, project):
    db, _run, documents, _selection, bundle = _input(client, project, [('original.pdf', ['alpha original'])])
    new = upload(client, project['id'], 'later.pdf', _pdf('alpha new document'))
    assert new['document_id'] not in {item[0] for item in documents}
    authenticate_projection_input(bundle, db, client.app.state.uploads)
    assert new['document_id'] not in {source['document_id'] for source in bundle.projection_sources}


def test_coherent_self_hashed_malformed_required_parts_are_rejected(client, project):
    from app.reference_projection_input import _bundle
    db, run, _documents, selection, bundle = _input(client, project, [('original.pdf', ['alpha original'])])
    forged = _bundle(run, bundle.question, [{'part_ref': 'P1', 'label': 'wrong shape'}],
                     list(zip(bundle.manifests, bundle.rebuild_refs)), selection)
    with pytest.raises(ProjectionInputError):
        authenticate_projection_input(forged, db, client.app.state.uploads)


def test_valid_but_unrelated_parts_cannot_replace_the_question_contract(client, project):
    from app.reference_projection_input import _bundle
    db, run, _documents, selection, bundle = _input(client, project, [('original.pdf', ['alpha original'])])
    unrelated = [{'part_ref': 'P1', 'text': 'Review an unrelated condition'}]
    with pytest.raises(ProjectionInputError, match='local question contract'):
        prepare_projection_input(db, client.app.state.uploads, run, selection, bundle.question, unrelated)
    forged = _bundle(run, bundle.question, unrelated,
                     list(zip(bundle.manifests, bundle.rebuild_refs)), selection)
    with pytest.raises(ProjectionInputError, match='local question contract'):
        authenticate_projection_input(forged, db, client.app.state.uploads)
