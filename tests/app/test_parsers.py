"""FR-PARSE-001/003/004，基础文字解析，不是施工视觉评测。"""
import io,zipfile
from pathlib import Path
import pytest
from PIL import Image
from app.parsers import parse_file,revision

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

def test_pdf_text_and_bbox(tmp_path):
    from reportlab.pdfgen import canvas
    p=tmp_path/'a.pdf';c=canvas.Canvas(str(p));c.drawString(72,700,'Provide test materials.');c.save()
    r=parse_file(p,p.name);assert r['fragments'][0]['locator']['page_number']==1
    assert len(r['fragments'][0]['locator']['bbox'])==4
    assert 'Provide' in r['fragments'][0]['text']
