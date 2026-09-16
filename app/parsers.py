"""有限资源文本解析。未处理的图形、扫描件和CAD显式进入Coverage。"""
from __future__ import annotations
import codecs
import re
import zipfile
from concurrent.futures import ProcessPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass, asdict, field
from datetime import date
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic
from defusedxml import ElementTree as ET
from app.cad import parse_cad
from app.visual_pipeline import (OCR_VERSION, local_ocr_available, ocr_image_file,
                                 ocr_pdf_page, pdf_cropbox_local_bbox,
                                 pdf_geometry_summary, PDF_CROP_COORDINATE_SYSTEM)

PARSER_VERSION='multisource-6'
PAGE_ROUTER_VERSION='pdf-page-router-1'
MAX_CHARS=2_000_000
MAX_FRAGMENT_CHARS=1600
# The default simple-task envelope is 6,000 conservative UTF-8 units. Leave
# room for prompt, schema, evidence identity and locator metadata.
MAX_FRAGMENT_BYTES=2200
PROGRESS_INTERVAL_SECONDS=5.0
PDF_PAGE_CHUNK=4
IMAGE_SUFFIXES={'.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp'}
LOW_TEXT_WORDS=8
LOW_TEXT_CHARS=80
_SPEC_SECTION=re.compile(r'\b(?:SECTION\s+)?\d{2}\s+\d{2}\s+\d{2}\b',re.I)
_SPEC_SECTION_HEADING=re.compile(
    r'^\s*(?:SECTION\s+)?\d{2}\s+\d{2}\s+\d{2}(?:\s*[-–—:]\s*[^;]{1,100})?\s*$',re.I)
_SPEC_PART=re.compile(r'^\s*PART\s+[123IVX]+\b(?:\s*[-–—:]\s*.*)?$',re.I|re.M)
_DIVISION=re.compile(r'^\s*DIVISION\s+\d{1,2}\b(?:\s*[-–—:]\s*.*)?$',re.I|re.M)
_CLAUSE=re.compile(r'^\s*((?:\d+\.)+\d+|\d+\.|[A-Z]\.|\([a-z0-9]+\))\s+',re.I)
_SCHEDULE=re.compile(r'\b(?:MATERIAL|EQUIPMENT|DOOR|WINDOW|FINISH|FIXTURE|PANEL|VALVE|LIGHTING)?\s*SCHEDULE\b',re.I)
_RFI_HEADER=re.compile(
    r'(?im)^\s*(?:REQUEST\s+FOR\s+INFORMATION|RFI)(?:\s+(?:NO\.?|NUMBER)|\s*#)?\s*[:#-]?\s*'
    r'([A-Z0-9][A-Z0-9.-]{0,30})?\s*$')
_SUBMITTAL_HEADER=re.compile(
    r'(?im)^\s*(?:SUBMITTAL|SUBMISSION)(?:\s+(?:NO\.?|NUMBER)|\s*#)?\s*[:#-]?\s*'
    r'([A-Z0-9][A-Z0-9.-]{0,30})?\s*$')
_RFI_ROLE=re.compile(r'(?im)^\s*(QUESTION|REQUEST|RESPONSE|ANSWER|REPLY)\s*[:#-]?\s*')
_SUBMITTAL_STATUS=re.compile(
    r'(?im)^\s*(?:SUBMITTAL\s+)?STATUS\s*[:#-]\s*'
    r'(APPROVED\s+AS\s+NOTED|REVISE\s+AND\s+RESUBMIT|REJECTED|APPROVED|REVIEWED|PENDING)\b')
_EMAIL_RFI_SUBJECT=re.compile(
    r'(?im)^Subject:\s*(?:Re:\s*)?(?:REQUEST\s+FOR\s+INFORMATION|RFI)'
    r'(?:\s+(?:NO\.?|NUMBER)|\s*#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9.-]{0,30})?')
_EMAIL_SUBMITTAL_SUBJECT=re.compile(
    r'(?im)^Subject:\s*(?:Re:\s*)?(?:SUBMITTAL|SUBMISSION)'
    r'(?:\s+(?:NO\.?|NUMBER)|\s*#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9.-]{0,30})?')

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

