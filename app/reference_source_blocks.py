"""Fail-closed, extractive source excerpts for a future Reference candidate path.

This module deliberately has no selector, database, model, or object-relation
authority.  A B reference is only a compact alias for a fully reconstructable
raw-text excerpt; it never establishes an engineering object, a leader-line
relationship, or answer support.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from collections import defaultdict

from app.page_selector import _binding_words, _valid_layout_member


VERSION = 'source-excerpt-blocks-1'
MAX_PUBLIC_CITATIONS = 4
MAX_PUBLIC_QUOTE_CHARS = 1800


class SourceBlockError(ValueError):
    pass


class _SegmentCoverageError(ValueError):
    pass


def _stable(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_stable(value).encode('utf-8')).hexdigest()


def _unusable(row: dict, reason: str) -> dict:
    return {
        'block_ref': None,
        'document_id': row.get('document_id'),
        'page_number': (row.get('locator') or {}).get('page_number')
        if isinstance(row.get('locator'), dict) else None,
        'text': row.get('raw_text') if isinstance(row.get('raw_text'), str) else '',
        'spans': [], 'status': 'UNUSABLE', 'reason': reason,
    }


def _rotation_like(item: dict) -> bool:
    """Only axis-aligned native spans are usable; unknown orientation is unsafe."""
    for key in ('rotation', 'angle', 'rotation_degrees'):
        value = item.get(key)
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                  or not math.isfinite(float(value)) or float(value) != 0.0):
            return True
    for key in ('writing_mode', 'direction', 'orientation', 'transform', 'matrix'):
        if key in item:
            return True
    return False


def _strict_words(row: dict, input_index: int) -> tuple[list[dict] | None, str | None]:
    """Validate every nonblank source character before any excerpt is exposed."""
    try:
        inherited, source = _binding_words(row, input_index)
    except Exception:
        return None, 'INVALID_SOURCE'
    if source.get('binding_status') != 'BOUND':
        return None, source.get('binding_reason', 'UNBOUND')
    text = row['raw_text']; mapping = row.get('text_map')
    if not isinstance(mapping, list) or not mapping:
        return None, 'MISSING_TEXT_MAP'
    spans = []
    words = []
    seen = set()
    for ordinal, item in enumerate(mapping):
        if not isinstance(item, dict) or _rotation_like(item):
            return None, 'GEOMETRY_REQUIRES_DIRECTION_METADATA'
        valid = _valid_layout_member(item, text)
        if valid is None:
            return None, 'INVALID_TEXT_MAP'
        start, end, bbox = valid
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return None, 'GEOMETRY_REQUIRES_DIRECTION_METADATA'
        token = text[start:end]
        nonblank_count = sum(not character.isspace() for character in token)
        if (nonblank_count >= 3
                and (bbox[3] - bbox[1]) > (bbox[2] - bbox[0]) * 2.0):
            return None, 'GEOMETRY_REQUIRES_DIRECTION_METADATA'
        key = (start, end)
        if key in seen:
            return None, 'DUPLICATE_SPAN'
        seen.add(key); spans.append((start, end))
        if token.strip():
            words.append({'start': start, 'end': end, 'text': token, 'bbox': bbox,
                          'ordinal': ordinal, 'evidence_id': row['evidence_id'],
                          'document_id': row['document_id'], 'page_number': source['page_number'],
                          'input_index': input_index})
    ordered = sorted(spans)
    if any(previous[1] > current[0] for previous, current in zip(ordered, ordered[1:])):
        return None, 'OVERLAPPING_SPAN'
    covered = [False] * len(text)
    for start, end in spans:
        for index in range(start, end):
            covered[index] = True
    if any(not character.isspace() and not covered[index]
           for index, character in enumerate(text)):
        return None, 'UNMAPPED_NONBLANK_TEXT'
    if not words:
        return None, 'NO_NONBLANK_MAPPED_TOKEN'
    # A page may have independent columns.  Verify offset order within each
    # native lane, never across lanes whose y coordinates can interleave.
    for lane in _lanes(words):
        geometric = sorted(lane, key=lambda item: (item['bbox'][1], item['bbox'][0], item['ordinal']))
        if any(previous['start'] > current['start'] for previous, current in zip(geometric, geometric[1:])):
            return None, 'GEOMETRY_REQUIRES_DIRECTION_METADATA'
    return words, None


def _fragments(row: dict, words: list[dict]) -> list[dict]:
    """Make contiguous parent-text excerpts; never synthesize cross-source spans."""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (item['bbox'][1], item['bbox'][0], item['ordinal'])):
        top = word['bbox'][1]; height = max(1.0, word['bbox'][3] - top)
        if lines:
            anchor = sum(item['bbox'][1] for item in lines[-1]) / len(lines[-1])
            if abs(top - anchor) <= max(2.0, min(5.0, height * .45)):
                lines[-1].append(word); continue
        lines.append([word])
    fragments = []
    for line in lines:
        segment: list[dict] = []
        for word in sorted(line, key=lambda item: item['bbox'][0]):
            if segment:
                previous = segment[-1]
                gap = word['bbox'][0] - previous['bbox'][2]
                height = max(previous['bbox'][3] - previous['bbox'][1],
                             word['bbox'][3] - word['bbox'][1], 1.0)
                if gap > max(24.0, height * 2.5):
                    fragments.append(_fragment(row, segment)); segment = []
            segment.append(word)
        if segment:
            fragments.append(_fragment(row, segment))
    return fragments


def _fragment(row: dict, words: list[dict]) -> dict:
    start = min(item['start'] for item in words)
    end = max(item['end'] for item in words)
    text = row['raw_text']
    # Include only trailing whitespace.  Crossing another nonblank token would
    # join distinct source facts and would no longer be an exact contiguous span.
    while end < len(text) and text[end].isspace():
        end += 1
    covered = set()
    for word in words:
        covered.update(range(word['start'], word['end']))
    if any(not character.isspace() and index not in covered
           for index, character in enumerate(text[start:end], start)):
        # Geometry selected A/B but not an interleaved parent-text X.  There
        # is no safe way to quote the min..max range without exposing X, so
        # reject the whole source rather than silently clipping or absorbing it.
        raise _SegmentCoverageError('SEGMENT_WOULD_OMIT_NONBLANK_TEXT')
    boxes = [item['bbox'] for item in words]
    return {
        'evidence_id': row['evidence_id'], 'document_id': row['document_id'],
        'page_number': words[0]['page_number'], 'start': start, 'end': end,
        'text': text[start:end], 'bbox': [min(box[0] for box in boxes), min(box[1] for box in boxes),
                                            max(box[2] for box in boxes), max(box[3] for box in boxes)],
        'input_index': words[0]['input_index'],
    }


def _same_reading_lane(before: dict, current: dict) -> bool:
    vertical_gap = current['bbox'][1] - before['bbox'][3]
    height = max(before['bbox'][3] - before['bbox'][1], current['bbox'][3] - current['bbox'][1], 1.0)
    if vertical_gap < -max(2.0, height * .45) or vertical_gap > max(36.0, height * 3.0):
        return False
    overlap = min(before['bbox'][2], current['bbox'][2]) - max(before['bbox'][0], current['bbox'][0])
    return overlap >= 0


def _lanes(values: list[dict]) -> list[list[dict]]:
    """Use the v9-style left-edge column structure before reading by y.

    The fixed 80-point tolerance is a column-membership guard, not an inferred
    relationship.  A fragment that cannot share that horizontal lane is never
    joined merely because it is adjacent in vertical page order.
    """
    lanes: list[list[dict]] = []
    for value in sorted(values, key=lambda item: (item['bbox'][0], item['bbox'][1], item.get('input_index', 0), item['start'])):
        left = value['bbox'][0]
        if lanes and left - lanes[-1][0]['bbox'][0] <= 80.0:
            lanes[-1].append(value)
        else:
            lanes.append([value])
    return lanes


def _block(fragments: list[dict], block_ref: str) -> dict:
    spans = []
    for fragment in fragments:
        span = {key: fragment[key] for key in ('evidence_id', 'start', 'end', 'text', 'bbox')}
        if spans and spans[-1]['evidence_id'] == span['evidence_id'] and spans[-1]['end'] == span['start']:
            previous = spans[-1]
            previous['end'] = span['end']; previous['text'] += span['text']
            previous['bbox'] = [min(previous['bbox'][0], span['bbox'][0]),
                                min(previous['bbox'][1], span['bbox'][1]),
                                max(previous['bbox'][2], span['bbox'][2]),
                                max(previous['bbox'][3], span['bbox'][3])]
        else:
            spans.append(span)
    text = ''
    for span in spans:
        if text and not text.endswith(('\n', '\r')) and not span['text'].startswith(('\n', '\r')):
            text += '\n'
        text += span['text']
    return {'block_ref': block_ref, 'document_id': fragments[0]['document_id'],
            'page_number': fragments[0]['page_number'], 'text': text, 'spans': spans,
            'status': 'BOUND'}


def _body(rows: list[dict]) -> dict:
    if not isinstance(rows, (list, tuple)):
        raise SourceBlockError('selected source rows must be a list')
    source_manifest = []
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    unusable = []
    seen = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise SourceBlockError('source row must be an object')
        evidence_id = row.get('evidence_id')
        if not isinstance(evidence_id, str) or not evidence_id:
            raise SourceBlockError('source row requires a stable evidence id')
        if evidence_id in seen:
            raise SourceBlockError('duplicate evidence id')
        seen.add(evidence_id)
        text = row.get('raw_text')
        if not isinstance(text, str):
            raise SourceBlockError('source row requires raw text')
        mapping = row.get('text_map')
        source_manifest.append({
            'evidence_id': evidence_id, 'document_id': row.get('document_id'),
            'page_number': (row.get('locator') or {}).get('page_number')
            if isinstance(row.get('locator'), dict) else None,
            'raw_text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'text_map_sha256': _digest(mapping), 'extraction_method': row.get('extraction_method'),
        })
        words, reason = _strict_words(row, index)
        if words is None:
            unusable.append(_unusable(row, reason or 'UNUSABLE')); continue
        try:
            fragments = _fragments(row, words)
        except _SegmentCoverageError as exc:
            unusable.append(_unusable(row, str(exc))); continue
        grouped[(row['document_id'], words[0]['page_number'])].extend(fragments)
    blocks = []
    for key, fragments in sorted(grouped.items(), key=lambda item: min(value['input_index'] for value in item[1])):
        # Crucially, split into horizontal lanes before y ordering.  A global
        # y sweep would alternate left/right columns and destroy continuations.
        for lane in _lanes(fragments):
            current: list[dict] = []
            for fragment in sorted(lane, key=lambda item: (item['bbox'][1], item['bbox'][0], item['input_index'], item['start'])):
                if current and _same_reading_lane(current[-1], fragment):
                    current.append(fragment)
                else:
                    if current: blocks.append(_block(current, f'B{len(blocks) + 1}'))
                    current = [fragment]
            if current: blocks.append(_block(current, f'B{len(blocks) + 1}'))
    blocks.extend(unusable)
    return {'version': VERSION, 'source_manifest': source_manifest, 'blocks': blocks,
            'limitations': [
                'Blocks are extractive excerpts, not engineering objects or relationships.',
                'Blocks do not interpret leader lines, arrows, adjacency, applicability, or answer support.',
                'text_map does not authenticate text direction. This heuristic only rejects suspected long narrow tokens; short tokens and other BOUND text are not direction-verified.',
                'All candidate use remains REVIEW_REQUIRED.',
            ]}


def build_source_blocks(rows: list[dict]) -> dict:
    """Build a deterministic source-only manifest without clipping any BOUND fact."""
    body = _body(rows)
    return {**body, 'manifest_sha256': _digest(body)}


def _verify_self(manifest: dict) -> None:
    if not isinstance(manifest, dict) or manifest.get('version') != VERSION:
        raise SourceBlockError('unsupported source block manifest')
    body = {key: value for key, value in manifest.items() if key != 'manifest_sha256'}
    digest = manifest.get('manifest_sha256')
    if not isinstance(digest, str) or not hmac.compare_digest(digest, _digest(body)):
        raise SourceBlockError('source block manifest hash is inconsistent')


def authenticate_source_blocks(manifest: dict, rows: list[dict]) -> None:
    """Rebuild all geometry and parent offsets; equality is the only authentication."""
    _verify_self(manifest)
    rebuilt = build_source_blocks(rows)
    if not hmac.compare_digest(manifest['manifest_sha256'], rebuilt['manifest_sha256']) or manifest != rebuilt:
        raise SourceBlockError('source blocks do not exactly match current source rows')


def model_source_blocks(manifest: dict) -> dict:
    """Return only non-citable B aliases and complete BOUND excerpt text."""
    _verify_self(manifest)
    blocks = []
    for block in manifest['blocks']:
        if block.get('status') != 'BOUND':
            continue
        blocks.append({key: block[key] for key in ('block_ref', 'document_id', 'page_number', 'text')})
    return {'version': VERSION, 'manifest_sha256': manifest['manifest_sha256'], 'blocks': blocks,
            'limitations': list(manifest['limitations'])}
