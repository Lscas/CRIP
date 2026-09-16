"""FR-PARSE-001/003/004，基础文字解析，不是施工视觉评测。"""
import io,zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image,ImageDraw
from app.parsers import MAX_FRAGMENT_BYTES,parse_file,revision
from app.parser_worker import save_result
from app.visual_pipeline import (PDF_CROP_COORDINATE_SYSTEM, ocr_pdf_page,
                                 render_pdf_page, render_visual_png)

@pytest.mark.parametrize('text,expected',[
 ('Revision Date: 2026-08-01','2026-08-01'),('Upload Date: 2026-08-01',None),
 ('Revision Date: 2026-02-30',None),('Revision Date: 2026-01-01\nRevision Date: 2026-02-01',None),
 ('修订日期：2026-07-02','2026-07-02'),('2026-08-01',None)])
def test_revision_only_internal(text,expected):assert revision(text)[0]==expected

def test_txt_lines_and_encoding(tmp_path):
    p=tmp_path/'x.txt';p.write_text('Revision Date: 2026-01-01\nline two\nthird',encoding='utf-8')
    r=parse_file(p,p.name);assert r['status']=='SUCCESS'
    f=r['fragments'][0];assert f['locator']['text_line_start']==1 and f['locator']['text_line_end']==3
    p.write_bytes(b'\xff\xfe'+ '中文'.encode('utf-16-le'))
    assert parse_file(p,p.name)['fragments'][0]['text']=='中文'
    p.write_bytes(b'\xffbad');assert parse_file(p,p.name)['status']=='FAILED'

def test_txt_long_no_drop(tmp_path):
    p=tmp_path/'x.txt';text='x'*4000;p.write_text(text, encoding="utf-8")
    r=parse_file(p,p.name);assert ''.join(f['text'] for f in r['fragments'])==text

def test_txt_long_chinese_splits_by_utf8_budget_without_data_loss(tmp_path):
    p=tmp_path/'long-zh.txt';text='施工材料与检查要求。'*156;p.write_text(text,encoding='utf-8')
    fragments=parse_file(p,p.name)['fragments']
    assert len(fragments)>1 and ''.join(f['text'] for f in fragments)==text
    assert all(len(f['text'].encode('utf-8'))<=MAX_FRAGMENT_BYTES for f in fragments)

def test_docx_minimal(tmp_path):
    p=tmp_path/'x.docx'
    xml='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Material alpha</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Quantity 2</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
    with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml',xml)
    r=parse_file(p,p.name);assert len(r['fragments'])==2 and r['status']=='PARTIAL'
    assert r['fragments'][1]['locator']['native_element_id']=='body-element-2'

def test_docx_entity_rejected(tmp_path):
    p=tmp_path/'x.docx'
    with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml','<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><x>&y;</x>')
    with pytest.raises(Exception):parse_file(p,p.name)

def test_image_pending(tmp_path):
    p=tmp_path/'a.png';Image.new('RGB',(10,10)).save(p)
    r=parse_file(p,p.name);assert r['status']=='PARTIAL' and not r['fragments']


def test_local_ocr_returns_traceable_boxes_and_confidence(tmp_path):
    p=tmp_path/'ocr.png';image=Image.new('RGB',(900,180),'white')
    ImageDraw.Draw(image).text((30,55),'MATERIAL DOOR D-101',fill='black',stroke_width=1)
    image.save(p)
    r=parse_file(p,p.name)
    assert len(r['visual_tasks'])==1
    task=r['visual_tasks'][0]
    assert (task['reason'],task['region_type'],task['bbox'],task['page_type'])==(
        'IMAGE_INPUT','FULL_PAGE',None,'GRAPHIC_OR_SCAN')
    assert r['fragments'] and all(f['method']=='OCR' for f in r['fragments'])
    assert all(0<=f['confidence']<=1 for f in r['fragments'])
    assert all(f['locator']['coordinate_system']=='image-pixels-top-left-exif-normalized' for f in r['fragments'])
    assert all(len(f['locator']['bbox'])==4 and f['text_map'] for f in r['fragments'])

def test_image_render_applies_exif_orientation_and_names_coordinate_system(tmp_path):
    path=tmp_path/'oriented.jpg';image=Image.new('RGB',(80,40),'white')
    exif=Image.Exif();exif[274]=6;image.save(path,exif=exif)
    raw,width,height,coordinate_system=render_visual_png(path,path.name)
    with Image.open(io.BytesIO(raw)) as rendered:
        assert rendered.size==(40,80)
    assert (width,height)==(40.0,80.0)
    assert coordinate_system=='image-pixels-top-left-exif-normalized'

