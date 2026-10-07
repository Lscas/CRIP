"""Frozen, source-only PDF character projection for an opt-in future path.

This module intentionally has no database, selector, prompt, claim, or answer
integration.  It projects native PDF characters into mechanically-derived
directional line/lane runs.  A row is never an engineering object, condition,
or answer authority.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import math
from collections import defaultdict
from pathlib import Path
from types import MappingProxyType

import pdfplumber
import pdfminer


PROJECTION_VERSION = 'pdf-char-lane-projection-1'
_CONFIG = MappingProxyType({
    # Scale rounding and cardinal axes have intentionally separate tolerances:
    # rounding noise is acceptable, arbitrary-angle text is not.
    'scale_ratio_tolerance': 0.01,
    'cardinal_axis_tolerance': 0.000001,
    'line_tolerance_points': 3.0,
    'lane_left_tolerance_points': 80.0,
    # A gap larger than ordinary word spacing is a distinct printed component;
    # later lane formation must never join such siblings back into one row.
    'line_component_gap_points': 36.0,
    'lane_run_gap_points': 36.0,
    'synthetic_space_gap_points': 3.0,
    'max_claim_characters': 1000,
    'max_quote_characters': 1800,
    'accepted_cardinal_rotations': (0, 90, 180, 270),
})


class PdfProjectionError(ValueError):
    pass


def _stable(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _config_snapshot() -> dict:
    """Return JSON-owned configuration, never the immutable runtime mapping."""
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in _CONFIG.items()
    }


def _source_bytes(pdf_path: str | Path | bytes) -> bytes:
    if isinstance(pdf_path, bytes):
        return pdf_path
    if isinstance(pdf_path, (str, Path)):
        return Path(pdf_path).read_bytes()
    raise PdfProjectionError('PDF input must be a path or bytes')


def _expected_sha256(expected_sha256: str, actual: str) -> None:
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise PdfProjectionError('expected PDF SHA-256 is malformed')
    try:
        int(expected_sha256, 16)
    except ValueError as exc:
        raise PdfProjectionError('expected PDF SHA-256 is malformed') from exc
    if not hmac.compare_digest(expected_sha256.lower(), actual):
        raise PdfProjectionError('PDF bytes do not match expected SHA-256')


def _finite_number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _direction(matrix: object) -> dict | None:
    """Classify only non-mirrored cardinal CTMs; everything else is unsafe."""
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 6:
        return None
    values = [_finite_number(item) for item in matrix]
    if any(item is None for item in values):
        return None
    a, b, c, d, _e, _f = values  # type: ignore[misc]
    x_scale = math.hypot(a, b); y_scale = math.hypot(c, d)
    if x_scale <= 0 or y_scale <= 0:
        return None
    determinant = a * d - b * c
    # Mirror, shear, non-uniform scale, and arbitrary rotation have no safe
    # reading direction in this narrow projection.
    if determinant <= 0 or abs((x_scale / y_scale) - 1.0) > _CONFIG['scale_ratio_tolerance']:
        return None
    normalized = (a / x_scale, b / x_scale, c / y_scale, d / y_scale)
    candidates = {
        0: (1.0, 0.0, 0.0, 1.0),
        90: (0.0, 1.0, -1.0, 0.0),
        180: (-1.0, 0.0, 0.0, -1.0),
        270: (0.0, -1.0, 1.0, 0.0),
    }
    for degrees, target in candidates.items():
        if max(abs(actual - wanted) for actual, wanted in zip(normalized, target)) <= _CONFIG['cardinal_axis_tolerance']:
            return {
                'degrees': degrees,
                'matrix': [a, b, c, d, values[4], values[5]],
                'reading_axis': 'X' if degrees in (0, 180) else 'Y',
                'line_axis': 'Y' if degrees in (0, 180) else 'X',
            }
    return None


def _bbox(char: dict) -> list[float] | None:
    values = [_finite_number(char.get(key)) for key in ('x0', 'top', 'x1', 'bottom')]
    if any(value is None for value in values):
        return None
    x0, top, x1, bottom = values  # type: ignore[misc]
    if x1 < x0 or bottom < top:
        return None
    return [x0, top, x1, bottom]


def _native_char(char: dict, ordinal: int, page_number: int) -> dict:
    text = char.get('text')
    direction = _direction(char.get('matrix'))
    bbox = _bbox(char)
    matrix = char.get('matrix')
    matrix = ([float(value) for value in matrix]
              if isinstance(matrix, (list, tuple)) and len(matrix) == 6
              and all(_finite_number(value) is not None for value in matrix) else None)
    value = {
        'char_id': f'P{page_number}C{ordinal}', 'ordinal': ordinal,
        # Preserve a JSON string field even for malformed native metadata;
        # ``text_valid`` plus UNUSABLE status makes the absence explicit and
        # prevents consumers from treating the empty representation as text.
        'text': text if isinstance(text, str) else '',
        'text_valid': isinstance(text, str),
        'bbox': bbox, 'matrix': matrix,
        'upright': char.get('upright') if isinstance(char.get('upright'), bool) else None,
        **({'mcid': char['mcid']} if char.get('mcid') is not None else {}),
        **({'tag': char['tag']} if char.get('tag') is not None else {}),
    }
    if not isinstance(text, str) or direction is None or bbox is None:
        return {**value, 'status': 'UNUSABLE',
                'reason': 'GEOMETRY_REQUIRES_DIRECTION_METADATA'}
    return {**value, 'direction': direction, 'status': 'BOUND'}


def _line_coordinate(item: dict) -> float:
    box = item['bbox']; degrees = item['direction']['degrees']
    return (box[1] + box[3]) / 2 if degrees in (0, 180) else (box[0] + box[2]) / 2


def _reading_coordinate(item: dict) -> float:
    box = item['bbox']; degrees = item['direction']['degrees']
    if degrees == 0: return box[0]
    if degrees == 180: return -box[2]
    # pdfplumber's ``top`` axis is inverted from PDF user-space y.  A +90
    # CTM therefore reads toward decreasing top; -90 reads toward increasing.
    if degrees == 90: return -box[3]
    return box[1]


def _lane_coordinate(line: dict) -> float:
    box = line['bbox']; degrees = line['direction']['degrees']
    return box[0] if degrees in (0, 180) else box[1]


def _sort_line_chars(chars: list[dict]) -> list[dict]:
    return sorted(chars, key=_reading_coordinate)


def _char_gap(before: dict, current: dict, degrees: int) -> float:
    previous_box = before['bbox']; current_box = current['bbox']
    if degrees == 0: return current_box[0] - previous_box[2]
    if degrees == 180: return previous_box[0] - current_box[2]
    if degrees == 90: return previous_box[1] - current_box[3]
    return current_box[1] - previous_box[3]


def _line_from_chars(chars: list[dict], direction: dict) -> dict:
    chars = _sort_line_chars(chars)
    pieces = []; text = ''; prior = None
    for char in chars:
        if prior is not None:
            degrees = direction['degrees']
            gap = _char_gap(prior, char, degrees)
            if (gap > _CONFIG['synthetic_space_gap_points']
                    and not prior['text'].isspace() and not char['text'].isspace()):
                pieces.append({'kind': 'synthetic_space', 'text': ' '}); text += ' '
        pieces.append({'kind': 'native_chars', 'char_ids': [char['char_id']], 'text': char['text']})
        text += char['text']; prior = char
    boxes = [char['bbox'] for char in chars]
    return {
        'text': text, 'char_ids': [char['char_id'] for char in chars], 'pieces': pieces,
        'bbox': [min(box[0] for box in boxes), min(box[1] for box in boxes),
                 max(box[2] for box in boxes), max(box[3] for box in boxes)],
        'direction': {'degrees': direction['degrees'], 'reading_axis': direction['reading_axis'],
                      'line_axis': direction['line_axis']},
    }


def _line_components(chars: list[dict], direction: dict) -> tuple[list[list[dict]], set[str]]:
    """Split only at obvious blanks; reject each complete overlapping component.

    A rejected component is never reassembled from the surviving tail characters.
    """
    ordered = _sort_line_chars(chars)
    raw_components: list[list[dict]] = []; current: list[dict] = []
    for char in ordered:
        if current and _char_gap(current[-1], char, direction['degrees']) > _CONFIG['line_component_gap_points']:
            raw_components.append(current); current = []
        current.append(char)
    if current:
        raw_components.append(current)

    components: list[list[dict]] = []; rejected: set[str] = set()
    for component in raw_components:
        gaps = [_char_gap(before, after, direction['degrees'])
                for before, after in zip(component, component[1:])]
        if any(gap < -0.5 for gap in gaps):
            rejected.update(item['char_id'] for item in component)
        else:
            components.append(component)
    return components, rejected


def _lines(chars: list[dict]) -> tuple[list[dict], set[str]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for char in chars: grouped[char['direction']['degrees']].append(char)
    output = []; rejected: set[str] = set()
    for degrees, values in sorted(grouped.items()):
        clusters: list[list[dict]] = []
        for char in sorted(values, key=lambda item: (_line_coordinate(item), _reading_coordinate(item), item['ordinal'])):
            coordinate = _line_coordinate(char)
            if clusters:
                anchor = sum(_line_coordinate(item) for item in clusters[-1]) / len(clusters[-1])
                if abs(coordinate - anchor) <= _CONFIG['line_tolerance_points']:
                    clusters[-1].append(char); continue
            clusters.append([char])
        for cluster in clusters:
            components, ambiguous = _line_components(cluster, cluster[0]['direction'])
            rejected.update(ambiguous)
            for component in components:
                if not any(item['char_id'] in rejected for item in component):
                    output.append(_line_from_chars(component, component[0]['direction']))
    return output, rejected


def _lanes(lines: list[dict]) -> list[list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for line in lines: grouped[line['direction']['degrees']].append(line)
    output = []
    for degrees, values in sorted(grouped.items()):
        lanes: list[list[dict]] = []
        for line in sorted(values, key=lambda item: (_lane_coordinate(item), _line_coordinate(item), item['text'])):
            coordinate = _lane_coordinate(line)
            if lanes and coordinate - _lane_coordinate(lanes[-1][0]) <= _CONFIG['lane_left_tolerance_points']:
                lanes[-1].append(line)
            else:
                lanes.append([line])
        output.extend(lanes)
    return output


def _run_gap(before: dict, current: dict) -> float:
    degrees = before['direction']['degrees']; a = before['bbox']; b = current['bbox']
    if degrees == 0: return b[1] - a[3]
    if degrees == 180: return a[1] - b[3]
    if degrees == 90: return b[0] - a[2]
    return a[0] - b[2]


def _reading_intervals_overlap(before: dict, current: dict) -> bool:
    """Require a shared reading-axis interval before treating lines as one run."""
    degrees = before['direction']['degrees']
    a = before['bbox']; b = current['bbox']
    if degrees in (0, 180):
        a_start, a_end, b_start, b_end = a[0], a[2], b[0], b[2]
    else:
        a_start, a_end, b_start, b_end = a[1], a[3], b[1], b[3]
    return max(a_start, b_start) <= min(a_end, b_end)


def _unsupported_native_touches_line(native: dict, line: dict) -> bool:
    """Whether a direction-rejected native char contaminates this component.

    The rejected char has finite native geometry but no trusted reading
    direction.  We can still safely reject (never include) a component when
    its perpendicular band intersects and its reading interval overlaps or is
    separated only by the configured obvious-component gap.
    """
    bad = native['bbox']; box = line['bbox']; degrees = line['direction']['degrees']
    if degrees in (0, 180):
        band_start, band_end = box[1], box[3]
        bad_band_start, bad_band_end = bad[1], bad[3]
        read_start, read_end = box[0], box[2]
        bad_read_start, bad_read_end = bad[0], bad[2]
    else:
        band_start, band_end = box[0], box[2]
        bad_band_start, bad_band_end = bad[0], bad[2]
        read_start, read_end = box[1], box[3]
        bad_read_start, bad_read_end = bad[1], bad[3]
    same_band = max(band_start, bad_band_start) <= min(band_end, bad_band_end)
    if not same_band:
        return False
    if max(read_start, bad_read_start) <= min(read_end, bad_read_end):
        return True
    separation = min(abs(bad_read_start - read_end), abs(read_start - bad_read_end))
    return separation <= _CONFIG['line_component_gap_points']


def _row(lines: list[dict], row_ref: str, *, ambiguous_lane: bool = False) -> dict:
    text = ''; pieces = []; char_ids = []
    for index, line in enumerate(lines):
        if index:
            text += '\n'; pieces.append({'kind': 'synthetic_newline', 'text': '\n'})
        text += line['text']; pieces.extend(line['pieces']); char_ids.extend(line['char_ids'])
    boxes = [line['bbox'] for line in lines]
    status = 'BOUND'; reason = None
    if ambiguous_lane:
        status = 'UNUSABLE'; reason = 'AMBIGUOUS_LANE_COMPONENTS'
    elif len(text) > _CONFIG['max_claim_characters']:
        status = 'UNUSABLE'; reason = 'CLAIM_TEXT_LIMIT'
    elif len(text) > _CONFIG['max_quote_characters']:
        status = 'UNUSABLE'; reason = 'QUOTE_TEXT_LIMIT'
    value = {
        'row_ref': row_ref, 'text': text,
        'text_sha256': _sha256(text.encode('utf-8')), 'char_ids': char_ids,
        'provenance': {'lines': lines, 'pieces': pieces},
        'direction': lines[0]['direction'],
        'direction_degrees': lines[0]['direction']['degrees'],
        'bbox': [min(box[0] for box in boxes), min(box[1] for box in boxes),
                 max(box[2] for box in boxes), max(box[3] for box in boxes)],
        'status': status,
    }
    if reason: value['reason'] = reason
    return value


def _rows(lines: list[dict]) -> list[dict]:
    rows = []
    for lane in _lanes(lines):
        direction = lane[0]['direction']['degrees']
        ordered = sorted(lane, key=lambda item: (_line_coordinate(item), _lane_coordinate(item), item['text']),
                         reverse=direction in (180, 270))
        current: list[dict] = []
        ambiguous_lane = False
        for line in ordered:
            if (current and _run_gap(current[-1], line) <= _CONFIG['lane_run_gap_points']
                    and _reading_intervals_overlap(current[-1], line)):
                if abs(_line_coordinate(current[-1]) - _line_coordinate(line)) <= _CONFIG['line_tolerance_points']:
                    # Distinct components in one reading-line band cannot be
                    # assigned to a vertical lane without guessing.
                    ambiguous_lane = True
                current.append(line)
            else:
                if current:
                    rows.append(_row(current, f'R{len(rows) + 1}', ambiguous_lane=ambiguous_lane))
                current = [line]
                ambiguous_lane = False
        if current:
            rows.append(_row(current, f'R{len(rows) + 1}', ambiguous_lane=ambiguous_lane))
    return rows


def _page_projection(page: object, page_number: int) -> dict:
    chars = page.chars  # pdfplumber Page, intentionally duck-typed for no public coupling
    rotation = getattr(page, 'rotation', 0)
    if type(rotation) is not int or rotation not in _CONFIG['accepted_cardinal_rotations']:
        raise PdfProjectionError('PDF page rotation is not a supported cardinal direction')
    # Call pdfplumber's own crop API when available, while retaining the full
    # native-page character inventory for explicit outside-crop accounting.
    cropbox = getattr(page, 'cropbox', None)
    if not isinstance(cropbox, (list, tuple)) or len(cropbox) != 4:
        cropbox = [0.0, 0.0, float(page.width), float(page.height)]
    cropbox = [float(value) for value in cropbox]
    # The CropBox is always a visibility boundary; for a document without an
    # explicit CropBox pdfplumber exposes the MediaBox here.
    if hasattr(page, 'crop'):
        page.crop(tuple(cropbox))
    native_chars = []; usable = []; unusable = []
    for ordinal, char in enumerate(chars, 1):
        native = _native_char(char, ordinal, page_number); native_chars.append(native)
        bbox = native.get('bbox')
        inside = (isinstance(bbox, list)
                  and bbox[0] >= cropbox[0] and bbox[1] >= cropbox[1]
                  and bbox[2] <= cropbox[2] and bbox[3] <= cropbox[3])
        if native['status'] != 'BOUND':
            unusable.append(native)
        elif not inside:
            unusable.append({**native, 'status': 'UNUSABLE', 'reason': 'OUTSIDE_CROPBOX'})
        else:
            usable.append(native)
    # Form components from the complete native page before excluding crop
    # exterior characters.  Otherwise an exposed prefix could impersonate a
    # complete source line after its condition tail was cropped away.
    candidates = [item for item in native_chars if item['status'] == 'BOUND']
    handled_ids = set()
    # A missing/non-finite matrix, missing bbox, or missing text cannot be
    # assigned to a reliable direction band.  Keeping neighbouring glyphs
    # would silently close that hole (e.g. ``AB`` + missing ``C`` + ``DE``),
    # so the page's otherwise projectable chars fail closed.  By contrast a
    # finite mirror/shear matrix remains geometrically localized and is
    # already rejected only as its own native character above.
    unlocatable_native = any(
        item['status'] != 'BOUND'
        and (item.get('matrix') is None or item.get('bbox') is None
             or not item.get('text_valid', True))
        for item in native_chars
    )
    accepted_lines = []
    if unlocatable_native:
        for char in candidates:
            unusable.append({**char, 'status': 'UNUSABLE',
                             'reason': 'UNLOCATABLE_NATIVE_CHARACTER'})
            handled_ids.add(char['char_id'])
    else:
        lines, ambiguous_ids = _lines(candidates) if candidates else ([], set())
        for char in candidates:
            if char['char_id'] in ambiguous_ids:
                unusable.append({**char, 'status': 'UNUSABLE',
                                 'reason': 'AMBIGUOUS_OVERLAPPING_NATIVE_CHARACTERS'})
                handled_ids.add(char['char_id'])

        unsupported_geometry = [
            item for item in native_chars
            if item['status'] != 'BOUND' and item.get('bbox') is not None
            and item.get('matrix') is not None and item.get('text_valid', False)
        ]
        for line in lines:
            line_ids = set(line['char_ids'])
            if any(_unsupported_native_touches_line(item, line)
                   for item in unsupported_geometry):
                for char_id in line_ids:
                    source = next(item for item in candidates if item['char_id'] == char_id)
                    unusable.append({**source, 'status': 'UNUSABLE',
                                     'reason': 'UNSUPPORTED_CHARACTER_IN_COMPONENT'})
                    handled_ids.add(char_id)
            else:
                outside = line_ids - {item['char_id'] for item in usable}
                if outside:
                    for char_id in line_ids:
                        source = next(item for item in candidates if item['char_id'] == char_id)
                        unusable.append({**source, 'status': 'UNUSABLE',
                                         'reason': ('OUTSIDE_CROPBOX' if char_id in outside
                                                    else 'CROPBOX_PARTIAL_COMPONENT')})
                        handled_ids.add(char_id)
                else:
                    accepted_lines.append(line)
                    handled_ids.update(line_ids)
    rows = _rows(accepted_lines) if accepted_lines else []
    assigned = {char_id for row in rows for char_id in row['char_ids']}
    for char in candidates:
        if char['char_id'] not in assigned and char['char_id'] not in handled_ids:
            unusable.append({**char, 'status': 'UNUSABLE',
                             'reason': 'UNASSIGNED_NATIVE_CHARACTER'})
    # A native ordinal has exactly one terminal accounting destination.  Crop
    # preclassification and whole-component rejection can encounter the same
    # ordinal through different fail-closed paths; retain the first, most
    # direct reason rather than manufacture duplicate identities.
    unusable_by_id: dict[str, dict] = {}
    for item in unusable:
        unusable_by_id.setdefault(item['char_id'], item)
    unusable = [unusable_by_id[f'P{page_number}C{ordinal}']
                for ordinal in range(1, len(chars) + 1)
                if f'P{page_number}C{ordinal}' in unusable_by_id]
    return {
        'page_number': page_number,
        'cropbox': cropbox,
        'rotation': rotation,
        'native_char_count': len(chars), 'native_chars': native_chars,
        'rows': rows, 'unusable': unusable,
        'counts': {'bound_rows': sum(row['status'] == 'BOUND' for row in rows),
                   'unusable_rows': sum(row['status'] == 'UNUSABLE' for row in rows),
                   'unusable_characters': len(unusable)},
    }


def _body(pdf_bytes: bytes, pdf_sha256: str, page_numbers: list[int]) -> dict:
    if not isinstance(page_numbers, list) or not page_numbers or any(type(page) is not int or page < 1 for page in page_numbers):
        raise PdfProjectionError('page_numbers must be a non-empty list of positive integers')
    if len(set(page_numbers)) != len(page_numbers):
        raise PdfProjectionError('page_numbers must be unique')
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if max(page_numbers) > len(pdf.pages):
            raise PdfProjectionError('requested page is outside the PDF')
        pages = [_page_projection(pdf.pages[number - 1], number) for number in page_numbers]
    next_row = 1
    for page in pages:
        for row in page['rows']:
            row['row_ref'] = f'R{next_row}'
            next_row += 1
    return {
        'projection_version': PROJECTION_VERSION, 'pdf_sha256': pdf_sha256,
        'page_numbers': page_numbers,
        'algorithm': {'config': _config_snapshot(), 'pdfplumber_version': getattr(pdfplumber, '__version__', None),
                      'pdfminer_version': getattr(pdfminer, '__version__', None)},
        'pages': pages,
        'limitations': [
            'Rows are mechanical native-character projections, not engineering objects or conditions.',
            'CTM direction is mechanically authenticated; semantics, object identity, condition applicability, and answer completeness are always false.',
            'Synthetic spaces and newlines are explicit provenance pieces and never masquerade as native PDF characters.',
        ],
        'semantic_verified': False, 'object_verified': False,
        'condition_verified': False, 'answer_complete': False,
    }


def build_pdf_projection(pdf_path: str | Path | bytes, *, expected_sha256: str,
                         page_numbers: list[int]) -> dict:
    """Build an unconnected, fully deterministic native-char sidecar manifest."""
    pdf_bytes = _source_bytes(pdf_path); digest = _sha256(pdf_bytes)
    _expected_sha256(expected_sha256, digest)
    body = _body(pdf_bytes, digest, page_numbers)
    return {**body, 'manifest_sha256': _sha256(_stable(body).encode('utf-8'))}


def authenticate_pdf_projection(manifest: dict, pdf_path: str | Path | bytes, *,
                                expected_sha256: str, page_numbers: list[int]) -> None:
    """Re-extract all requested native chars and require exact manifest equality."""
    if not isinstance(manifest, dict) or manifest.get('projection_version') != PROJECTION_VERSION:
        raise PdfProjectionError('unsupported PDF projection manifest')
    supplied = manifest.get('manifest_sha256')
    body = {key: value for key, value in manifest.items() if key != 'manifest_sha256'}
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, _sha256(_stable(body).encode('utf-8'))):
        raise PdfProjectionError('PDF projection manifest hash is inconsistent')
    rebuilt = build_pdf_projection(pdf_path, expected_sha256=expected_sha256, page_numbers=page_numbers)
    if manifest != rebuilt:
        raise PdfProjectionError('PDF projection does not exactly match source bytes and requested pages')
