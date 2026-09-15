"""Local OCR, safe page rendering, and auditable PDF geometry summaries."""
from __future__ import annotations

import io
import math
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image,ImageOps


OCR_VERSION = 'rapidocr-3-local-v1'
VISION_RENDER_VERSION = 'vision-render-1'
OCR_MAX_SIDE = 6000
VISION_MAX_SIDE = 2048
OCR_RESOLUTION = 150
_OCR_ENGINE = None
PDF_CROP_COORDINATE_SYSTEM = 'pdf-cropbox-points-top-left'


def local_ocr_available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        import rapidocr  # noqa: F401
        return True
    except ImportError:
        return False


def _ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr import RapidOCR
        _OCR_ENGINE=RapidOCR(params={'Global.log_level':'critical'})
    return _OCR_ENGINE


def _bounded_rgb(image: Image.Image, max_side: int) -> Image.Image:
    image = image.convert('RGB')
    if max(image.size) <= max_side:
        return image
    scale = max_side / max(image.size)
    return image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)


def pdf_cropbox_dimensions(page: Any) -> tuple[float, float]:
    """Return the visible CropBox dimensions in pdfplumber's top-left points."""
    x0, top, x1, bottom = (float(value) for value in page.cropbox)
    return x1 - x0, bottom - top


def pdf_cropbox_local_bbox(bbox: list[float] | tuple[float, float, float, float],
                           cropbox: tuple[float, float, float, float] | list[float]) -> list[float]:
    """Translate a PDF bbox into the rendered CropBox-local coordinate system."""
    x0, top, _, _ = (float(value) for value in cropbox)
    return [float(bbox[0]) - x0, float(bbox[1]) - top,
            float(bbox[2]) - x0, float(bbox[3]) - top]


def render_pdf_page(path: Path, page_number: int, *, resolution: int = OCR_RESOLUTION,
                    max_side: int = OCR_MAX_SIDE) -> tuple[Image.Image, float, float]:
    """Render only the visible CropBox and return its local point dimensions."""
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        if not 1 <= page_number <= len(pdf.pages):
            raise ValueError('PDF页码超出范围')
        page = pdf.pages[page_number - 1]
        width, height = pdf_cropbox_dimensions(page)
        # Explicitly retain pdfplumber's default CropBox rendering.  Rendering the
        # MediaBox can expose content that a source PDF intentionally crops away.
        image = page.to_image(resolution=resolution, antialias=True, force_mediabox=False).original
    return _bounded_rgb(image, max_side), width, height


def render_visual_png(path: Path, original_name: str, page_number: int = 1) -> tuple[bytes, float, float, str]:
    """Create a bounded PNG derivative for vision/API preview; the original stays immutable."""
    suffix = Path(original_name).suffix.lower()
    if suffix == '.pdf':
        # Pick a resolution near the final target so huge plan sheets do not inflate memory.
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            if not 1 <= page_number <= len(pdf.pages):
                raise ValueError('PDF页码超出范围')
            page = pdf.pages[page_number - 1]
            width, height = pdf_cropbox_dimensions(page)
            resolution = max(36, min(150, round(VISION_MAX_SIDE * 72 / max(width, height))))
            image = page.to_image(resolution=resolution, antialias=True, force_mediabox=False).original
        coordinate_system = PDF_CROP_COORDINATE_SYSTEM
    else:
        if page_number != 1:
            raise ValueError('图片只有第1页')
        with Image.open(path) as source:
            normalized=ImageOps.exif_transpose(source)
            normalized.load()
            width, height = float(normalized.width), float(normalized.height)
            image = normalized.copy()
        coordinate_system = 'image-pixels-top-left-exif-normalized'
    image = _bounded_rgb(image, VISION_MAX_SIDE)
    out = io.BytesIO()
    image.save(out, format='PNG', optimize=True)
    return out.getvalue(), width, height, coordinate_system


