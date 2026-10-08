"""Append-only offline projection-source stages for bounded supplement chains."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from app.db import Database, DomainError
from app.page_selector import LAYOUT_BOUND_SELECTOR_VERSION, select_pages
from app.reference_projection_input import (
    ProjectionInput, ProjectionInputError, VERSION as INPUT_VERSION,
    _canonical, _documents, _frozen_run, _geometry_summary, _hash,
    _run_document_ids, _selected_pages, _source_projection,
    authenticate_projection_input,
)
from app.reference_projection_decision import _request_key


VERSION = 'reference-projection-stage-1'
_TOOLS = frozenset({'FIND_IDENTIFIER', 'SEARCH_TEXT'})
_COMPLETE_REASONS = frozenset({'CLAIM_TEXT_LIMIT', 'QUOTE_TEXT_LIMIT'})
_EXPLICIT_IDENTIFIER = re.compile(
    r'(?i)\b(?:SHEET|DRAWING|DWG\.?|SECTION|PARAGRAPH|PARA\.?|CLAUSE|ARTICLE|RFI|SUBMITTAL)'
    r'\s*(?:NO\.?\s*)?[:#-]?\s*[A-Z0-9][A-Z0-9._/ -]{0,31}')


class ProjectionStageError(DomainError):
    pass


def _copy(raw: str) -> Any:
    return json.loads(raw)


@dataclass(frozen=True)
class ProjectionStage:
    """A stage stores JSON, returning detached values to callers."""

    stage_version: str
    initial_bundle: ProjectionInput
    round_index: int
    initial_projection_input_sha256: str
    initial_projection_context_sha256: str
    initial_ordered_projection_manifest_sha256: str
    parent_projection_input_sha256: str | None
    projection_input_sha256: str
    projection_context_sha256: str
    ordered_projection_manifest_sha256: str
    added_page_count: int
    _request_batches_json: str
    _context_json: str
    _projection_sources_json: str
    _manifests_json: str
    _rebuild_refs_json: str

    @property
    def question(self) -> str: return self.initial_bundle.question

    @property
    def run_id(self) -> str: return self.initial_bundle.run_id

    @property
    def project_id(self) -> str: return self.initial_bundle.project_id

    @property
    def snapshot_id(self) -> str: return self.initial_bundle.snapshot_id

    @property
    def required_parts(self) -> list[dict]: return self.initial_bundle.required_parts

    @property
    def request_batches(self) -> list[dict]: return _copy(self._request_batches_json)

    @property
    def context(self) -> dict: return _copy(self._context_json)

    @property
    def projection_sources(self) -> list[dict]: return _copy(self._projection_sources_json)

    @property
    def manifests(self) -> list[dict]: return _copy(self._manifests_json)

    @property
    def rebuild_refs(self) -> list[dict]: return _copy(self._rebuild_refs_json)


def _request(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {'tool', 'query'}
            or value.get('tool') not in _TOOLS or not isinstance(value.get('query'), str)
            or not 2 <= len(value['query'].strip()) or len(value['query']) > 240
            or (value['tool'] == 'FIND_IDENTIFIER'
                and not _EXPLICIT_IDENTIFIER.search(value['query']))):
        raise ProjectionStageError('Projection supplement request is malformed.', 409)
    return {'tool': value['tool'], 'query': value['query']}


def _keys(selection) -> list[list[object]]:
    values = [[page.document_id, page.page_number] for page in selection.selected_pages]
    if any(not isinstance(document_id, str) or type(page) is not int or page < 1
           for document_id, page in values):
        raise ProjectionStageError('Projection supplement selection has an invalid page.', 409)
    return [[document_id, page] for document_id, page in sorted(set(map(tuple, values)))]


def _union(values: list[str], additions: list[str]) -> list[str]:
    output = list(values)
    for value in additions:
        if value not in output: output.append(value)
    return output


def _row_values(manifest: dict, ref: dict, start: int) -> tuple[list[dict], list[dict]]:
    rows = []; incomplete = []
    for page in manifest['pages']:
        for summary in _geometry_summary({'pages': [page]}):
            incomplete.append({'document_id': ref['document_id'], **summary})
        for row in page['rows']:
            reason = row.get('reason')
            binding = {
                'document_id': ref['document_id'], 'pdf_sha256': ref['pdf_sha256'],
                'page_number': page['page_number'], 'manifest_sha256': ref['manifest_sha256'],
                'projection_version': manifest['projection_version'],
                'source_row_ref': row['row_ref'], 'row_text_sha256': row['text_sha256'],
                'direction': row['direction'], 'char_ids': row['char_ids'],
            }
            rows.append({
                'row_ref': f'X{start + len(rows) + 1}', 'source_row_ref': row['row_ref'],
                'document_id': ref['document_id'], 'page_number': page['page_number'],
                'text': row['text'], 'text_sha256': row['text_sha256'],
                'direction_degrees': row['direction_degrees'], 'char_ids': row['char_ids'],
                'status': row['status'], **({'reason': reason} if reason else {}),
                'selectable': row['status'] == 'BOUND' or reason in _COMPLETE_REASONS,
                'binding': binding,
            })
    return rows, incomplete


def _build(base: ProjectionInput, batches: list[dict], appended: list[tuple[dict, dict]],
           *, parent_hash: str | None) -> ProjectionStage:
    base_context = base.context
    rows = list(base_context['rows'])
    conflicts = list(base_context['source_conflicts'])
    incomplete = list(base_context['page_unusable_summary'])
    for manifest, ref in appended:
        added, bad = _row_values(manifest, ref, len(rows))
        rows.extend(added); incomplete.extend(bad)
    for batch in batches:
        for request in batch['requests']:
            conflicts = _union(conflicts, request['source_conflicts'])
    context = {**base_context, 'projection_input_version': VERSION,
               'initial_projection_input_version': base.projection_input_version,
               'rows': rows, 'source_conflicts': conflicts,
               'page_unusable_summary': incomplete,
               'projection_complete': not incomplete}
    sources = base.projection_sources + [
        {key: ref[key] for key in ('document_id', 'pdf_sha256', 'page_numbers', 'manifest_sha256')}
        for _, ref in appended]
    manifests = base.manifests + [manifest for manifest, _ in appended]
    refs = base.rebuild_refs + [ref for _, ref in appended]
    ordered = _hash(['reference-ordered-projection-stage-manifests-1', sources])
    identity = ['reference-projection-stage-input-1', base.projection_input_sha256,
                batches, ordered]
    return ProjectionStage(
        VERSION, base, len(batches), base.projection_input_sha256,
        base.projection_context_sha256, base.ordered_projection_manifest_sha256,
        parent_hash, _hash(identity), _hash(context), ordered,
        # This is the newly appended batch's count.  A zero after an earlier
        # successful batch is a terminal, machine-checkable no-new-page result.
        (sum(len(request['new_page_keys']) for request in batches[-1]['requests']) if batches else 0),
        _canonical(batches), _canonical(context), _canonical(sources), _canonical(manifests), _canonical(refs),
    )


def prepare_projection_stage(base: ProjectionInput, db: Database, uploads) -> ProjectionStage:
    """Authenticate an unchanged initial bundle and wrap it as stage zero."""
    try:
        authenticate_projection_input(base, db, uploads)
    except ProjectionInputError as exc:
        raise ProjectionStageError('Projection initial bundle is not authentic.', 409) from exc
    return _build(base, [], [], parent_hash=None)


def _append_batch(stage: ProjectionStage, requests: list[dict], db: Database, uploads) -> tuple[list[dict], list[tuple[dict, dict]]]:
    if not isinstance(requests, list) or not 1 <= len(requests) <= 2:
        raise ProjectionStageError('Projection supplement batch must contain one or two requests.', 409)
    normalized = [_request(item) for item in requests]
    previous = {_request_key(item) for batch in stage.request_batches for item in batch['requests']}
    if len({_request_key(item) for item in normalized}) != len(normalized) or any(
            _request_key(item) in previous for item in normalized):
        raise ProjectionStageError('Projection supplement request is duplicated.', 409)
    stored_ids = db.one('SELECT document_ids FROM runs WHERE id=?', (stage.initial_bundle.run_id,))['document_ids']
    if isinstance(stored_ids, str):
        try:
            stored_ids = json.loads(stored_ids)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectionStageError('Projection supplement run snapshot is malformed.', 409) from exc
    run = _frozen_run(db, {'id': stage.initial_bundle.run_id,
                           'project_id': stage.initial_bundle.project_id,
                           'snapshot_id': stage.initial_bundle.snapshot_id,
                           'document_ids': _run_document_ids({'document_ids': stored_ids})})
    selector = stage.initial_bundle.selection['selector_version']
    if selector != LAYOUT_BOUND_SELECTOR_VERSION:
        raise ProjectionStageError('Projection supplement requires the v9 page selector.', 409)
    seen = {(item['document_id'], page)
            for source in stage.projection_sources
            for item in [source]
            for page in item['page_numbers']}
    batch_requests = []; added = []
    for ordinal, request in enumerate(normalized, 1):
        selection = select_pages(db, run, request['query'], selector_version=selector,
                                 scope_question=stage.question)
        selected_keys = _keys(selection)
        new_keys = [key for key in selected_keys if tuple(key) not in seen]
        for key in new_keys: seen.add(tuple(key))
        batch_requests.append({**request, 'request_ordinal': ordinal,
                               'selection_id': selection.selection_id,
                               'selected_page_keys': selected_keys, 'new_page_keys': new_keys,
                               'source_conflicts': list(selection.source_conflicts)})
        grouped: dict[str, list[int]] = {}
        for document_id, page in new_keys: grouped.setdefault(document_id, []).append(page)
        documents = _documents(db, run, {key: sorted(value) for key, value in grouped.items()}) if grouped else {}
        for document_id in sorted(grouped):
            added.append(_source_projection(uploads, documents[document_id], sorted(grouped[document_id])))
    return batch_requests, added


def append_projection_stage(stage: ProjectionStage, requests: list[dict], db: Database, uploads) -> ProjectionStage:
    """Append one replayable request batch; never mutate or recompute old rows."""
    authenticate_projection_stage(stage, db, uploads)
    if stage.round_index >= 2:
        raise ProjectionStageError('Projection supplement chain reached its two-batch bound.', 409)
    batch, added = _append_batch(stage, requests, db, uploads)
    old_appended = list(zip(stage.manifests[len(stage.initial_bundle.manifests):],
                            stage.rebuild_refs[len(stage.initial_bundle.rebuild_refs):]))
    return _build(stage.initial_bundle, stage.request_batches + [{'requests': batch}], old_appended + added,
                  parent_hash=stage.projection_input_sha256)


def authenticate_projection_stage(stage: ProjectionStage, db: Database, uploads) -> None:
    """Rebuild base, every settled supplement selection, and all append manifests."""
    if not isinstance(stage, ProjectionStage) or stage.stage_version != VERSION:
        raise ProjectionStageError('Projection stage version is unsupported.', 409)
    try:
        authenticate_projection_input(stage.initial_bundle, db, uploads)
    except ProjectionInputError as exc:
        raise ProjectionStageError('Projection initial bundle is not authentic.', 409) from exc
    if (stage.initial_projection_input_sha256 != stage.initial_bundle.projection_input_sha256
            or stage.initial_projection_context_sha256 != stage.initial_bundle.projection_context_sha256
            or stage.initial_ordered_projection_manifest_sha256 != stage.initial_bundle.ordered_projection_manifest_sha256
            or type(stage.round_index) is not int or not 0 <= stage.round_index <= 2
            or len(stage.request_batches) != stage.round_index):
        raise ProjectionStageError('Projection stage initial identity is inconsistent.', 409)
    rebuilt = prepare_projection_stage(stage.initial_bundle, db, uploads)
    for batch in stage.request_batches:
        if not isinstance(batch, dict) or set(batch) != {'requests'}:
            raise ProjectionStageError('Projection stage request batch is malformed.', 409)
        if (not isinstance(batch.get('requests'), list)
                or any(not isinstance(item, dict) for item in batch['requests'])):
            raise ProjectionStageError('Projection stage request batch is malformed.', 409)
        raw_requests = [{'tool': item.get('tool'), 'query': item.get('query')}
                        for item in batch['requests']]
        requests, added = _append_batch(rebuilt, raw_requests, db, uploads)
        if batch != {'requests': requests}:
            raise ProjectionStageError('Projection stage request replay changed.', 409)
        old_appended = list(zip(rebuilt.manifests[len(rebuilt.initial_bundle.manifests):],
                                rebuilt.rebuild_refs[len(rebuilt.initial_bundle.rebuild_refs):]))
        rebuilt = _build(rebuilt.initial_bundle, rebuilt.request_batches + [{'requests': requests}],
                         old_appended + added, parent_hash=rebuilt.projection_input_sha256)
    if rebuilt != stage:
        raise ProjectionStageError('Projection stage does not exactly match frozen sources.', 409)