def test_pdf_text_and_bbox(tmp_path):
    from reportlab.pdfgen import canvas
    p=tmp_path/'a.pdf';c=canvas.Canvas(str(p));c.drawString(72,700,'Provide test materials.');c.save()
    progress=[];r=parse_file(p,p.name,progress=progress.append)
    assert progress and progress[-1]['fragments'][0]['text']=='Provide test materials.'
    assert progress[-1]['page_count']==1
    assert r['fragments'][0]['locator']['page_number']==1
    assert len(r['fragments'][0]['locator']['bbox'])==4
    assert 'Provide' in r['fragments'][0]['text']


def test_single_pdf_page_workers_preserve_order_and_content(tmp_path):
    from reportlab.pdfgen import canvas
    path=tmp_path/'parallel.pdf';drawing=canvas.Canvas(str(path))
    for page in range(1,9):
        drawing.drawString(72,700,f'Page {page} copper pipe requirement')
        drawing.showPage()
    drawing.save()

    serial=parse_file(path,path.name,workers=1)
    parallel=parse_file(path,path.name,workers=2)

    assert parallel['pages']==serial['pages']
    assert [item['text'] for item in parallel['fragments']]==[item['text'] for item in serial['fragments']]
    assert [item['locator']['page_number'] for item in parallel['fragments']]==list(range(1,9))


def test_rich_text_page_with_decorative_rule_skips_vision_but_large_sheet_keeps_it(tmp_path):
    from reportlab.pdfgen import canvas
    text='Provide materials and execute inspections in accordance with the project specifications.'
    ordinary=tmp_path/'ordinary.pdf';drawing=canvas.Canvas(str(ordinary))
    drawing.drawString(72,700,text);drawing.line(72,680,500,680);drawing.save()
    assert parse_file(ordinary,ordinary.name)['visual_tasks']==[]
    large=tmp_path/'large.pdf';drawing=canvas.Canvas(str(large),pagesize=(1600,1000))
    drawing.drawString(72,900,text);drawing.line(72,880,1500,880);drawing.save()
    tasks=parse_file(large,large.name)['visual_tasks']
    assert len(tasks)==1 and tasks[0]['reason']=='LARGE_FORMAT'
    assert tasks[0]['region_type']=='FULL_PAGE' and tasks[0]['page_type']=='DRAWING'


def test_pdf_cropbox_is_the_only_rendered_and_analyzed_region(tmp_path,monkeypatch):
    """All page-derived locators are local to the same visible CropBox PNG."""
    from reportlab.pdfgen import canvas
    path=tmp_path/'cropped.pdf';drawing=canvas.Canvas(str(path),pagesize=(600,800))
    # The non-zero crop origin deliberately excludes the HIDDEN label.
    drawing.setCropBox([100,100,500,600])
    drawing.drawString(150,750,'HIDDEN OUTSIDE CROPBOX')
    drawing.drawString(150,400,'VISIBLE INSIDE CROPBOX')
    drawing.save()

    rendered,width,height=render_pdf_page(path,1,resolution=72)
    assert (width,height)==(400.0,500.0) and rendered.size==(400,500)
    raw,vwidth,vheight,coordinate_system=render_visual_png(path,path.name,1)
    assert (vwidth,vheight,coordinate_system)==(400.0,500.0,PDF_CROP_COORDINATE_SYSTEM)
    with Image.open(io.BytesIO(raw)) as preview:
        assert preview.width / preview.height == pytest.approx(400 / 500,rel=0.01)
    cropped,cwidth,cheight,crop_system=render_visual_png(path,path.name,1,[50,50,250,300])
    assert (cwidth,cheight,crop_system)==(400.0,500.0,PDF_CROP_COORDINATE_SYSTEM)
    with Image.open(io.BytesIO(cropped)) as preview:
        assert preview.width / preview.height == pytest.approx(200 / 250,rel=0.01)

    result=parse_file(path,path.name)
    text='\n'.join(fragment['text'] for fragment in result['fragments'])
    assert 'VISIBLE INSIDE CROPBOX' in text and 'HIDDEN OUTSIDE CROPBOX' not in text
    fragment=result['fragments'][0]
    assert fragment['locator']['coordinate_system']==PDF_CROP_COORDINATE_SYSTEM
    assert all(0 <= value <= limit for value,limit in zip(fragment['locator']['bbox'],[400,500,400,500]))
    assert all(0 <= value <= limit for value,limit in zip(fragment['text_map'][0]['bbox'],[400,500,400,500]))

    class Engine:
        def __call__(self,array):
            height_px,width_px=array.shape[:2]
            return SimpleNamespace(txts=['OCR VISIBLE'],scores=[0.9],boxes=[[
                [0,0],[width_px / 2,0],[width_px / 2,height_px / 2],[0,height_px / 2]
            ]])
    monkeypatch.setattr('app.visual_pipeline.local_ocr_available',lambda:True)
    monkeypatch.setattr('app.visual_pipeline._ocr_engine',lambda:Engine())
    ocr=ocr_pdf_page(path,1)
    assert ocr[0]['locator']['coordinate_system']==PDF_CROP_COORDINATE_SYSTEM
    assert ocr[0]['locator']['bbox']==pytest.approx([0,0,200,250],abs=1.0)


