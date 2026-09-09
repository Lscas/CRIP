"""从保存的记录导出，不调用模型。"""
from __future__ import annotations
import io
import json
from datetime import datetime,timezone
from app.db import Database

def collect(db:Database,run:dict,reviewed_only:bool=False):
    records=[]
    for row in db.all('SELECT envelope,review_version FROM records WHERE run_id=? ORDER BY kind,id',(run['id'],)):
        envelope=json.loads(row['envelope'])
        if reviewed_only and envelope['review']['status'] not in ('ACCEPTED','EDITED'):continue
        records.append(envelope)
    evidence=[json.loads(r['payload']) for r in db.all('SELECT payload FROM evidence WHERE run_id=?',(run['id'],))]
    return {'notice':'工程审核候选；未审核或部分结果不可直接用作采购/施工依据。',
            'generated_at':datetime.now(timezone.utc).isoformat(),'run':run,
            'cost':db.cost(run['project_id']),'reviewed_only':reviewed_only,'records':records,'evidence':evidence}

def as_json(data:dict)->bytes:
    return json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')

def as_xlsx(data:dict)->bytes:
    # 用户应用依赖通用可安装的openpyxl，不依赖ChatGPT专有运行时。
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill,Alignment
    wb=Workbook();wb.remove(wb.active)
    def sheet(title,header,rows):
        ws=wb.create_sheet(title);ws.append(header);ws.freeze_panes='A2'
        for row in rows:
            ws.append(['' if v is None else json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for v in row])
        for cell in ws[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='20364B')
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value,str):
                    cell.data_type='s'  # 文件/模型文本不作为公式执行。
                    if len(cell.value)>32760:cell.value=cell.value[:32680]+' [内容过长；完整字段见JSON导出]'
                cell.alignment=Alignment(vertical='top',wrap_text=True)
        for i,column in enumerate(ws.columns,1):
            from openpyxl.utils import get_column_letter
            ws.column_dimensions[get_column_letter(i)].width=22 if i<4 else 48
        ws.auto_filter.ref=ws.dimensions
        return ws
    sheet('运行说明',['项目','运行','状态','声明','覆盖','费用'],[[data['run']['project_id'],data['run']['id'],data['run']['status'],data['notice'],data['run']['coverage'],data['cost']]])
    names={'MATERIAL':'材料清单','INSPECTION':'检查测试报告','CONFLICT':'冲突差异','MISSING':'缺失信息'}
    for kind,title in names.items():
        rows=[]
        for record in data['records']:
            if record['kind']!=kind:continue
            c=record['candidate'];name=c.get('name') or c.get('requirement') or c.get('subject')
            rows.append([record['meta']['record_id'],name,c.get('csi_sections'),record['review']['status'],
                         c.get('requirement_status') or c.get('resolution_status'),c.get('quantity'),
                         c.get('evidence_ids',c.get('related_evidence_ids')),c])
        sheet(title,['记录ID','名称/要求','CSI','人工审核','证据状态','设计净量','来源ID','完整候选JSON'],rows)
    sheet('证据',['来源ID','文件ID','内部修订日期','定位','原文'],[[e['evidence_id'],e['document_id'],e['internal_revision_date'],e['locator'],e['raw_text']] for e in data['evidence']])
    out=io.BytesIO();wb.save(out);return out.getvalue()
