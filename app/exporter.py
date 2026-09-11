"""从保存的记录导出，不调用模型。"""
from __future__ import annotations
import io
import json
from datetime import datetime,timezone
from app.db import Database

def collect(db:Database,run:dict,reviewed_only:bool=False,verifier=None):
    records=[]
    for row in db.all('SELECT envelope,review_version FROM records WHERE run_id=? ORDER BY kind,id',(run['id'],)):
        envelope=json.loads(row['envelope'])
        if reviewed_only and envelope['review']['status'] not in ('ACCEPTED','EDITED'):continue
        records.append(envelope)
    evidence=[json.loads(r['payload']) for r in db.all('SELECT payload FROM evidence WHERE run_id=?',(run['id'],))]
    reports={}
    for e in evidence:
        e['file_name']=db.one('SELECT name FROM documents WHERE id=?',(e['document_id'],))['name']
    for record in records:
        record_id=record['meta']['record_id']
        if verifier:reports[record_id]=verifier.get(record_id)
        else:
            row=db.one('SELECT payload FROM verification_reports WHERE record_id=?',(record_id,),False)
            reports[record_id]=json.loads(row['payload']) if row else {'record_id':record_id,'status':'NOT_CHECKED','fields':[]}
    return {'verifications':reports,'citation_basis':'引用文字由原始解析文本切片取得；不代表现场正确或完整无漏项。','notice':'工程审核候选；未审核或部分结果不可直接用作采购/施工依据。',
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
                         c.get('evidence_ids',c.get('related_evidence_ids')),c,
                         data.get('verifications',{}).get(record['meta']['record_id'],{}).get('status','NOT_CHECKED'),
                         list(dict.fromkeys(q['citation_id'] for f in data.get('verifications',{}).get(record['meta']['record_id'],{}).get('fields',[]) for q in f['citations']))])
        sheet(title,['记录ID','名称/要求','CSI','人工审核','证据状态','设计净量','来源ID','完整候选JSON','独立核验状态','原句引用ID'],rows)
    sheet('证据',['来源ID','文件ID','内部修订日期','定位','原文'],[[e['evidence_id'],e['document_id'],e['internal_revision_date'],e['locator'],e['raw_text']] for e in data['evidence']])
    checks=[];quotes={}
    for rid,report in data.get('verifications',{}).items():
        for f in report.get('fields',[]):
            checks.append([rid,f['path'],f['label'],f['claim'],f['basis'],f['status'],f['method'],[q['citation_id'] for q in f['citations']],f['issues'],report.get('checked_at'),f.get('request_id')])
            for q in f['citations']:quotes[q['citation_id']]=q
    ws_checks=sheet('字段核验',['记录ID','字段路径','属性','值/结论','依据类别','核验状态','核验方法','引用ID','问题说明','核验时间','调用ID'],checks)
    quote_items=list(quotes.values())
    ws_quotes=sheet('原句引用',['引用ID','来源ID','原文件名称','文件指纹','Revision','修订日期','页码/段落/位置','原句','起始偏移','结束偏移','引用粒度','文字基础','片段指纹'],
      [[q['citation_id'],q['evidence_id'],q['file_name'],q['file_sha256'],q['revision_label'],q['internal_revision_date'],q['locator'],q['quote'],q['start'],q['end'],q['granularity'],q['text_basis'],q['fragment_sha256']] for q in quote_items])
    quote_rows={q['citation_id']:i+2 for i,q in enumerate(quote_items)}
    evidence_rows={e['evidence_id']:i+2 for i,e in enumerate(data['evidence'])}
    for i,row in enumerate(checks,2):
        if row[7]:
            cell=ws_checks.cell(i,8);cell.hyperlink="#'原句引用'!A"+str(quote_rows[row[7][0]])
    for i,q in enumerate(quote_items,2):
        if q['evidence_id'] in evidence_rows:ws_quotes.cell(i,2).hyperlink="#'证据'!A"+str(evidence_rows[q['evidence_id']])
    for title in names.values():
        ws=wb[title]
        for i in range(2,ws.max_row+1):
            ids=json.loads(ws.cell(i,10).value or '[]')
            if ids:ws.cell(i,10).hyperlink="#'原句引用'!A"+str(quote_rows[ids[0]])
    out=io.BytesIO();wb.save(out);return out.getvalue()
