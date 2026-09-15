"""有限资源文本解析。未处理的图形、扫描件和CAD显式进入Coverage。"""
from __future__ import annotations
import codecs
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, asdict, field
from datetime import date
from pathlib import Path
from time import monotonic
from defusedxml import ElementTree as ET
from app.cad import parse_cad
from app.visual_pipeline import (OCR_VERSION, local_ocr_available, ocr_image_file,
                                 ocr_pdf_page, pdf_cropbox_local_bbox,
                                 pdf_geometry_summary, PDF_CROP_COORDINATE_SYSTEM)

PARSER_VERSION='multisource-4'
MAX_CHARS=2_000_000
MAX_FRAGMENT_CHARS=1600
# The default simple-task envelope is 6,000 conservative UTF-8 units. Leave
# room for prompt, schema, evidence identity and locator metadata.
MAX_FRAGMENT_BYTES=2200
PROGRESS_INTERVAL_SECONDS=5.0
IMAGE_SUFFIXES={'.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp'}
LOW_TEXT_WORDS=8
LOW_TEXT_CHARS=80

@dataclass
class Fragment:
    text: str
    locator: dict
    method: str
    internal_revision_date: str | None = None
    revision_label: str | None = None
    text_map: list[dict] = field(default_factory=list)
    confidence: float | None = None

def locator(**kwargs):
    return dict(page_number=None,sheet=None,section=None,paragraph=None,bbox=None,
                coordinate_system=None,text_line_start=None,text_line_end=None,native_element_id=None,**kwargs) if not kwargs else {
        **locator(),**kwargs}

def revision(text: str):
    """只接受明确标记的ISO内部修订日期，不从文件名/上传时间推断。"""
    ds=re.findall(r'(?:revision\s*date|rev\.?\s*date|修订日期|修訂日期)\s*[:：]?\s*(\d{4}-\d{2}-\d{2})',text,re.I)
    try: dates={date.fromisoformat(d).isoformat() for d in ds}
    except ValueError: dates=set()
    labels=re.findall(r'(?:^|\n)\s*(?:Revision|Rev\.?|修订号)\s*[:：]\s*([A-Za-z0-9.-]+)',text,re.I)
    return (next(iter(dates)) if len(dates)==1 else None,
            labels[0] if len(set(labels))==1 else None)

def split_lines(lines: list[str], loc: dict, method: str, rev: tuple) -> list[Fragment]:
    out=[]; buf=[]; first=1; chars=0;size=0
    def flush(last):
        nonlocal buf,chars,size,first
        text='\n'.join(buf)
        if text.strip():
            out.append(Fragment(text, {**loc,'text_line_start':first,'text_line_end':last},method,*rev))
        buf=[];chars=0;size=0
    for i,line in enumerate(lines,1):
        line_size=len(line.encode('utf-8'))
        if len(line)>MAX_FRAGMENT_CHARS or line_size>MAX_FRAGMENT_BYTES:
            if buf: flush(i-1)
            for part in split_text(line):
                out.append(Fragment(part,{**loc,'text_line_start':i,'text_line_end':i},method,*rev))
            first=i+1
            continue
        if buf and (chars+len(line)+1>MAX_FRAGMENT_CHARS or size+line_size+1>MAX_FRAGMENT_BYTES):
            flush(i-1);first=i
        if not buf:first=i
        buf.append(line);chars+=len(line)+1;size+=line_size+1
    flush(len(lines))
    return out


def split_text(text: str) -> list[str]:
    """Split without cutting Unicode code points; preserve exact source text."""
    parts=[];start=0;size=0
    for index,char in enumerate(text):
        width=len(char.encode('utf-8'))
        if index>start and (index-start>=MAX_FRAGMENT_CHARS or size+width>MAX_FRAGMENT_BYTES):
            parts.append(text[start:index]);start=index;size=0
        size+=width
    if start<len(text):parts.append(text[start:])
    return parts

def result_payload(status: str, fragments: list[Fragment], pages: list[dict], warnings: list[str],
                   page_count: int | None = None, **extras) -> dict:
    result={'status':status,'fragments':[asdict(x) for x in fragments],
            'pages':list(pages),'warnings':list(warnings),'parser_version':PARSER_VERSION}
    if page_count is not None:result['page_count']=page_count
    result.update(extras)
    return result