def split_lines(lines: list[str], loc: dict, method: str, rev: tuple,
                line_offset: int = 0) -> list[Fragment]:
    out=[]; buf=[]; first=1; chars=0;size=0
    def flush(last):
        nonlocal buf,chars,size,first
        text='\n'.join(buf)
        if text.strip():
            out.append(Fragment(text, {**loc,'text_line_start':first+line_offset,
                                      'text_line_end':last+line_offset},method,*rev))
        buf=[];chars=0;size=0
    for i,line in enumerate(lines,1):
        line_size=len(line.encode('utf-8'))
        if len(line)>MAX_FRAGMENT_CHARS or line_size>MAX_FRAGMENT_BYTES:
            if buf: flush(i-1)
            for part in split_text(line):
                out.append(Fragment(part,{**loc,'text_line_start':i+line_offset,
                                          'text_line_end':i+line_offset},method,*rev))
            first=i+1
            continue
        if buf and (chars+len(line)+1>MAX_FRAGMENT_CHARS or size+line_size+1>MAX_FRAGMENT_BYTES):
            flush(i-1);first=i
        if not buf:first=i
        buf.append(line);chars+=len(line)+1;size+=line_size+1
    flush(len(lines))
    return out


def _clean_identifier(value: str | None, prefix: str) -> str | None:
    if not value:return None
    value=value.strip().upper()
    if value in {'QUESTION','REQUEST','RESPONSE','ANSWER','REPLY','NO','NUMBER'}:return None
    return value.removeprefix(prefix).lstrip(' #:-') or None


def document_context(text: str, original_name: str = '') -> dict:
    """Return a conservative workflow label; it is routing metadata, not approval."""
    searchable=text[:80_000]
    rfi=_RFI_HEADER.search(searchable) or _EMAIL_RFI_SUBJECT.search(searchable)
    if not rfi:
        rfi=re.search(r'(?i)(?:^|[\s_.-])RFI(?:[\s_.#-]+([A-Z0-9][A-Z0-9.-]{0,30}))?',
                      Path(original_name).stem)
    if rfi is not None:
        identifier=_clean_identifier(rfi.group(1) if rfi.lastindex else None,'RFI')
        roles={value.upper() for value in _RFI_ROLE.findall(searchable)}
        role=('MIXED' if {'QUESTION','REQUEST'} & roles and {'RESPONSE','ANSWER','REPLY'} & roles else
              'RESPONSE' if {'RESPONSE','ANSWER','REPLY'} & roles else 'QUESTION')
        return {'document_type':'RFI_RESPONSE' if role=='RESPONSE' else 'RFI_QUESTION',
                'workflow_type':'RFI','identifier':identifier,'role':role,'status':None}
    submittal=_SUBMITTAL_HEADER.search(searchable) or _EMAIL_SUBMITTAL_SUBJECT.search(searchable)
    if not submittal:
        submittal=re.search(r'(?i)(?:^|[\s_.-])SUBMITTAL(?:[\s_.#-]+([A-Z0-9][A-Z0-9.-]{0,30}))?',
                            Path(original_name).stem)
    if submittal is not None:
        identifier=_clean_identifier(submittal.group(1) if submittal.lastindex else None,'SUBMITTAL')
        status_match=_SUBMITTAL_STATUS.search(searchable)
        return {'document_type':'SUBMITTAL','workflow_type':'SUBMITTAL','identifier':identifier,
                'role':None,'status':status_match.group(1).upper() if status_match else None}
    return {'document_type':'UNKNOWN','workflow_type':None,'identifier':None,'role':None,'status':None}


def _workflow_label(context: dict, role: str | None = None) -> str | None:
    workflow=context.get('workflow_type')
    if not workflow:return None
    label=workflow
    if context.get('identifier'):label+=' '+context['identifier']
    if workflow=='RFI':label+=' > '+(role or context.get('role') or 'MIXED')
    if workflow=='SUBMITTAL' and context.get('status'):label+=' > STATUS: '+context['status']
    return label


