"""DXF object metadata and geometry takeoff; bounded local DWG conversion."""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path


CAD_VERSION = 'ezdxf-objects-1'
UNIT_NAMES = {1: 'IN', 2: 'FT', 4: 'MM', 5: 'CM', 6: 'M', 21: 'US_FT'}
ROOT = Path(__file__).resolve().parents[1]


def cad_available() -> bool:
    try:
        import ezdxf  # noqa: F401
        return True
    except ImportError:
        return False


def oda_converter_path() -> Path | None:
    candidates = []
    configured = os.getenv('CIRP_ODA_FILE_CONVERTER', '').strip()
    if configured:
        candidates.append(Path(configured))
    found = shutil.which('ODAFileConverter') or shutil.which('ODAFileConverter.exe')
    if found:
        candidates.append(Path(found))
    candidates.extend([
        Path(r'C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe'),
        Path(r'C:\Program Files\ODA File Converter\ODAFileConverter.exe'),
    ])
    for path in candidates:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and resolved.name.casefold() == 'odafileconverter.exe':
            return resolved
    return None


def libredwg_converter_path() -> Path | None:
    candidates=[]
    configured=os.getenv('CIRP_LIBREDWG_DWG2DXF','').strip()
    if configured:candidates.append(Path(configured))
    found=shutil.which('dwg2dxf') or shutil.which('dwg2dxf.exe')
    if found:candidates.append(Path(found))
    # The installer keeps this third-party runtime out of the source bundle and
    # discovers it without modifying the machine PATH.
    candidates.extend(sorted((ROOT/'.local'/'tools').glob('libredwg-*-win64/dwg2dxf.exe'),reverse=True))
    for path in candidates:
        try:resolved=path.resolve(strict=True)
        except OSError:continue
        if resolved.is_file() and resolved.name.casefold() in ('dwg2dxf','dwg2dxf.exe'):
            return resolved
    return None


def dwg_converter() -> tuple[str,Path] | None:
    libre=libredwg_converter_path()
    if libre is not None:return ('GNU LibreDWG',libre)
    oda=oda_converter_path()
    if oda is not None:return ('ODA File Converter',oda)
    return None


def _convert_dwg(path: Path) -> tuple[Path, tempfile.TemporaryDirectory, str]:
    selected=dwg_converter()
    if selected is None:
        raise FileNotFoundError('GNU LibreDWG/ODA转换器未安装或未配置')
    label,converter=selected
    holder = tempfile.TemporaryDirectory(prefix='cirp-dwg-')
    root = Path(holder.name)
    source_dir, output_dir = root / 'input', root / 'output'
    source_dir.mkdir(); output_dir.mkdir()
    source = source_dir / 'source.dwg'
    shutil.copyfile(path, source)
    if label=='GNU LibreDWG':
        converted=output_dir/'source.dxf'
        command=[str(converter),'-v0','-y','-o',str(converted),str(source)]
    else:
        converted=output_dir/'source.dxf'
        command=[str(converter),str(source_dir),str(output_dir),'ACAD2018','DXF','0','1','*.dwg']
    # Converter output is neither trusted nor needed for the evidence chain.
    # Discard it so a damaged drawing or noisy converter cannot fill parser
    # memory during the bounded subprocess window.
    completed=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             timeout=180,check=False)
    if not converted.exists():converted=next(output_dir.glob('*.dxf'),None)
    if completed.returncode != 0 or converted is None:
        holder.cleanup()
        raise RuntimeError(label+'转换失败')
    return converted,holder,label


def _entity_length(entity) -> float:
    kind = entity.dxftype()
    if kind == 'LINE':
        return float(entity.dxf.start.distance(entity.dxf.end))
    if kind == 'ARC':
        sweep = (float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360
        return float(entity.dxf.radius) * math.radians(sweep)
    if kind == 'CIRCLE':
        return 2 * math.pi * float(entity.dxf.radius)
    if kind in ('LWPOLYLINE', 'POLYLINE'):
        return sum(_entity_length(part) for part in entity.virtual_entities())
    if kind in ('ELLIPSE', 'SPLINE'):
        try:
            from ezdxf.path import make_path
            points = list(make_path(entity).flattening(distance=0.01, segments=8))
            return sum(float(a.distance(b)) for a, b in zip(points, points[1:]))
        except Exception:
            return 0.0
    return 0.0


def _entity_area(entity) -> float:
    kind = entity.dxftype()
    if kind == 'CIRCLE':
        return math.pi * float(entity.dxf.radius) ** 2
    if kind == 'LWPOLYLINE' and entity.closed:
        points = list(entity.get_points('xyb'))
        if points and all(abs(float(p[2])) < 1e-12 for p in points):
            xy = [(float(p[0]), float(p[1])) for p in points]
            return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))) / 2
    return 0.0


