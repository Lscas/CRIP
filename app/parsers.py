"""有限资源文本解析。未处理的图形、扫描件和CAD显式进入Coverage。"""
from __future__ import annotations
import codecs
import re
import zipfile
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from defusedxml import ElementTree as ET

PARSER_VERSION='text-baseline-1'
MAX_CHARS=2_000_000
MAX_FRAGMENT_CHARS=1600
IMAGE_SUFFIXES={'.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp'}

@dataclass
class Fragment:
    text: str
    locator: dict
    method: str
    internal_revision_date: str | None = None
    revision_label: str | None = None

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
    out=[]; buf=[]; first=1; size=0
    def flush(last):
        nonlocal buf,size,first
        text='\n'.join(buf)
        if text.strip():
            out.append(Fragment(text, {**loc,'text_line_start':first,'text_line_end':last},method,*rev))
        buf=[];size=0
    for i,line in enumerate(lines,1):
        if len(line)>MAX_FRAGMENT_CHARS:
            if buf: flush(i-1)
            for start in range(0,len(line),MAX_FRAGMENT_CHARS):
                out.append(Fragment(line[start:start+MAX_FRAGMENT_CHARS],{**loc,'text_line_start':i,'text_line_end':i},method,*rev))
            first=i+1
            continue
        if buf and size+len(line)+1>MAX_FRAGMENT_CHARS:
            flush(i-1);first=i
        if not buf:first=i
        buf.append(line);size+=len(line)+1
    flush(len(lines))
    return out

def parse_file(path: Path, original_name: str) -> dict:
    ext=Path(original_name).suffix.lower(); fragments=[]; warnings=[]; pages=[]
    if ext in IMAGE_SUFFIXES:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return {'status':'PARTIAL','fragments':[],'pages':[{'page':1,'status':'NEEDS_VISION'}],
                'warnings':['图像已接收；本轮未接入OCR或视觉模型，不能宣称已读图。'],'parser_version':PARSER_VERSION}
    if ext=='.dwg':
        return {'status':'PARTIAL','fragments':[],'pages':[],
                'warnings':['CAD转换适配器未接入；DWG保留在清单，未完成内容分析。'],'parser_version':PARSER_VERSION}
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
        with pdfplumber.open(path) as pdf:
            total=0
            for index,page in enumerate(pdf.pages,1):
                if total>=MAX_CHARS:
                    warnings.append('PDF超过单文件字符资源限额；后续页未处理。')
                    pages.extend({'page':j,'status':'NOT_PROCESSED'} for j in range(index,len(pdf.pages)+1));break
                words=page.extract_words()
                if not words:
                    pages.append({'page':index,'status':'NEEDS_VISION'})
                    warnings.append(f'PDF第{index}页没有可用文字层；需要OCR/视觉。');continue
                text=' '.join(w['text'] for w in words);rev=revision(page.extract_text() or text)
                visual=bool(page.images or page.lines or page.curves or page.width>1000 or page.height>1400)
                pages.append({'page':index,'status':'TEXT_ONLY_VISUAL_PENDING' if visual else 'TEXT_EXTRACTED'})
                if visual:warnings.append(f'PDF第{index}页含图像/线条/大幅面；当前只分析文字层。')
                # 空间邻近词构成短片段，保留页坐标。尚不声称二维表格和施工对象解析。
                buf=[]; n=0
                for word in words:
                    if n+len(word['text'])>MAX_FRAGMENT_CHARS and buf:
                        fragments.append(pdf_fragment(buf,index,rev));buf=[];n=0
                    buf.append(word);n+=len(word['text'])+1
                if buf:fragments.append(pdf_fragment(buf,index,rev))
                total+=len(text)
                page.close()
    else:
        return {'status':'UNSUPPORTED','fragments':[],'pages':[],
                'warnings':[f'格式{ext or "无扩展名"}尚未支持，未调用模型。'],'parser_version':PARSER_VERSION}
    if not fragments:warnings.append('没有可用于文本模型的内容，不代表文件没有要求。')
    return {'status':'PARTIAL' if warnings else 'SUCCESS','fragments':[asdict(x) for x in fragments],
            'pages':pages,'warnings':warnings,'parser_version':PARSER_VERSION}

def pdf_fragment(words, page, rev):
    text=' '.join(w['text'] for w in words)
    bbox=[min(w['x0'] for w in words),min(w['top'] for w in words),max(w['x1'] for w in words),max(w['bottom'] for w in words)]
    return Fragment(text,locator(page_number=page,bbox=bbox,coordinate_system='pdf-points-top-left'), 'TEXT_LAYER',*rev)