def annotate_workflow_fragments(fragments: list[Fragment], text: str, original_name: str,
                                *, prefix: str | None = None) -> dict:
    context=document_context(text,original_name);current=context.get('role')
    for fragment in fragments:
        matches={value.upper() for value in _RFI_ROLE.findall(fragment.text)}
        if {'QUESTION','REQUEST'} & matches and {'RESPONSE','ANSWER','REPLY'} & matches:current='MIXED'
        elif {'QUESTION','REQUEST'} & matches:current='QUESTION'
        elif {'RESPONSE','ANSWER','REPLY'} & matches:current='RESPONSE'
        workflow=_workflow_label(context,current)
        existing=fragment.locator.get('section')
        section=' > '.join(value for value in (prefix,workflow,existing) if value)
        if section:fragment.locator['section']=section
    return context


def split_workflow_lines(lines: list[str], loc: dict, method: str, rev: tuple,
                         text: str, original_name: str, *, prefix: str | None = None
                         ) -> tuple[list[Fragment],dict]:
    """Split explicit RFI question/response headings before ordinary size batching."""
    context=document_context(text,original_name)
    if context.get('workflow_type')!='RFI':
        fragments=split_lines(lines,loc,method,rev)
        annotate_workflow_fragments(fragments,text,original_name,prefix=prefix)
        return fragments,context
    groups=[];start=0;current='QUESTION';buffer=[]
    for index,line in enumerate(lines):
        match=_RFI_ROLE.match(line)
        role=None
        if match:
            value=match.group(1).upper()
            role='RESPONSE' if value in {'RESPONSE','ANSWER','REPLY'} else 'QUESTION'
        if role and buffer:
            groups.append((start,buffer,current));buffer=[];start=index
        if not buffer:start=index
        if role:current=role
        buffer.append(line)
    if buffer:groups.append((start,buffer,current))
    fragments=[]
    for offset,group,role in groups:
        section=' > '.join(value for value in (prefix,_workflow_label(context,role),loc.get('section')) if value)
        fragments.extend(split_lines(group,{**loc,'section':section or None},method,rev,offset))
    return fragments,context


class _PlainHTML(HTMLParser):
    """Extract visible HTML text without executing or fetching active content."""
    _BLOCKS={'address','article','br','div','h1','h2','h3','h4','h5','h6','li','p','section','table','tr'}
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        tag=tag.casefold()
        if tag in {'script','style','noscript'}:self.hidden+=1
        elif not self.hidden and tag in self._BLOCKS:self.parts.append('\n')
    def handle_endtag(self,tag):
        tag=tag.casefold()
        if tag in {'script','style','noscript'} and self.hidden:self.hidden-=1
        elif not self.hidden and tag in self._BLOCKS:self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)
    def text(self):
        return '\n'.join(line.strip() for line in ''.join(self.parts).splitlines() if line.strip())


def _email_part_text(part) -> tuple[str,str] | None:
    content_type=part.get_content_type()
    if content_type not in {'text/plain','text/html'}:return None
    try:value=part.get_content()
    except (LookupError,UnicodeError,ValueError):return None
    if not isinstance(value,str):return None
    if content_type=='text/html':
        parser=_PlainHTML();parser.feed(value);parser.close();value=parser.text()
    return content_type,value