def parse_cad(path: Path, original_name: str) -> dict:
    if not cad_available():
        return {'status': 'PARTIAL', 'fragments': [], 'pages': [], 'warnings': ['本地DXF解析组件不可用。'],
                'parser_version': CAD_VERSION, 'cad_level': 'UNAVAILABLE', 'takeoffs': []}
    suffix = Path(original_name).suffix.lower()
    holder = None
    source = path
    warnings = []
    if suffix == '.dwg':
        try:
            source,holder,converter_label=_convert_dwg(path)
            warnings.append(f'DWG已通过本机{converter_label}派生为DXF；原文件保持不变。')
        except Exception as exc:
            return {'status': 'PARTIAL', 'fragments': [], 'pages': [],
                    'warnings': [f'DWG对象解析不可用：{type(exc).__name__}；需要安装并配置GNU LibreDWG dwg2dxf或ODA File Converter。'],
                    'parser_version': CAD_VERSION, 'cad_level': 'UNAVAILABLE', 'takeoffs': []}
    try:
        import ezdxf
        document = ezdxf.readfile(source)
        unit_code = int(document.header.get('$INSUNITS', 0) or 0)
        unit = UNIT_NAMES.get(unit_code)
        if unit is None:
            warnings.append('CAD模型空间单位缺失或当前未映射；长度和面积只保留原始坐标值，不输出设计量。')
        # Keep the formula inputs narrower than the layer inventory.  A layer
        # may contain text, symbols, and inserts in addition to measurable
        # geometry; those objects are evidence of the layer but must not be
        # represented as inputs to a length/area total they did not affect.
        layers = defaultdict(lambda: {
            'types': Counter(), 'handles': [], 'length_handles': [],
            'area_handles': [], 'length': 0.0, 'area': 0.0,
        })
        blocks = defaultdict(list)
        texts = defaultdict(list)
        for entity in document.modelspace():
            layer = str(entity.dxf.get('layer', '0'))
            item = layers[layer]
            item['types'][entity.dxftype()] += 1
            handle = str(entity.dxf.get('handle', '') or '')
            if handle:
                item['handles'].append(handle)
            length = _entity_length(entity)
            area = _entity_area(entity)
            item['length'] += length
            item['area'] += area
            if handle and length > 0:
                item['length_handles'].append(handle)
            if handle and area > 0:
                item['area_handles'].append(handle)
            if entity.dxftype() == 'INSERT':
                name=str(entity.dxf.name)
                block_key=(name,layer)
                blocks[block_key].append(handle or f'insert-{len(blocks[block_key]) + 1}')
            elif entity.dxftype() in ('TEXT', 'MTEXT'):
                try:
                    value = entity.plain_text() if entity.dxftype() == 'MTEXT' else str(entity.dxf.text)
                    if value.strip():
                        texts[layer].append(value.strip())
                except Exception:
                    pass
        fragments = []
        layer_fragment = {}
        for layer in sorted(layers):
            item = layers[layer]
            parts = [f'CAD layer: {layer}', 'Entity counts: ' + ', '.join(f'{k}={v}' for k, v in sorted(item['types'].items()))]
            layer_blocks={name:len(handles) for (name,block_layer),handles in blocks.items() if block_layer==layer}
            if layer_blocks:
                parts.append('Block inserts: '+', '.join(f'{name}={count}' for name,count in sorted(layer_blocks.items())))
            if unit:
                parts.extend([f'Geometric length: {item["length"]:.6f} {unit}',
                              f'Closed primitive area: {item["area"]:.6f} {unit}^2'])
            if texts[layer]:
                parts.append('Visible CAD text: ' + ' | '.join(texts[layer])[:1200])
            layer_fragment[layer] = len(fragments)
            fragments.append({
                'text': '\n'.join(parts),
                'locator': {'page_number': None, 'sheet': 'Model', 'section': None, 'paragraph': None,
                            'bbox': None, 'coordinate_system': f'cad-modelspace-{unit or "unitless"}',
                            'text_line_start': None, 'text_line_end': None,
                            'native_element_id': 'cad-layer:' + layer},
                'method': 'CAD_OBJECT', 'internal_revision_date': None, 'revision_label': None,
                'text_map': [], 'confidence': 1.0,
            })
        takeoffs = []
        for (name,source_layer),handles in sorted(blocks.items()):
            # Counts are direct CAD object counts and do not need a drawing scale.
            takeoffs.append({'kind': 'BLOCK_COUNT', 'label': name, 'value': len(handles), 'unit': 'EA',
                             'method': 'CAD_OBJECT_COUNT', 'entity_ids': handles,
                             'layer':source_layer,
                             'scope_key': f'modelspace:layer:{source_layer}:block:{name}',
                             'source_fragment_index': layer_fragment[source_layer],
                             'review_status': 'PENDING', 'basis': 'DESIGN_MODEL_OBJECTS',
                             'scope_note': '模型空间块实例计数；可能包含图例或参考对象，人工确认范围后才能作为设计净量。'})
        for layer, item in sorted(layers.items()):
            if not unit:
                continue
            if item['length'] > 0:
                takeoffs.append({'kind': 'LAYER_LENGTH', 'label': layer, 'value': round(item['length'], 6),
                                 'unit': unit, 'method': 'CAD_MEASUREMENT', 'entity_ids': item['length_handles'],
                                 'scope_key': 'modelspace:layer:' + layer, 'source_fragment_index': layer_fragment[layer],
                                 'review_status': 'PENDING', 'basis': 'DESIGN_MODEL_OBJECTS',
                                 'scope_note': '模型空间图层几何合计；未自动排除图框、图例、辅助线或重复表达。',
                                 'formula': 'SUM_ENTITY_LENGTHS',
                                 'calibration': {'view_id': 'modelspace', 'ratio': 1, 'paper_unit': unit,
                                                 'design_unit': unit, 'verified': True}})
            if item['area'] > 0:
                takeoffs.append({'kind': 'LAYER_AREA', 'label': layer, 'value': round(item['area'], 6),
                                 'unit': unit + '^2', 'method': 'CAD_MEASUREMENT', 'entity_ids': item['area_handles'],
                                 'scope_key': 'modelspace:layer:' + layer, 'source_fragment_index': layer_fragment[layer],
                                 'review_status': 'PENDING', 'basis': 'DESIGN_MODEL_OBJECTS',
                                 'scope_note': '模型空间图层封闭图元面积合计；未自动映射材料或去除重复表达。',
                                 'formula': 'SUM_CLOSED_ENTITY_AREAS',
                                 'calibration': {'view_id': 'modelspace', 'ratio': 1, 'paper_unit': unit,
                                                 'design_unit': unit, 'verified': True}})
        return {'status': 'PARTIAL' if warnings else 'SUCCESS', 'fragments': fragments,
                'pages': [{'page': None, 'status': 'CAD_OBJECT_METADATA'}], 'warnings': warnings,
                'parser_version': CAD_VERSION, 'cad_level': 'OBJECT_METADATA', 'takeoffs': takeoffs,
                'cad_units': {'code': unit_code, 'unit': unit}}
    except Exception as exc:
        # DXF readers can fail while loading or lazily traversing malformed
        # objects.  This is a recoverable per-file capability failure: do not
        # let it turn the enclosing parser worker into a generic FAILED state,
        # and never include a file path or converter output in the warning.
        return {
            'status': 'PARTIAL', 'fragments': [],
            'pages': [{'page': None, 'status': 'CAD_OBJECT_METADATA_UNAVAILABLE'}],
            'warnings': [f'CAD对象解析不可用：{type(exc).__name__}。原文件未被修改。'],
            'parser_version': CAD_VERSION, 'cad_level': 'UNAVAILABLE', 'takeoffs': [],
        }
    finally:
        if holder is not None:
            holder.cleanup()
