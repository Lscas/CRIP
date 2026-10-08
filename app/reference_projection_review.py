"""Private source-review derivation and immutable-byte rendering.

Precondition: record and stage have returned together from the complete execution
authenticator. Only the full-source review wrapper may call this private builder.
No PDF reads, writes, settings, model calls or public routes occur here.
"""
from __future__ import annotations

import hashlib
import json
import math
from io import BytesIO

from app.db import DomainError
from app.visual_pipeline import PDF_CROP_COORDINATE_SYSTEM, pdf_cropbox_local_bbox, render_visual_png


VERSION = 'reference-projection-review-view-1'


def _reject():
    raise DomainError('Projection review source binding is inconsistent.', 409)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _one(values):
    if len(values) != 1:
        _reject()
    return values[0]


def _geometry(page, row):
    box, cropbox = row.get('bbox'), page.get('cropbox')
    for value in (box, cropbox):
        if (not isinstance(value, list) or len(value) != 4
                or any(type(number) not in (int, float) or not math.isfinite(number)
                       for number in value)):
            _reject()
    rotation = page.get('rotation')
    if type(rotation) is not int or rotation not in (0, 90, 180, 270):
        _reject()
    width, height = cropbox[2] - cropbox[0], cropbox[3] - cropbox[1]
    local = pdf_cropbox_local_bbox(box, cropbox)
    if not (0 <= local[0] < local[2] <= width and 0 <= local[1] < local[3] <= height):
        _reject()
    # Padding is only a viewing aid; exact source bbox remains separately bound.
    crop = [max(0., local[0] - 4), max(0., local[1] - 4),
            min(width, local[2] + 4), min(height, local[3] + 4)]
    if crop[2] - crop[0] < 8 or crop[3] - crop[1] < 8:
        crop = None  # Renderer cannot safely crop a tiny page; show full page.
    return {'coordinate_system': PDF_CROP_COORDINATE_SYSTEM,
            'page_width': width, 'page_height': height, 'rotation': rotation,
            'bbox': local, 'render_crop_bbox': crop,
            'render_mode': 'SOURCE_CROP' if crop is not None else 'FULL_PAGE'}


def _build_review_view_from_authenticated_stage(record, stage, *, result_id, result_hash,
                                                document_names):
    """Pure derivation, not proof of authentication; never expose to API callers.

Even here, bind each selected row to its exact document/manifest/page/native
characters. Never return a stage, object key, unselected row or provider output.
"""
    packet = record.get('review_packet')
    if (record.get('projection_execution_version') != 'reference-projection-execution-2'
            or record.get('loop_version') != 'project-projection-loop-2'
            or record.get('status') != 'REVIEW_REQUIRED'
            or not isinstance(packet, dict)
            or packet.get('packet_version') != 'projection-review-packet-2'
            or record.get('run_id') != stage.run_id
            or record.get('snapshot_id') != stage.snapshot_id
            or record.get('question') != stage.question
            or packet.get('projection_input_sha256') != stage.projection_input_sha256
            or packet.get('projection_context_sha256') != stage.projection_context_sha256):
        _reject()
    rows = stage.context['rows']
    manifests, refs = stage.manifests, stage.rebuild_refs
    if len(manifests) != len(refs):
        _reject()
    selections = []
    for ordinal, binding in enumerate(packet['source_bindings'], 1):
        selected = _one([row for row in rows if row['row_ref'] == binding['row_ref']])
        expected_binding = {**selected['binding'], 'row_ref': selected['row_ref'],
                            'part_refs': binding['part_refs']}
        if _json(binding) != _json(expected_binding) or not selected['selectable']:
            _reject()
        matching = [(manifest, ref) for manifest, ref in zip(manifests, refs)
                    if ref['document_id'] == binding['document_id']
                    and ref['manifest_sha256'] == binding['manifest_sha256']]
        manifest, ref = _one(matching)
        if (manifest['manifest_sha256'] != binding['manifest_sha256']
                or manifest['pdf_sha256'] != binding['pdf_sha256']
                or ref['pdf_sha256'] != binding['pdf_sha256']
                or manifest['projection_version'] != binding['projection_version']):
            _reject()
        page = _one([value for value in manifest['pages']
                     if value['page_number'] == binding['page_number']])
        row = _one([value for value in page['rows']
                    if value['row_ref'] == binding['source_row_ref']])
        if (row['text'] != selected['text']
                or row['text_sha256'] != binding['row_text_sha256']
                or selected['text_sha256'] != binding['row_text_sha256']
                or hashlib.sha256(row['text'].encode('utf-8')).hexdigest() != row['text_sha256']
                or _json(row['direction']) != _json(binding['direction'])
                or row['char_ids'] != binding['char_ids']
                or row['char_ids'] != selected['char_ids']):
            _reject()
        name = document_names.get(binding['document_id'])
        if not isinstance(name, str) or not name:
            _reject()
        selections.append({'ordinal': ordinal, **binding, 'document_name': name,
                           'text': row['text'], 'geometry': _geometry(page, row)})
    body = {'view_version': VERSION, 'result_id': result_id, 'result_hash': result_hash,
            'result_kind': record['result_kind'], 'run_id': stage.run_id,
            'snapshot_id': stage.snapshot_id, 'question': stage.question,
            'projection_execution_version': record['projection_execution_version'],
            'loop_version': record['loop_version'], 'status': 'REVIEW_REQUIRED',
            'selections': selections, 'object_condition_relations_verified': False,
            'answer_completeness_verified': False}
    view = {**body, 'review_view_sha256': hashlib.sha256(_json(body).encode('utf-8')).hexdigest()}
    return json.loads(_json(view))


def _render_review_source_from_authenticated_bytes(pdf_bytes, source):
    """Render only this immutable, rehashed source after full view authentication.

The store reads the server-selected upload object once into bytes,
then calls here; no caller-supplied path, bbox or source is a public API input.
This helper alone does NOT authenticate the supplied view/source identity.
"""
    if (type(pdf_bytes) is not bytes
            or hashlib.sha256(pdf_bytes).hexdigest() != source['pdf_sha256']):
        raise DomainError('Projection review source bytes changed.', 409)
    geometry = source['geometry']
    if geometry['coordinate_system'] != PDF_CROP_COORDINATE_SYSTEM:
        _reject()
    try:
        result = render_visual_png(BytesIO(pdf_bytes), 'authenticated-source.pdf',
                                   source['page_number'], geometry['render_crop_bbox'])
    except (ValueError, OSError) as exc:
        raise DomainError('Projection review source cannot be rendered.', 409) from exc
    if result[1:] != (geometry['page_width'], geometry['page_height'], PDF_CROP_COORDINATE_SYSTEM):
        _reject()
    return result