def _parse_email(path: Path, original_name: str) -> dict:
    size=path.stat().st_size
    if size>MAX_CHARS*4:
        return result_payload('FAILED',[],[],['Email exceeds the local 8 MB parsing limit; no model was called.'],
                              document_type='EMAIL',workflow_type=None,attachments=[])
    message=BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    headers=[]
    for name in ('Subject','From','To','Cc','Date'):
        value=message.get(name)
        if value is not None:headers.append(f'{name}: {str(value)[:4000]}')
    attachments=[];plain=[];html=[]
    parts=message.walk() if message.is_multipart() else [message]
    for part in parts:
        disposition=part.get_content_disposition();filename=part.get_filename()
        if disposition=='attachment' or filename:
            attachments.append({'file_name':str(filename or 'unnamed attachment')[:240],
                                'content_type':part.get_content_type(),'status':'NOT_PROCESSED'})
            continue
        if part.is_multipart():continue
        parsed=_email_part_text(part)
        if parsed:
            (plain if parsed[0]=='text/plain' else html).append(parsed[1])
    body='\n\n'.join(value for value in (plain or html) if value.strip())
    if len(body)>MAX_CHARS:body=body[:MAX_CHARS]
    full='\n'.join(headers+[body]);rev=revision(full);fragments=[]
    context=document_context(full,original_name)
    if headers:
        header_section=' > '.join(value for value in ('EMAIL > HEADERS',_workflow_label(context,context.get('role'))) if value)
        fragments.extend(split_lines(headers,locator(section=header_section,native_element_id='email-headers'),
                                     'EMAIL',rev))
    if body:
        body_fragments,context=split_workflow_lines(
            body.splitlines(),locator(native_element_id='email-body'),'EMAIL',rev,full,original_name,
            prefix='EMAIL > BODY')
        fragments.extend(body_fragments)
    else:context=document_context(full,original_name)
    warnings=[]
    if attachments:warnings.append('Email attachments were inventoried but not analyzed; upload each attachment separately.')
    if not body:warnings.append('Email has no supported plain-text or HTML body.')
    pages=[{'page':None,'status':'EMAIL_BODY_EXTRACTED' if body else 'EMAIL_HEADERS_ONLY'}]
    return result_payload('PARTIAL' if warnings else 'SUCCESS',fragments,pages,warnings,
                          document_type='EMAIL',workflow_type=context.get('workflow_type'),
                          document_identifier=context.get('identifier'),workflow_status=context.get('status'),
                          attachments=attachments,active_content_processed=False)


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


def _page_type(words: list[dict], text: str, graphics: int, width: float, height: float) -> str:
    """Cheap deterministic router; it does not claim semantic document classification."""
    large=width>1000 or height>1400
    if _SCHEDULE.search(text):return 'SCHEDULE'
    if _SPEC_SECTION.search(text) or _SPEC_PART.search(text) or _DIVISION.search(text):return 'SPEC_PAGE'
    if not words:return 'GRAPHIC_OR_SCAN' if graphics else 'BLANK'
    if large or graphics>=40:return 'DRAWING'
    if len(words)>=120:return 'SPEC_PAGE'
    return 'MIXED' if graphics else 'TEXT_PAGE'


def _word_lines(words: list[dict]) -> list[list[dict]]:
    """Group native PDF words into visible lines without inventing reading order across columns."""
    lines=[]
    for word in sorted(words,key=lambda item:(float(item['top']),float(item['x0']))):
        top=float(word['top']);height=max(1.0,float(word['bottom'])-top)
        if lines:
            previous=lines[-1]
            anchor=sum(float(item['top']) for item in previous)/len(previous)
            tolerance=max(2.0,min(5.0,height*.45))
            if abs(top-anchor)<=tolerance:
                previous.append(word);continue
        lines.append([word])
    for line in lines:line.sort(key=lambda item:float(item['x0']))
    return lines


def _heading_update(text: str, section: str | None, part: str | None) -> tuple[str | None,str | None,str | None]:
    value=' '.join(text.split())
    section_match=_SPEC_SECTION_HEADING.match(value)
    if section_match:
        number=_SPEC_SECTION.search(section_match.group(0)).group(0)
        prefix='SECTION '+number.upper().removeprefix('SECTION ').strip()
        return prefix,None,prefix
    if _DIVISION.match(value):
        division=value.upper()
        return division,None,division
    if _SPEC_PART.match(value):
        part=value.upper()
        return section,part,' > '.join(item for item in (section,part) if item)
    return section,part,None


def _find_tables(page, page_type: str, text: str) -> list:
    if page_type not in {'SPEC_PAGE','SCHEDULE'} and not _SCHEDULE.search(text):return []
    if not _SCHEDULE.search(text) and len(page.lines or [])+len(page.rects or [])<4:return []
    try:
        return list(page.find_tables() or [])[:12]
    except Exception:
        # Table recovery is additive. A malformed vector grid must not discard
        # native text or turn the whole document into a parser failure.
        return []