def test_low_density_pdf_text_layer_also_runs_ocr_fallback(tmp_path,monkeypatch):
    from reportlab.pdfgen import canvas
    path=tmp_path/'sparse-scan.pdf';drawing=canvas.Canvas(str(path))
    drawing.drawString(72,700,'X');drawing.line(72,650,144,650);drawing.save()
    fake=[{'text':'VISIBLE SCANNED NOTE','locator':{'page_number':1,'sheet':None,'section':None,
           'paragraph':None,'bbox':[10,20,200,40],'coordinate_system':'pdf-points-top-left',
           'text_line_start':None,'text_line_end':None,'native_element_id':None},
           'method':'OCR','internal_revision_date':None,'revision_label':None,
           'text_map':[{'start':0,'end':20,'bbox':[10,20,200,40]}],'confidence':0.91}]
    monkeypatch.setattr('app.parsers.local_ocr_available',lambda:True)
    monkeypatch.setattr('app.parsers.ocr_pdf_page',lambda source,page:fake)
    result=parse_file(path,path.name)
    assert {fragment['method'] for fragment in result['fragments']}=={'TEXT_LAYER','OCR'}
    assert result['pages'][0]['status']=='TEXT_AND_OCR_VISUAL_PENDING'
    assert any('文字层密度低' in warning for warning in result['warnings'])


def test_pdf_vector_geometry_requires_explicit_scale_and_does_not_claim_material_quantity(tmp_path):
    from reportlab.pdfgen import canvas
    p=tmp_path/'scaled.pdf';c=canvas.Canvas(str(p));c.drawString(72,700,'SCALE: 1/4" = 1\'-0"')
    c.line(72,650,144,650);c.save()
    r=parse_file(p,p.name);g=r['geometry_summaries'][0]
    assert g['calibration']['ratio']==48 and g['status']=='CALIBRATED_RAW_GEOMETRY'
    assert g['calibrated_total_length']['unit']=='FT'
    assert g['material_quantity'] is None and '不作为设计净量' in g['scope_note']


def test_pdf_spec_structure_and_schedule_rows_keep_relationships_and_route_one_crop(tmp_path):
    from reportlab.pdfgen import canvas
    path=tmp_path/'schedule.pdf';drawing=canvas.Canvas(str(path),pagesize=(1600,1000))
    drawing.drawString(72,940,'SECTION 22 11 16')
    drawing.drawString(72,910,'PART 2 - PRODUCTS')
    drawing.drawString(72,880,'2.1 EQUIPMENT SCHEDULE')
    xs=[72,360,620,820];ys=[820,780,740]
    for x in xs:drawing.line(x,ys[-1],x,ys[0])
    for y in ys:drawing.line(xs[0],y,xs[-1],y)
    drawing.drawString(82,792,'Equipment');drawing.drawString(370,792,'Size');drawing.drawString(630,792,'Quantity')
    drawing.drawString(82,752,'Backflow preventer');drawing.drawString(370,752,'2 inch');drawing.drawString(630,752,'2')
    drawing.save()

    result=parse_file(path,path.name)

    assert result['pages'][0]['page_type']=='SCHEDULE'
    assert result['pages'][0]['table_count']>=1
    rows=[item for item in result['fragments'] if (item['locator']['paragraph'] or '').startswith('Table ')]
    assert rows and any('Backflow preventer' in item['text'] and '2 inch' in item['text'] for item in rows)
    assert all('22 11 16' in (item['locator']['section'] or '') for item in rows)
    assert len(result['visual_tasks'])==1
    task=result['visual_tasks'][0]
    assert task['region_type']=='TABLE' and len(task['bbox'])==4
    assert task['coordinate_system']==PDF_CROP_COORDINATE_SYSTEM


