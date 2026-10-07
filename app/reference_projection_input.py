"""Snapshot-bound PDF projection inputs for the internal selector10 Gateway.

There is no public request or persistence entry point here. The builder turns a
locally authorized ``PageSelection`` into a deterministic projection bundle;
authentication rebuilds it from the persisted run and original object bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from app.db import Database, DomainError, dumps
from app.page_selector import PageSelection, select_pages
from app.reference_pdf_projection import build_pdf_projection
from app.reference_extractive import ExtractiveContractError, _parts


VERSION = 'reference-projection-input-1'
_HASH = re.compile(r'^[0-9a-f]{64}$')
_COMPLETE_ROW_REASONS = frozenset({'CLAIM_TEXT_LIMIT', 'QUOTE_TEXT_LIMIT'})


class ProjectionInputError(DomainError):
    pass


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProjectionInputError('Projection input is not serializable.', 409) from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _json_copy(raw: str) -> Any:
    return json.loads(raw)


@dataclass(frozen=True)
class ProjectionInput:
    """Immutable-at-rest bundle with fresh decoded public views per access."""

    projection_input_version: str
    run_id: str
    project_id: str
    snapshot_id: str
    question: str
    projection_input_sha256: str
    projection_context_sha256: str
    ordered_projection_manifest_sha256: str
    rebuild_refs_sha256: str
    _required_parts_json: str
    _context_json: str
    _projection_sources_json: str
    _manifests_json: str
    _rebuild_refs_json: str
    _selection_json: str

    @property
    def required_parts(self) -> list[dict]:
        return _json_copy(self._required_parts_json)

    @property
    def context(self) -> dict:
        return _json_copy(self._context_json)

    @property
    def projection_sources(self) -> list[dict]:
        return _json_copy(self._projection_sources_json)

    @property
    def manifests(self) -> list[dict]:
        return _json_copy(self._manifests_json)

    @property
    def rebuild_refs(self) -> list[dict]:
        return _json_copy(self._rebuild_refs_json)

    @property
    def selection(self) -> dict:
        return _json_copy(self._selection_json)

    def public(self) -> dict:
        """Return a detached value for an offline model-input adapter."""
        return {
            'projection_input_version': self.projection_input_version,
            'run_id': self.run_id, 'project_id': self.project_id,
            'snapshot_id': self.snapshot_id, 'question': self.question,
            'required_parts': self.required_parts, 'context': self.context,
            'projection_input_sha256': self.projection_input_sha256,
            'projection_context_sha256': self.projection_context_sha256,
            'ordered_projection_manifest_sha256': self.ordered_projection_manifest_sha256,
            'projection_sources': self.projection_sources,
            'manifests': self.manifests,
        }


def _run_document_ids(run: dict) -> list[str]:
    value = run.get('document_ids')
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ProjectionInputError('Projection input requires a valid frozen document snapshot.', 409)
    if len(set(value)) != len(value):
        raise ProjectionInputError('Projection input document snapshot is duplicated.', 409)
    return value


def _frozen_run(db: Database, supplied: dict) -> dict:
    """Reload and validate the durable run/document snapshot identity."""
    if not isinstance(supplied, dict) or not isinstance(supplied.get('id'), str):
        raise ProjectionInputError('Projection input run is malformed.', 409)
    persisted = db.one('SELECT * FROM runs WHERE id=?', (supplied['id'],))
    persisted['document_ids'] = (json.loads(persisted['document_ids'])
                                 if isinstance(persisted.get('document_ids'), str)
                                 else persisted.get('document_ids'))
    document_ids = _run_document_ids(persisted)
    placeholders = ','.join('?' for _ in document_ids)
    documents = db.all(
        f'''SELECT id,sha256 FROM documents WHERE project_id=? AND id IN ({placeholders}) ORDER BY id''',
        (persisted['project_id'], *document_ids))
    if len(documents) != len(document_ids) or any(not _HASH.fullmatch(row['sha256']) for row in documents):
        raise ProjectionInputError('Projection input frozen documents are unavailable.', 409)
    snapshot = 'SN-' + hashlib.sha256(dumps(documents).encode()).hexdigest()[:32]
    if persisted.get('snapshot_id') != snapshot:
        raise ProjectionInputError('Projection input run snapshot is inconsistent.', 409)
    supplied_ids = supplied.get('document_ids')
    if isinstance(supplied_ids, str):
        try:
            supplied_ids = json.loads(supplied_ids)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectionInputError('Projection input supplied run snapshot is malformed.', 409) from exc
    if (supplied.get('project_id') != persisted['project_id']
            or supplied.get('snapshot_id') != persisted['snapshot_id']
            or supplied_ids != document_ids):
        raise ProjectionInputError('Projection input supplied run is not the persisted snapshot.', 409)
    return persisted


def _valid_required_parts(value: object) -> list[dict]:
    try:
        _parts(value)
    except ExtractiveContractError as exc:
        raise ProjectionInputError('Projection input required parts are malformed.', 409) from exc
    return _json_copy(_canonical(value))


def _authorized_selection(db: Database, run: dict, selection: object, question: str) -> PageSelection:
    if not isinstance(selection, PageSelection):
        raise ProjectionInputError('Projection input requires a saved page selection.', 409)
    if (selection.run_id != run.get('id') or selection.snapshot_id != run.get('snapshot_id')
            or selection.question != question):
        raise ProjectionInputError('Projection page selection is outside the requested run snapshot.', 409)
    # Rebuild instead of trusting caller-held dataclass values: selection_id
    # includes the selected pages/evidence and catches forged document/page refs.
    rebuilt = select_pages(db, run, question, selector_version=selection.selector_version)
    if (selection.selection_id != rebuilt.selection_id
            or selection.selected_pages != rebuilt.selected_pages
            or selection.evidence_rows != rebuilt.evidence_rows):
        raise ProjectionInputError('Projection page selection does not match the frozen source snapshot.', 409)
    return rebuilt


def _selected_pages(selection: PageSelection, document_ids: list[str]) -> dict[str, list[int]]:
    pages: dict[str, set[int]] = defaultdict(set)
    for page in selection.selected_pages:
        if page.document_id not in document_ids or type(page.page_number) is not int or page.page_number < 1:
            raise ProjectionInputError('Projection selection contains an unauthorized document page.', 409)
        pages[page.document_id].add(page.page_number)
    if not pages:
        raise ProjectionInputError('Projection selection has no source pages.', 409)
    return {document_id: sorted(values) for document_id, values in sorted(pages.items())}


def _documents(db: Database, run: dict, selected: dict[str, list[int]]) -> dict[str, dict]:
    ids = list(selected)
    placeholders = ','.join('?' for _ in ids)
    rows = db.all(
        f'''SELECT id,project_id,name,sha256,object_key FROM documents
            WHERE project_id=? AND id IN ({placeholders}) ORDER BY id''',
        (run['project_id'], *ids))
    found = {row['id']: row for row in rows}
    if set(found) != set(ids):
        raise ProjectionInputError('Projection source document is absent from the frozen project.', 409)
    for document_id, row in found.items():
        if (document_id not in run['document_ids'] or row['project_id'] != run['project_id']
                or not isinstance(row.get('sha256'), str) or not _HASH.fullmatch(row['sha256'])
                or not isinstance(row.get('object_key'), str) or not row['object_key']):
            raise ProjectionInputError('Projection source document identity is malformed.', 409)
    return found


def _source_projection(uploads, document: dict, page_numbers: list[int]) -> tuple[dict, dict]:
    """Read each object once, bind its bytes, then derive its manifest from bytes."""
    try:
        pdf_bytes = uploads.object_path(document).read_bytes()
    except OSError as exc:
        raise ProjectionInputError('Projection source object is unavailable.', 409) from exc
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    if digest != document['sha256']:
        raise ProjectionInputError('Projection source object does not match its frozen document SHA-256.', 409)
    try:
        manifest = build_pdf_projection(pdf_bytes, expected_sha256=digest, page_numbers=page_numbers)
    except Exception as exc:
        raise ProjectionInputError('Projection source PDF cannot be rebuilt.', 409) from exc
    source = {
        'document_id': document['id'], 'pdf_sha256': digest,
        'page_numbers': page_numbers, 'manifest_sha256': manifest['manifest_sha256'],
    }
    rebuild_ref = {**source, 'object_key': document['object_key']}
    return manifest, rebuild_ref


def _geometry_summary(manifest: dict) -> list[dict]:
    result = []
    for page in manifest['pages']:
        reasons = sorted({item.get('reason') for item in page.get('unusable', [])
                          if isinstance(item, dict) and isinstance(item.get('reason'), str)})
        # A character wholly outside a PDF CropBox is not a broken projection
        # of the visible page.  Every other terminal reason means the selected
        # page has an unrepresented native component and must block review.
        blocking = [reason for reason in reasons if reason != 'OUTSIDE_CROPBOX']
        row_reasons = sorted({row.get('reason') for row in page.get('rows', [])
                              if isinstance(row, dict) and row.get('status') != 'BOUND'
                              and row.get('reason') not in _COMPLETE_ROW_REASONS
                              and isinstance(row.get('reason'), str)})
        if blocking or row_reasons or not page.get('rows'):
            result.append({'page_number': page['page_number'],
                           'reasons': reasons + row_reasons + ([] if page.get('rows') else ['NO_NATIVE_TEXT']),
                           'unusable_character_count': len(page['unusable'])})
    return result


def _bundle(run: dict, question: str, required_parts: list[dict], projection_items: list[tuple[dict, dict]],
            selection: PageSelection) -> ProjectionInput:
    manifests = [manifest for manifest, _ in projection_items]
    refs = [ref for _, ref in projection_items]
    sources = [{key: ref[key] for key in ('document_id', 'pdf_sha256', 'page_numbers', 'manifest_sha256')}
               for ref in refs]
    ordered = _hash(['reference-ordered-projection-manifest-1', sources])
    rows = []
    unusable_pages = []
    for manifest, ref in projection_items:
        unusable_pages.extend([
            {'document_id': ref['document_id'], **value}
            for value in _geometry_summary(manifest)
        ])
        for page in manifest['pages']:
            for row in page['rows']:
                reason = row.get('reason')
                selectable = row['status'] == 'BOUND' or reason in _COMPLETE_ROW_REASONS
                binding = {
                    'document_id': ref['document_id'], 'pdf_sha256': ref['pdf_sha256'],
                    'page_number': page['page_number'], 'manifest_sha256': ref['manifest_sha256'],
                    'projection_version': manifest['projection_version'],
                    'source_row_ref': row['row_ref'], 'row_text_sha256': row['text_sha256'],
                    'direction': row['direction'], 'char_ids': row['char_ids'],
                }
                rows.append({
                    'row_ref': '', 'source_row_ref': row['row_ref'],
                    'document_id': ref['document_id'], 'page_number': page['page_number'],
                    'text': row['text'], 'text_sha256': row['text_sha256'],
                    'direction_degrees': row['direction_degrees'], 'char_ids': row['char_ids'],
                    'status': row['status'], **({'reason': reason} if reason else {}),
                    'selectable': selectable, 'binding': binding,
                })
    for index, row in enumerate(rows, 1):
        row['row_ref'] = f'X{index}'
    context = {
        'projection_input_version': VERSION, 'question': question,
        'required_parts': required_parts,
        'projection_complete': not unusable_pages,
        'source_conflicts': list(selection.source_conflicts),
        'page_unusable_summary': unusable_pages,
        'rows': rows,
    }
    context_hash = _hash(context)
    input_hash = _hash(['reference-projection-input-identity-1', question, required_parts, ordered])
    selection_identity = {
        'selection_id': selection.selection_id,
        'selector_version': selection.selector_version,
        'source_conflicts': list(selection.source_conflicts),
    }
    refs_hash = _hash(['reference-projection-rebuild-refs-1', selection_identity, refs])
    return ProjectionInput(
        VERSION, run['id'], run['project_id'], run['snapshot_id'], question,
        input_hash, context_hash, ordered, refs_hash,
        _canonical(required_parts), _canonical(context), _canonical(sources),
        _canonical(manifests), _canonical(refs), _canonical(selection_identity),
    )


def prepare_projection_input(db: Database, uploads, run: dict, page_selection: PageSelection,
                             question: str, required_parts: list[dict]) -> ProjectionInput:
    """Create a deterministic bundle from current frozen-run PDF objects only."""
    if not isinstance(question, str) or not question.strip():
        raise ProjectionInputError('Projection input run or question is malformed.', 409)
    run = _frozen_run(db, run)
    document_ids = _run_document_ids(run)
    parts = _valid_required_parts(required_parts)
    from app.evidence_loop import _answer_parts
    if parts != _answer_parts(question):
        raise ProjectionInputError('Projection input parts do not match the local question contract.', 409)
    selection = _authorized_selection(db, run, page_selection, question)
    selected = _selected_pages(selection, document_ids)
    documents = _documents(db, run, selected)
    items = []
    for document_id, page_numbers in selected.items():
        items.append(_source_projection(uploads, documents[document_id], page_numbers))
    return _bundle(run, question, parts, items, selection)


def authenticate_projection_input(bundle: ProjectionInput, db: Database, uploads) -> None:
    """Re-read frozen objects and require byte-for-byte bundle reconstruction."""
    if not isinstance(bundle, ProjectionInput) or bundle.projection_input_version != VERSION:
        raise ProjectionInputError('Projection input bundle version is unsupported.', 409)
    if not isinstance(bundle.question, str) or not bundle.question.strip():
        raise ProjectionInputError('Projection input question is malformed.', 409)
    parts = _valid_required_parts(bundle.required_parts)
    from app.evidence_loop import _answer_parts
    if parts != _answer_parts(bundle.question):
        raise ProjectionInputError('Projection input parts do not match the local question contract.', 409)
    if not all(isinstance(value, str) and _HASH.fullmatch(value) for value in (
            bundle.projection_input_sha256, bundle.projection_context_sha256,
            bundle.ordered_projection_manifest_sha256, bundle.rebuild_refs_sha256)):
        raise ProjectionInputError('Projection input bundle hashes are malformed.', 409)
    try:
        raw = db.one('SELECT * FROM runs WHERE id=?', (bundle.run_id,))
        raw['document_ids'] = (json.loads(raw['document_ids'])
                               if isinstance(raw.get('document_ids'), str) else raw.get('document_ids'))
        run = _frozen_run(db, raw)
    except (DomainError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProjectionInputError('Projection input frozen run is unavailable.', 409) from exc
    if run.get('project_id') != bundle.project_id or run.get('snapshot_id') != bundle.snapshot_id:
        raise ProjectionInputError('Projection input run identity changed.', 409)
    document_ids = _run_document_ids(run)
    refs = bundle.rebuild_refs
    selection_identity = bundle.selection
    if (not isinstance(selection_identity, dict)
            or set(selection_identity) != {'selection_id', 'selector_version', 'source_conflicts'}
            or not isinstance(selection_identity['selection_id'], str)
            or not isinstance(selection_identity['selector_version'], str)
            or not isinstance(selection_identity['source_conflicts'], list)
            or any(not isinstance(item, str) for item in selection_identity['source_conflicts'])):
        raise ProjectionInputError('Projection input selection identity is malformed.', 409)
    rebuilt_selection = select_pages(
        db, run, bundle.question, selector_version=selection_identity['selector_version'])
    if (rebuilt_selection.selection_id != selection_identity['selection_id']
            or list(rebuilt_selection.source_conflicts) != selection_identity['source_conflicts']):
        raise ProjectionInputError('Projection input source selection changed.', 409)
    if (not isinstance(refs, list) or not refs
            or _hash(['reference-projection-rebuild-refs-1', selection_identity, refs]) != bundle.rebuild_refs_sha256
            or any(not isinstance(item, dict) or set(item) != {
                'document_id', 'pdf_sha256', 'page_numbers', 'manifest_sha256', 'object_key'} for item in refs)):
        raise ProjectionInputError('Projection input rebuild references are malformed.', 409)
    selected = {item['document_id']: item['page_numbers'] for item in refs}
    if len(selected) != len(refs) or any(document_id not in document_ids for document_id in selected):
        raise ProjectionInputError('Projection input rebuild references leave the frozen document snapshot.', 409)
    if selected != _selected_pages(rebuilt_selection, document_ids):
        raise ProjectionInputError('Projection input rebuild pages differ from the trusted selection.', 409)
    documents = _documents(db, run, selected)
    rebuilt_items = []
    for ref in refs:
        document = documents[ref['document_id']]
        if document['sha256'] != ref['pdf_sha256'] or document['object_key'] != ref['object_key']:
            raise ProjectionInputError('Projection input document reference changed.', 409)
        manifest, rebuilt_ref = _source_projection(uploads, document, ref['page_numbers'])
        if rebuilt_ref != ref or manifest['manifest_sha256'] != ref['manifest_sha256']:
            raise ProjectionInputError('Projection input source projection changed.', 409)
        rebuilt_items.append((manifest, rebuilt_ref))
    source_conflicts = bundle.context.get('source_conflicts')
    if (not isinstance(source_conflicts, list) or source_conflicts != selection_identity['source_conflicts']
            or any(not isinstance(item, str) for item in source_conflicts)):
        raise ProjectionInputError('Projection input source conflicts are malformed.', 409)
    rebuilt = _bundle(run, bundle.question, bundle.required_parts, rebuilt_items, rebuilt_selection)
    if rebuilt != bundle:
        raise ProjectionInputError('Projection input bundle does not exactly match frozen sources.', 409)