def _rect(box: Any) -> list[float]:
    points = list(box)
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _merge_rects(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def ocr_image(image: Image.Image, *, page_number: int, coordinate_width: float,
              coordinate_height: float, coordinate_system: str,
              max_fragment_bytes: int = 2200, max_fragment_chars: int = 1600) -> list[dict]:
    """Run local OCR and group adjacent lines into bounded evidence fragments."""
    if not local_ocr_available():
        return []
    import numpy as np
    image = _bounded_rgb(image, OCR_MAX_SIDE)
    result = _ocr_engine()(np.asarray(image))
    texts = tuple(result.txts or ())
    scores = tuple(result.scores or ())
    boxes = list(result.boxes) if result.boxes is not None else []
    if not texts or len(texts) != len(scores) or len(texts) != len(boxes):
        return []
    sx, sy = coordinate_width / image.width, coordinate_height / image.height
    rows = []
    for text, score, box in zip(texts, scores, boxes):
        value = str(text).strip()
        if not value:
            continue
        rect = _rect(box)
        rows.append((value, max(0.0, min(1.0, float(score))),
                     [rect[0] * sx, rect[1] * sy, rect[2] * sx, rect[3] * sy]))

    fragments: list[dict] = []
    batch: list[tuple[str, float, list[float]]] = []
    chars = size = 0

    def flush() -> None:
        nonlocal batch, chars, size
        if not batch:
            return
        raw = '\n'.join(row[0] for row in batch)
        mapping = []
        offset = 0
        for text, _, bbox in batch:
            mapping.append({'start': offset, 'end': offset + len(text), 'bbox': bbox})
            offset += len(text) + 1
        fragments.append({
            'text': raw,
            'locator': {
                'page_number': page_number, 'sheet': None, 'section': None, 'paragraph': None,
                'bbox': _merge_rects([row[2] for row in batch]),
                'coordinate_system': coordinate_system, 'text_line_start': None,
                'text_line_end': None, 'native_element_id': None,
            },
            'method': 'OCR', 'internal_revision_date': None, 'revision_label': None,
            'text_map': mapping,
            'confidence': round(sum(row[1] for row in batch) / len(batch), 6),
        })
        batch = []
        chars = size = 0

    for row in rows:
        row_size = len(row[0].encode('utf-8'))
        if batch and (chars + len(row[0]) + 1 > max_fragment_chars or size + row_size + 1 > max_fragment_bytes):
            flush()
        # A single OCR line above the gateway envelope is split without inventing boxes.
        if row_size > max_fragment_bytes or len(row[0]) > max_fragment_chars:
            flush()
            start = 0
            while start < len(row[0]):
                end = min(len(row[0]), start + max_fragment_chars)
                while len(row[0][start:end].encode('utf-8')) > max_fragment_bytes:
                    end -= 1
                batch = [(row[0][start:end], row[1], row[2])]
                flush()
                start = end
            continue
        batch.append(row)
        chars += len(row[0]) + 1
        size += row_size + 1
    flush()
    return fragments


def ocr_pdf_page(path: Path, page_number: int) -> list[dict]:
    image, width, height = render_pdf_page(path, page_number)
    return ocr_image(image, page_number=page_number, coordinate_width=width,
                     coordinate_height=height, coordinate_system=PDF_CROP_COORDINATE_SYSTEM)


def ocr_image_file(path: Path) -> list[dict]:
    with Image.open(path) as source:
        normalized=ImageOps.exif_transpose(source)
        normalized.load()
        width, height = float(normalized.width), float(normalized.height)
        image = normalized.copy()
    return ocr_image(image, page_number=1, coordinate_width=width, coordinate_height=height,
                     coordinate_system='image-pixels-top-left-exif-normalized')


def _number(value: str) -> float:
    value = re.sub(r'\s+', '', value)
    return float(Fraction(value)) if '/' in value else float(value)


def extract_scale(text: str) -> tuple[dict | None, str | None]:
    """Return one unambiguous explicit drawing scale; never infer from sheet size."""
    if re.search(r'\b(?:NTS|NOT\s+TO\s+SCALE)\b', text, re.I):
        return None, '图面明确标记为不按比例。'
    found = []
    imperial = re.compile(
        r'(?:SCALE\s*[:=]?\s*)?(\d+(?:\.\d+)?|\d+\s*/\s*\d+)\s*(?:"|IN(?:CH(?:ES)?)?)\s*=\s*'
        r'(\d+(?:\.\d+)?|\d+\s*/\s*\d+)\s*(?:\'|FT|FEET)(?:\s*[- ]\s*(\d+(?:\.\d+)?)\s*(?:"|IN))?', re.I)
    for match in imperial.finditer(text):
        paper = _number(match.group(1))
        design = _number(match.group(2)) * 12 + (float(match.group(3)) if match.group(3) else 0)
        if paper > 0 and design > 0:
            found.append({'ratio': design / paper, 'paper_unit': 'IN', 'design_unit': 'IN',
                          'source_text': match.group(0).strip()})
    metric = re.compile(r'\bSCALE\s*[:=]?\s*1\s*:\s*(\d+(?:\.\d+)?)\b', re.I)
    for match in metric.finditer(text):
        ratio = float(match.group(1))
        if ratio > 0:
            found.append({'ratio': ratio, 'paper_unit': 'MM', 'design_unit': 'MM',
                          'source_text': match.group(0).strip()})
    unique = {(round(x['ratio'], 9), x['paper_unit'], x['design_unit']) for x in found}
    if len(unique) == 1:
        return found[0], None
    if len(unique) > 1:
        return None, '同一页检测到多个比例；未自动选择视图范围。'
    return None, '没有检测到唯一明确的图面比例。'


def pdf_geometry_summary(page: Any, page_number: int, text: str) -> dict:
    """Measure PDF vector primitives in paper coordinates; do not call them material quantities."""
    lines = list(page.lines or [])
    curves = list(page.curves or [])
    rects = list(page.rects or [])
    length = 0.0
    for line in lines:
        length += math.hypot(float(line['x1']) - float(line['x0']), float(line['bottom']) - float(line['top']))
    for curve in curves:
        points = curve.get('pts') or []
        for a, b in zip(points, points[1:]):
            length += math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
    rect_area = sum(abs(float(r['x1']) - float(r['x0'])) * abs(float(r['bottom']) - float(r['top'])) for r in rects)
    scale, warning = extract_scale(text)
    result = {
        'page': page_number, 'view_id': f'pdf-page-{page_number}',
        'coordinate_system': PDF_CROP_COORDINATE_SYSTEM,
        'primitive_counts': {'lines': len(lines), 'curves': len(curves), 'rectangles': len(rects)},
        'paper_length_points': round(length, 6), 'paper_rectangle_area_points2': round(rect_area, 6),
        'calibration': scale, 'status': 'CALIBRATED_RAW_GEOMETRY' if scale else 'CALIBRATION_REQUIRED',
        'warning': warning,
        'material_quantity': None,
        'scope_note': '这是整页矢量图元审计值，可能包含图框、标注和重复视图；未映射到材料，不作为设计净量。',
    }
    if scale:
        paper_inches = length / 72
        design_inches = paper_inches * scale['ratio'] if scale['paper_unit'] == 'IN' else None
        if design_inches is not None:
            result['calibrated_total_length'] = {'value': round(design_inches / 12, 6), 'unit': 'FT'}
        else:
            design_mm = paper_inches * 25.4 * scale['ratio']
            result['calibrated_total_length'] = {'value': round(design_mm / 1000, 6), 'unit': 'M'}
    return result