def test_dxf_object_count_and_known_unit_geometry_are_pending_review(tmp_path):
    import ezdxf
    p=tmp_path/'drawing.dxf';doc=ezdxf.new();doc.units=6
    doc.layers.add('PIPE');msp=doc.modelspace();msp.add_line((0,0),(3,4),dxfattribs={'layer':'PIPE'})
    block=doc.blocks.new('VALVE');block.add_circle((0,0),1)
    msp.add_blockref('VALVE',(1,1),dxfattribs={'layer':'PIPE'});msp.add_blockref('VALVE',(2,2),dxfattribs={'layer':'PIPE'})
    doc.saveas(p)
    r=parse_file(p,p.name)
    assert r['cad_level']=='OBJECT_METADATA' and r['status']=='SUCCESS'
    assert any(f['method']=='CAD_OBJECT' and f['confidence']==1 for f in r['fragments'])
    count=next(x for x in r['takeoffs'] if x['kind']=='BLOCK_COUNT')
    length=next(x for x in r['takeoffs'] if x['kind']=='LAYER_LENGTH' and x['label']=='PIPE')
    assert (count['label'],count['value'],count['unit'],count['review_status'])==('VALVE',2,'EA','PENDING')
    assert count['basis']=='DESIGN_MODEL_OBJECTS' and '才能作为设计净量' in count['scope_note']
    assert length['value']>=5 and length['unit']=='M' and length['calibration']['verified'] is True


def test_dxf_same_block_name_on_multiple_layers_has_matching_per_layer_evidence(tmp_path):
    import ezdxf
    path=tmp_path/'multi-layer.dxf';document=ezdxf.new();document.units=4
    document.layers.add('A');document.layers.add('B')
    block=document.blocks.new('VALVE');block.add_circle((0,0),1)
    model=document.modelspace();model.add_blockref('VALVE',(1,1),dxfattribs={'layer':'A'})
    model.add_blockref('VALVE',(2,2),dxfattribs={'layer':'B'});document.saveas(path)
    result=parse_file(path,path.name)
    counts=[item for item in result['takeoffs'] if item['kind']=='BLOCK_COUNT']
    assert [(item['layer'],item['value']) for item in counts]==[('A',1),('B',1)]
    for item in counts:
        evidence=result['fragments'][item['source_fragment_index']]['text']
        assert f'CAD layer: {item["layer"]}' in evidence and 'Block inserts: VALVE=1' in evidence


def test_malformed_dxf_is_a_traceable_cad_capability_failure(tmp_path):
    path=tmp_path/'broken.dxf';path.write_text('this is not a DXF document',encoding='utf-8')
    result=parse_file(path,path.name)
    assert result['status']=='PARTIAL' and result['cad_level']=='UNAVAILABLE'
    assert result['takeoffs']==[] and result['fragments']==[]
    assert result['warnings']==['CAD对象解析不可用：OSError。原文件未被修改。']


def test_dwg_conversion_with_malformed_derived_dxf_is_traceable(tmp_path,monkeypatch):
    """A bad converter result must not escape into parser_worker FAILED."""
    import tempfile
    from app.cad import parse_cad
    source=tmp_path/'input.dwg';source.write_bytes(b'unchanged-source')
    holder=tempfile.TemporaryDirectory();derived=Path(holder.name)/'derived.dxf'
    derived.write_text('not a DXF',encoding='utf-8')
    monkeypatch.setattr('app.cad._convert_dwg',lambda _: (derived,holder,'Synthetic converter'))
    result=parse_cad(source,source.name)
    assert result['status']=='PARTIAL' and result['cad_level']=='UNAVAILABLE'
    assert result['warnings']==['CAD对象解析不可用：OSError。原文件未被修改。']
    assert source.read_bytes()==b'unchanged-source'


def test_cad_measurement_formula_inputs_exclude_text_and_insert_handles(tmp_path):
    import ezdxf
    path=tmp_path/'mixed.dxf';document=ezdxf.new();document.units=6
    document.layers.add('PIPE');model=document.modelspace()
    line=model.add_line((0,0),(3,4),dxfattribs={'layer':'PIPE'})
    text=model.add_text('NOT A LENGTH',dxfattribs={'layer':'PIPE'})
    block=document.blocks.new('VALVE');block.add_circle((0,0),1)
    insert=model.add_blockref('VALVE',(1,1),dxfattribs={'layer':'PIPE'})
    document.saveas(path)
    result=parse_file(path,path.name)
    length=next(item for item in result['takeoffs'] if item['kind']=='LAYER_LENGTH')
    assert length['formula']=='SUM_ENTITY_LENGTHS' and length['value']==pytest.approx(5)
    assert length['entity_ids']==[line.dxf.handle]
    assert text.dxf.handle not in length['entity_ids'] and insert.dxf.handle not in length['entity_ids']

def test_parser_worker_progress_write_is_atomic(tmp_path):
    destination=tmp_path/'progress.json';result={'status':'PARTIAL','fragments':[],'pages':[],'warnings':[]}
    save_result(destination,result)
    assert destination.read_text(encoding='utf-8')
    assert not destination.with_name(destination.name+'.tmp').exists()