def _inside_table(word: dict, boxes: list[tuple[float,float,float,float]]) -> bool:
    x=(float(word['x0'])+float(word['x1']))/2;y=(float(word['top'])+float(word['bottom']))/2
    return any(x0<=x<=x1 and top<=y<=bottom for x0,top,x1,bottom in boxes)


def _structured_pdf_fragments(page, page_number: int, words: list[dict], rev: tuple,
                              tables: list) -> list[Fragment]:
    """Retain section, clause and table-row relationships in canonical evidence."""
    table_boxes=[tuple(float(value) for value in table.bbox) for table in tables]
    fragments=[];section=None;part=None;paragraph=None;headings=[];block=[];block_context=None;block_number=0

    def flush():
        nonlocal block,block_number
        if not block:return
        block_number+=1
        fragments.append(pdf_fragment(
            block,page_number,rev,page.cropbox,
            section=block_context[0],paragraph=block_context[1],
            native_element_id=f'page-{page_number}-block-{block_number}'))
        block=[]

    for line in _word_lines(words):
        line_text=' '.join(item['text'] for item in line).strip()
        if not line_text:continue
        section,part,heading=_heading_update(line_text,section,part)
        if heading:
            flush();paragraph=None;headings.append((min(float(item['top']) for item in line),heading))
        clause=_CLAUSE.match(line_text)
        if clause and not heading:
            flush();paragraph=clause.group(1)
        if all(_inside_table(item,table_boxes) for item in line):
            flush();continue
        context=(' > '.join(item for item in (section,part) if item) or None,paragraph)
        if block and context!=block_context:flush()
        block_context=context
        for word in line:
            if _inside_table(word,table_boxes):continue
            for value in split_text(word['text']):
                candidate={**word,'text':value}
                proposed=block+[candidate]
                if block and (sum(len(item['text'])+1 for item in proposed)>MAX_FRAGMENT_CHARS
                              or sum(len(item['text'].encode('utf-8'))+1 for item in proposed)>MAX_FRAGMENT_BYTES):
                    flush();block_context=context
                block.append(candidate)
    flush()

    for table_number,table in enumerate(tables,1):
        rows=table.extract() or []
        table_top=float(table.bbox[1])
        table_section=next((value for top,value in reversed(headings) if top<=table_top),None)
        row_objects=list(getattr(table,'rows',[]) or [])
        for row_number,cells in enumerate(rows,1):
            values=[' '.join(str(cell or '').split()) for cell in cells]
            if not any(values):continue
            raw=' | '.join(values)
            for piece_number,piece in enumerate(split_text(raw),1):
                row_bbox=(row_objects[row_number-1].bbox if row_number<=len(row_objects) else table.bbox)
                fragments.append(Fragment(
                    piece,locator(page_number=page_number,
                                  section=table_section,paragraph=f'Table {table_number} row {row_number}',
                                  bbox=pdf_cropbox_local_bbox(row_bbox,page.cropbox),
                                  coordinate_system=PDF_CROP_COORDINATE_SYSTEM,
                                  native_element_id=f'page-{page_number}-table-{table_number}-row-{row_number}-part-{piece_number}'),
                    'TEXT_LAYER',*rev))
    return sorted(fragments,key=lambda item:((item.locator.get('bbox') or [0,0])[1],
                                              (item.locator.get('bbox') or [0,0])[0],
                                              item.locator.get('native_element_id') or ''))


def _local_bbox(page, bbox) -> list[float]:
    return pdf_cropbox_local_bbox([float(value) for value in bbox],page.cropbox)