def parse_file(path: Path, original_name: str, progress: Callable[[dict],None] | None = None) -> dict:
    ext=Path(original_name).suffix.lower(); fragments=[]; warnings=[]; pages=[];page_count=None
    visual_tasks=[];geometry=[]
    if ext in IMAGE_SUFFIXES:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        if local_ocr_available():
            fragments=[Fragment(**item) for item in ocr_image_file(path)]
        state='OCR_EXTRACTED_VISUAL_PENDING' if fragments else 'NEEDS_VISION'
        pages=[{'page':1,'status':state}]
        visual_tasks=[{'page':1,'reason':'IMAGE_INPUT','status':'PENDING'}]
        if fragments:
            warnings.append(f'图片已用本机{OCR_VERSION}提取文字和坐标；结果需对照原图审核。')
        else:
            warnings.append('图片没有得到可用本地OCR文字；需要视觉模型或人工检查。')
        return result_payload('PARTIAL',fragments,pages,warnings,1,visual_tasks=visual_tasks,
                              geometry_summaries=[])
    if ext in ('.dwg','.dxf'):
        return parse_cad(path,original_name)
    if ext=='.txt':
        data=path.read_bytes() if path.stat().st_size<=MAX_CHARS*4 else path.open('rb').read(MAX_CHARS*4)
        if path.stat().st_size>len(data): warnings.append('文本超出当前单文件解析资源限额，剩余内容未处理。')
        enc='utf-16' if data.startswith((codecs.BOM_UTF16_LE,codecs.BOM_UTF16_BE)) else 'utf-8-sig'
        try:text=data.decode(enc)
        except UnicodeDecodeError:
            return {'status':'FAILED','fragments':[],'pages':[],
                    'warnings':['文本编码不是受支持的UTF-8或带BOM的UTF-16；需转换，不猜编码。'],'parser_version':PARSER_VERSION}
        if len(text)>MAX_CHARS: text=text[:MAX_CHARS];warnings.append('字符上限后的内容未处理。')
        fragments=split_lines(text.splitlines(),locator(), 'TXT', revision(text))
        pages=[{'page':1,'status':'TEXT_EXTRACTED'}]
    elif ext=='.docx':
        with zipfile.ZipFile(path) as z:
            members=z.infolist()
            if sum(m.file_size for m in members)>100_000_000 or any(m.file_size/max(m.compress_size,1)>200 for m in members):
                raise ValueError('DOCX解压资源限制触发')
            xml=z.read('word/document.xml'); root=ET.fromstring(xml)
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            body=root.find('w:body',ns)
            if body is None: raise ValueError('缺少DOCX正文')
            full='\n'.join(root.itertext());rev=revision(full)
            count=0
            for n,element in enumerate(body,1):
                lines=[]
                for p in element.findall('.//w:p',ns) if element.tag.endswith('}tbl') else [element]:
                    texts=[t.text or '' for t in p.findall('.//w:t',ns)]
                    line=''.join(texts)
                    if line:lines.append(line)
                if not lines:continue
                count+=sum(map(len,lines))
                if count>MAX_CHARS:warnings.append('DOCX超出解析字符限额，后续内容未处理。');break
                fragments.extend(split_lines(lines,locator(native_element_id=f'body-element-{n}'), 'DOCX',rev))
            if any(m.filename.startswith('word/media/') for m in members): warnings.append('DOCX内嵌图片未分析。')
            if root.findall('.//w:ins',ns) or root.findall('.//w:del',ns):warnings.append('DOCX含修订痕迹，接受状态未判定，需人工检查原件。')
            if any(re.match(r'word/(header|footer|comments|footnotes|endnotes)',m.filename) for m in members):warnings.append('DOCX页眉/页脚/批注/注脚等附属内容未提取。')
            warnings.append('DOCX仅正文与表格文字；表格合并关系和父条款继承未完成。')
            pages=[{'page':None,'status':'BODY_TEXT_EXTRACTED'}]
    elif ext=='.pdf':
        import pdfplumber
        last_progress=monotonic()-PROGRESS_INTERVAL_SECONDS
        def save_progress():
            nonlocal last_progress
            now=monotonic()
            if progress is not None and now-last_progress>=PROGRESS_INTERVAL_SECONDS:
                progress(result_payload('PARTIAL',fragments,pages,warnings,page_count,
                                        visual_tasks=visual_tasks,geometry_summaries=geometry))
                last_progress=now
        with pdfplumber.open(path) as pdf:
            total=0;page_count=len(pdf.pages)
            for index,page in enumerate(pdf.pages,1):
                if total>=MAX_CHARS:
                    warnings.append('PDF超过单文件字符资源限额；后续页未处理。')
                    pages.extend({'page':j,'status':'NOT_PROCESSED'} for j in range(index,len(pdf.pages)+1))
                    page.close();break
                try:
                    # Analysis follows the visible CropBox, the same region rendered
                    # for OCR, preview, and external vision. This avoids parsing or
                    # transferring content intentionally cropped out of a PDF.
                    visible_page=page.crop(page.cropbox)
                    words=visible_page.extract_words()
                    page_text=visible_page.extract_text() or ''
                    visual=bool(visible_page.images or visible_page.lines or visible_page.curves or visible_page.rects
                                or visible_page.width>1000 or visible_page.height>1400)
                    low_text_density=bool(words) and visual and (
                        len(words)<LOW_TEXT_WORDS or len(page_text.strip())<LOW_TEXT_CHARS)
                    summary=pdf_geometry_summary(visible_page,index,page_text)
                    if any(summary['primitive_counts'].values()):
                        geometry.append(summary)
                    if visual:
                        visual_tasks.append({'page':index,'reason':'PDF_GRAPHICS','status':'PENDING'})
                    ocr=[]
                    if (not words or low_text_density) and local_ocr_available():
                        ocr=ocr_pdf_page(path,index)
                        if words and ocr:
                            layer_compact=re.sub(r'\s+','',page_text).casefold()
                            ocr_compact=re.sub(r'\s+','', '\n'.join(item['text'] for item in ocr)).casefold()
                            if ocr_compact==layer_compact:
                                ocr=[]
                    if not words:
                        if not visual:
                            visual_tasks.append({'page':index,'reason':'NO_TEXT_LAYER','status':'PENDING'})
                        if ocr:
                            ocr_rev=revision('\n'.join(item['text'] for item in ocr))
                            for item in ocr:
                                item['internal_revision_date'],item['revision_label']=ocr_rev
                            fragments.extend(Fragment(**item) for item in ocr)
                            pages.append({'page':index,'status':'OCR_EXTRACTED_VISUAL_PENDING'})
                            warnings.append(f'PDF第{index}页没有文字层；已用本机{OCR_VERSION}提取，需对照图面审核。')
                        else:
                            pages.append({'page':index,'status':'NEEDS_VISION'})
                            warnings.append(f'PDF第{index}页没有可用文字层，本地OCR也未得到文字；需要视觉或人工检查。')
                    else:
                        text=' '.join(w['text'] for w in words);rev=revision(page_text or text)
                        pages.append({'page':index,'status':('TEXT_AND_OCR_VISUAL_PENDING' if ocr else
                                     'TEXT_ONLY_VISUAL_PENDING' if visual else 'TEXT_EXTRACTED')})
                        if visual:warnings.append(f'PDF第{index}页含图像/线条/大幅面；已排入视觉分析。')
                        # 空间邻近词构成短片段，保留页坐标。尚不声称二维表格和施工对象解析。
                        buf=[];chars=0;size=0;safe_words=[]
                        for word in words:
                            safe_words.extend({**word,'text':part} for part in split_text(word['text']))
                        for word in safe_words:
                            word_size=len(word['text'].encode('utf-8'))
                            if buf and (chars+len(word['text'])+1>MAX_FRAGMENT_CHARS
                                        or size+word_size+1>MAX_FRAGMENT_BYTES):
                                fragments.append(pdf_fragment(buf,index,rev,page.cropbox));buf=[];chars=0;size=0
                            buf.append(word);chars+=len(word['text'])+1;size+=word_size+1
                        if buf:fragments.append(pdf_fragment(buf,index,rev,page.cropbox))
                        if ocr:
                            ocr_rev=revision('\n'.join(item['text'] for item in ocr))
                            for item in ocr:
                                item['internal_revision_date'],item['revision_label']=ocr_rev
                            fragments.extend(Fragment(**item) for item in ocr)
                            warnings.append(f'PDF第{index}页文字层密度低；已追加本机{OCR_VERSION}结果，需对照图面去重审核。')
                        elif low_text_density:
                            warnings.append(f'PDF第{index}页文字层密度低；本机OCR未增加可用文字，仍保留视觉/人工检查。')
                        total+=len(text)
                finally:
                    page.close()
                save_progress()
    else:
        return {'status':'UNSUPPORTED','fragments':[],'pages':[],
                'warnings':[f'格式{ext or "无扩展名"}尚未支持，未调用模型。'],'parser_version':PARSER_VERSION}
    if not fragments:warnings.append('没有可用于文本模型的内容，不代表文件没有要求。')
    return result_payload('PARTIAL' if warnings else 'SUCCESS',fragments,pages,warnings,page_count,
                          visual_tasks=visual_tasks,geometry_summaries=geometry)

def pdf_fragment(words, page, rev, cropbox=(0.0, 0.0, 0.0, 0.0)):
    text=' '.join(w['text'] for w in words)
    bbox=pdf_cropbox_local_bbox([min(w['x0'] for w in words),min(w['top'] for w in words),
                                 max(w['x1'] for w in words),max(w['bottom'] for w in words)],cropbox)
    mapping=[]; offset=0
    for w in words:
        mapping.append({'start':offset,'end':offset+len(w['text']),
                        'bbox':pdf_cropbox_local_bbox([w['x0'],w['top'],w['x1'],w['bottom']],cropbox)})
        offset += len(w['text'])+1
    return Fragment(text,locator(page_number=page,bbox=bbox,coordinate_system=PDF_CROP_COORDINATE_SYSTEM), 'TEXT_LAYER',*rev,text_map=mapping)