def _visual_tasks(page, page_number: int, page_type: str, tables: list,
                  reason: str) -> list[dict]:
    """Prefer one useful high-resolution table crop; otherwise retain one overview call."""
    if tables and page_type in {'SCHEDULE','SPEC_PAGE'}:
        table=max(tables,key=lambda item:(item.bbox[2]-item.bbox[0])*(item.bbox[3]-item.bbox[1]))
        x0,top,x1,bottom=_local_bbox(page,table.bbox)
        width,height=float(page.width),float(page.height);pad=8.0
        bbox=[max(0.0,x0-pad),max(0.0,top-pad),min(width,x1+pad),min(height,bottom+pad)]
        return [{'page':page_number,'reason':reason,'status':'PENDING',
                 'region_id':f'page-{page_number}-table-1','region_type':'TABLE','bbox':bbox,
                 'coordinate_system':PDF_CROP_COORDINATE_SYSTEM,'page_type':page_type}]
    return [{'page':page_number,'reason':reason,'status':'PENDING',
             'region_id':f'page-{page_number}-overview','region_type':'FULL_PAGE','bbox':None,
             'coordinate_system':PDF_CROP_COORDINATE_SYSTEM,'page_type':page_type}]

def _parse_pdf_page(path: Path, page, index: int, default_context: dict | None = None) -> dict:
    fragments=[];warnings=[];visual_tasks=[];geometry=[];text_chars=0
    try:
        # Analysis follows the visible CropBox used by OCR, preview, and vision.
        visible_page=page.crop(page.cropbox)
        words=visible_page.extract_words()
        page_text=visible_page.extract_text() or ''
        graphics=len(visible_page.images)+len(visible_page.lines)+len(visible_page.curves)+len(visible_page.rects)
        large_format=visible_page.width>1000 or visible_page.height>1400
        page_type=_page_type(words,page_text,graphics,visible_page.width,visible_page.height)
        tables=_find_tables(visible_page,page_type,page_text)
        if tables and _SCHEDULE.search(page_text):page_type='SCHEDULE'
        low_text_density=bool(words) and graphics>0 and (
            len(words)<LOW_TEXT_WORDS or len(page_text.strip())<LOW_TEXT_CHARS)
        visual=large_format or (graphics>0 and (not words or low_text_density or graphics>=40))
        summary=pdf_geometry_summary(visible_page,index,page_text)
        if any(summary['primitive_counts'].values()):geometry.append(summary)
        if visual:
            reason='LARGE_FORMAT' if large_format else 'LOW_TEXT_GRAPHICS' if low_text_density or not words else 'DENSE_GRAPHICS'
            visual_tasks.extend(_visual_tasks(visible_page,index,page_type,tables,reason))
        ocr=[]
        if (not words or low_text_density) and local_ocr_available():
            ocr=ocr_pdf_page(path,index)
            if words and ocr:
                layer_compact=re.sub(r'\s+','',page_text).casefold()
                ocr_compact=re.sub(r'\s+','','\n'.join(item['text'] for item in ocr)).casefold()
                if ocr_compact==layer_compact:ocr=[]
        if not words:
            if ocr:
                ocr_rev=revision('\n'.join(item['text'] for item in ocr))
                for item in ocr:item['internal_revision_date'],item['revision_label']=ocr_rev
                fragments.extend(Fragment(**item) for item in ocr)
                status='OCR_EXTRACTED_VISUAL_PENDING'
                warnings.append(f'PDF第{index}页没有文字层；已用本机{OCR_VERSION}提取，需对照图面审核。')
            else:
                status='NEEDS_VISION' if visual else 'NO_CONTENT'
                warnings.append(f'PDF第{index}页没有可用文字层，本地OCR也未得到文字；'
                                +('已排入视觉或人工检查。' if visual else '未发现值得发送的图形内容，需人工确认空白页。'))
        else:
            text=' '.join(word['text'] for word in words);text_chars=len(text);rev=revision(page_text or text)
            status=('TEXT_AND_OCR_VISUAL_PENDING' if ocr else
                    'TEXT_ONLY_VISUAL_PENDING' if visual else 'TEXT_EXTRACTED')
            if visual:warnings.append(f'PDF第{index}页含图像/线条/大幅面；已排入视觉分析。')
            fragments.extend(_structured_pdf_fragments(visible_page,index,words,rev,tables))
            if ocr:
                ocr_rev=revision('\n'.join(item['text'] for item in ocr))
                for item in ocr:item['internal_revision_date'],item['revision_label']=ocr_rev
                fragments.extend(Fragment(**item) for item in ocr)
                warnings.append(f'PDF第{index}页文字层密度低；已追加本机{OCR_VERSION}结果，需对照图面去重审核。')
            elif low_text_density:
                warnings.append(f'PDF第{index}页文字层密度低；本机OCR未增加可用文字，仍保留视觉/人工检查。')
        page_source=page_text or '\n'.join(fragment.text for fragment in fragments)
        page_context=annotate_workflow_fragments(fragments,page_source,str(path))
        if not page_context.get('workflow_type') and default_context and default_context.get('workflow_type'):
            current=default_context.get('role')
            for fragment in fragments:
                matches={value.upper() for value in _RFI_ROLE.findall(fragment.text)}
                if {'QUESTION','REQUEST'} & matches and {'RESPONSE','ANSWER','REPLY'} & matches:current='MIXED'
                elif {'QUESTION','REQUEST'} & matches:current='QUESTION'
                elif {'RESPONSE','ANSWER','REPLY'} & matches:current='RESPONSE'
                existing=fragment.locator.get('section')
                workflow=_workflow_label(default_context,current)
                fragment.locator['section']=' > '.join(value for value in (workflow,existing) if value) or None
            page_context={**default_context,'role':current,
                          'document_type':'RFI_RESPONSE' if current=='RESPONSE' else default_context['document_type']}
        return {'fragments':fragments,'page':{'page':index,'status':status,'page_type':page_type,
                                              'router_version':PAGE_ROUTER_VERSION,'table_count':len(tables),
                                              'document_type':page_context.get('document_type','UNKNOWN')},'warnings':warnings,
                'visual_tasks':visual_tasks,'geometry_summaries':geometry,'text_chars':text_chars}
    finally:
        page.close()


def _parse_pdf_chunk(source: str, page_numbers: list[int], default_context: dict | None = None) -> list[dict]:
    """Open one PDF once per bounded chunk; each worker keeps its own OCR engine."""
    import pdfplumber
    path=Path(source)
    with pdfplumber.open(path) as pdf:
        return [_parse_pdf_page(path,pdf.pages[number-1],number,default_context) for number in page_numbers]


def parse_file(path: Path, original_name: str, progress: Callable[[dict],None] | None = None,
               workers: int = 1) -> dict:
    if workers not in (1,2,4):raise ValueError('本地PDF工作进程数必须为1、2或4')
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
        visual_tasks=[{'page':1,'reason':'IMAGE_INPUT','status':'PENDING','region_id':'page-1-overview',
                       'region_type':'FULL_PAGE','bbox':None,
                       'coordinate_system':'image-pixels-top-left-exif-normalized','page_type':'GRAPHIC_OR_SCAN'}]
        if fragments:
            warnings.append(f'图片已用本机{OCR_VERSION}提取文字和坐标；结果需对照原图审核。')
        else:
            warnings.append('图片没有得到可用本地OCR文字；需要视觉模型或人工检查。')
        return result_payload('PARTIAL',fragments,pages,warnings,1,visual_tasks=visual_tasks,
                              geometry_summaries=[])
    if ext in ('.dwg','.dxf'):
        return parse_cad(path,original_name)
    if ext=='.eml':
        return _parse_email(path,original_name)
    if ext=='.txt':
        data=path.read_bytes() if path.stat().st_size<=MAX_CHARS*4 else path.open('rb').read(MAX_CHARS*4)
        if path.stat().st_size>len(data): warnings.append('文本超出当前单文件解析资源限额，剩余内容未处理。')
        enc='utf-16' if data.startswith((codecs.BOM_UTF16_LE,codecs.BOM_UTF16_BE)) else 'utf-8-sig'
        try:text=data.decode(enc)
        except UnicodeDecodeError:
            return {'status':'FAILED','fragments':[],'pages':[],
                    'warnings':['文本编码不是受支持的UTF-8或带BOM的UTF-16；需转换，不猜编码。'],'parser_version':PARSER_VERSION}
        if len(text)>MAX_CHARS: text=text[:MAX_CHARS];warnings.append('字符上限后的内容未处理。')
        fragments,context=split_workflow_lines(text.splitlines(),locator(),'TXT',revision(text),text,original_name)
        pages=[{'page':1,'status':'TEXT_EXTRACTED','document_type':context['document_type']}]
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
            context=annotate_workflow_fragments(fragments,full,original_name)
            pages=[{'page':None,'status':'BODY_TEXT_EXTRACTED','document_type':context['document_type']}]
    elif ext=='.pdf':
        import pdfplumber
        default_context=document_context('',original_name)
        last_progress=monotonic()-PROGRESS_INTERVAL_SECONDS
        def save_progress():
            nonlocal last_progress
            now=monotonic()
            if progress is not None and now-last_progress>=PROGRESS_INTERVAL_SECONDS:
                progress(result_payload('PARTIAL',fragments,pages,warnings,page_count,
                                        visual_tasks=visual_tasks,geometry_summaries=geometry))
                last_progress=now
        with pdfplumber.open(path) as pdf:page_count=len(pdf.pages)
        total=0;limit_reached=False
        def merge_page(result):
            nonlocal total,limit_reached
            number=result['page']['page']
            if total>=MAX_CHARS:
                warnings.append('PDF超过单文件字符资源限额；后续页未处理。')
                pages.extend({'page':value,'status':'NOT_PROCESSED'} for value in range(number,page_count+1))
                limit_reached=True;return
            fragments.extend(result['fragments']);pages.append(result['page'])
            warnings.extend(result['warnings']);visual_tasks.extend(result['visual_tasks'])
            geometry.extend(result['geometry_summaries']);total+=result['text_chars'];save_progress()
        if workers==1:
            with pdfplumber.open(path) as pdf:
                for index,page in enumerate(pdf.pages,1):
                    merge_page(_parse_pdf_page(path,page,index,default_context))
                    if limit_reached:break
        else:
            # ponytail: bounded four-page chunks give one large PDF useful parallelism without a queue.
            window=workers*PDF_PAGE_CHUNK
            with ProcessPoolExecutor(max_workers=workers) as pool:
                for start in range(1,page_count+1,window):
                    groups=[list(range(first,min(first+PDF_PAGE_CHUNK,page_count+1)))
                            for first in range(start,min(start+window,page_count+1),PDF_PAGE_CHUNK)]
                    for results in pool.map(_parse_pdf_chunk,[str(path)]*len(groups),groups,
                                            [default_context]*len(groups)):
                        for result in results:
                            merge_page(result)
                            if limit_reached:break
                        if limit_reached:break
                    if limit_reached:break
    else:
        return {'status':'UNSUPPORTED','fragments':[],'pages':[],
                'warnings':[f'格式{ext or "无扩展名"}尚未支持，未调用模型。'],'parser_version':PARSER_VERSION}
    if not fragments:warnings.append('没有可用于文本模型的内容，不代表文件没有要求。')
    document_types={page.get('document_type') for page in pages if page.get('document_type') not in (None,'UNKNOWN')}
    document_type=(next(iter(document_types)) if len(document_types)==1 else
                   'UNKNOWN' if not document_types else 'OTHER')
    return result_payload('PARTIAL' if warnings else 'SUCCESS',fragments,pages,warnings,page_count,
                          visual_tasks=visual_tasks,geometry_summaries=geometry,
                          document_type=document_type)

def pdf_fragment(words, page, rev, cropbox=(0.0, 0.0, 0.0, 0.0), **location):
    text=' '.join(w['text'] for w in words)
    bbox=pdf_cropbox_local_bbox([min(w['x0'] for w in words),min(w['top'] for w in words),
                                 max(w['x1'] for w in words),max(w['bottom'] for w in words)],cropbox)
    mapping=[]; offset=0
    for w in words:
        mapping.append({'start':offset,'end':offset+len(w['text']),
                        'bbox':pdf_cropbox_local_bbox([w['x0'],w['top'],w['x1'],w['bottom']],cropbox)})
        offset += len(w['text'])+1
    return Fragment(text,locator(page_number=page,bbox=bbox,coordinate_system=PDF_CROP_COORDINATE_SYSTEM,
                                 **location), 'TEXT_LAYER',*rev,text_map=mapping)
