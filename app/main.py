"""本地单用户HTTP入口。默认不调用付费API。"""
from __future__ import annotations
import json
import hmac
import logging
from decimal import Decimal
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from fastapi import FastAPI,Request,Query
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel,ConfigDict,Field
from filelock import FileLock,Timeout as LockTimeout
from app.settings import Settings,ROOT,VERSION
from app.db import Database,DomainError,dumps,now,uid
from app.uploads import Uploads
from app.gateway import Gateway
from app.runner import Runner
from app.exporter import (collect,as_json,as_xlsx,reviewer_record_display,
                          reviewer_record_is_visible)
from app.remote_access import PreviewAccess
from contracts.runtime_rules import EvidenceScope,validate_candidate,validate_schema

class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class ProjectInput(Input):
    name:str=Field(min_length=1,max_length=150)
    budget_cny:Decimal=Field(default=Decimal('300'),ge=Decimal('0.01'),le=1000000,max_digits=13,decimal_places=6)
class BudgetInput(Input):
    limit_cny:Decimal=Field(ge=Decimal('0.01'),le=1000000,max_digits=13,decimal_places=6)
class RunInput(Input):local_workers:Literal[1,2,4]=2
class UploadInput(Input):name:str=Field(min_length=1,max_length=240);size:int=Field(ge=0)
class VerificationInput(Input):
    expected_version:int=Field(ge=0)
    semantic:bool=False

class ReconcileCallInput(Input):
    resolution:Literal['NOT_BILLED','BILLED']
    actual_cny:Decimal=Field(default=Decimal('0'),ge=0,le=100000)
    confirmation:Literal['PROVIDER_BILLING_CHECKED']
    note:str=Field(default='',max_length=1000)

class ReviewInput(Input):
    action:Literal['ACCEPTED','EDITED','REJECTED']
    expected_version:int=Field(ge=0)
    note:str=Field(default='',max_length=4000)
    candidate:dict|None=None
    quantity_action:Literal['PENDING','VERIFIED','REJECTED']|None=None


def create_app(settings:Settings|None=None)->FastAPI:
    s=settings or Settings.from_env();s.data_dir.mkdir(parents=True,exist_ok=True)
    db=Database(s.data_dir/'cirp.sqlite3');uploads=Uploads(db,s);gateway=Gateway(s,db);runner=Runner(db,s,uploads,gateway)
    @asynccontextmanager
    async def lifespan(app):
        lock=FileLock(str(s.data_dir/'server.lock'))
        try:lock.acquire(timeout=0)
        except LockTimeout:raise RuntimeError('该数据目录已有CIRP进程；原型只运行一个服务实例。')
        try:
            if s.start_worker:runner.start()
            yield
        finally:
            runner.close();gateway.close();lock.release()
    app=FastAPI(title='CIRP 开发原型',version=VERSION,lifespan=lifespan,
                docs_url=None if s.remote_enabled else '/docs',
                redoc_url=None if s.remote_enabled else '/redoc',
                openapi_url=None if s.remote_enabled else '/openapi.json')
    access=PreviewAccess(s)
    app.state.db=db;app.state.runner=runner;app.state.gateway=gateway;app.state.uploads=uploads;app.state.settings=s
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=list(s.allowed_hosts))
    @app.middleware('http')
    async def guard(request,call_next):
        # 唯一公开端点，仅暴露进程存活，不返回业务数据或版本/密钥。
        if s.render_free_preview and request.url.path == '/_health' and request.method in ('GET', 'HEAD'):
            if request.url.hostname not in s.allowed_hosts:
                return JSONResponse({'detail':'Invalid host'},400)
            if request.method == 'HEAD':
                return Response(status_code=200,headers={'Cache-Control':'no-store','X-Robots-Tag':'noindex'})
            return JSONResponse({'status':'ok'},headers={'Cache-Control':'no-store','X-Robots-Tag':'noindex'})
        if s.remote_enabled and not access.authorized(request):
            return access.reject()
        # 远程仍要求同源写入；未知Origin或缺少客户端标记不能改变数据。
        if s.remote_enabled:
            length=request.headers.get('content-length')
            if length:
                try:
                    if int(length) < 0 or int(length) > s.chunk_bytes + 65536:
                        return JSONResponse({'detail':'预览单次请求体超过上限'},413)
                except ValueError:
                    return JSONResponse({'detail':'无效Content-Length'},400)
        if request.url.path.startswith('/api/') and request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('x-cirp-client')!='browser':return JSONResponse({'detail':'缺少本地客户端标记'},403)
            origin=request.headers.get('origin')
            public_origins={f'https://{host}' for host in s.allowed_hosts} if s.remote_enabled else set()
            if origin and urlsplit(origin).netloc!=request.url.netloc and origin not in public_origins:
                return JSONResponse({'detail':'禁止跨站修改请求'},403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Cache-Control']='no-store'
        response.headers['X-Robots-Tag']='noindex, nofollow'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response
    @app.exception_handler(DomainError)
    async def domain_handler(request,exc):return JSONResponse({'detail':str(exc)},status_code=exc.code)

    @app.get('/api/health/version')
    def health():return {'app_version':VERSION,'spec_version':VERSION,'schema_version':'0.2.0','provider':s.provider}
    @app.get('/api/settings')
    def settings_view():return s.public()
    @app.get('/api/demo-files/{name}')
    def demo_file(name:str):
        if name not in ('01_original.txt','02_revision.txt'):
            raise DomainError('示例不存在',404)
        return FileResponse(ROOT/'examples/demo'/name,filename=name,media_type='text/plain; charset=utf-8')
    @app.get('/api/projects')
    def projects():return db.all('SELECT * FROM projects ORDER BY created_at DESC')
    @app.post('/api/projects',status_code=201)
    def project_create(data:ProjectInput):
        if not data.name.strip():raise DomainError('项目名不能为空')
        return db.create_project(data.name.strip(),data.budget_cny)
    @app.get('/api/projects/{pid}')
    def project_get(pid:str):return db.one('SELECT * FROM projects WHERE id=?',(pid,))
    @app.get('/api/projects/{pid}/manifest')
    def manifest(pid:str):
        db.one('SELECT * FROM projects WHERE id=?',(pid,))
        return {'uploads':db.all('SELECT * FROM uploads WHERE project_id=? ORDER BY created_at',(pid,)),
                'documents':db.all('SELECT id,project_id,name,size,sha256,created_at FROM documents WHERE project_id=? ORDER BY created_at',(pid,))}
    @app.get('/api/projects/{pid}/budget')
    def project_budget(pid:str):return db.cost(pid)
    @app.put('/api/projects/{pid}/budget')
    def project_budget_update(pid:str,data:BudgetInput):return db.set_budget_limit(pid,data.limit_cny)
    @app.post('/api/projects/{pid}/uploads',status_code=201)
    def upload_create(pid:str,data:UploadInput):return uploads.create(pid,data.name,data.size)
    @app.get('/api/uploads/{upload_id}')
    def upload_get(upload_id:str):return uploads.get(upload_id)
    @app.put('/api/uploads/{upload_id}/chunk')
    async def upload_chunk(upload_id:str,request:Request,offset:int=0):
        data=bytearray()
        async for part in request.stream():
            if len(data)+len(part)>s.chunk_bytes:raise DomainError('单次请求超过分片上限',413)
            data.extend(part)
        return uploads.write_chunk(upload_id,offset,bytes(data))
    @app.post('/api/uploads/{upload_id}/complete')
    def upload_complete(upload_id:str):return uploads.complete(upload_id)
    @app.post('/api/uploads/{upload_id}/abort')
    def upload_abort(upload_id:str):
        row=uploads.get(upload_id)
        if row['state']!='UPLOADING':raise DomainError('只可取消未完成上传',409)
        db.execute("UPDATE uploads SET state='ABORTED' WHERE id=?",(upload_id,))
        return uploads.get(upload_id)
    @app.get('/api/documents/{did}/file')
    def document_file(did:str):
        doc=db.one('SELECT * FROM documents WHERE id=?',(did,))
        return FileResponse(uploads.object_path(doc),filename=doc['name'],media_type='application/octet-stream')
    @app.post('/api/projects/{pid}/analysis-runs',status_code=202)
    def run_create(pid:str,data:RunInput|None=None):return runner.create(pid,(data or RunInput()).local_workers)
    @app.get('/api/projects/{pid}/analysis-runs')
    def runs_get(pid:str):return [runner.get(x['id']) for x in db.all('SELECT id FROM runs WHERE project_id=? ORDER BY created_at DESC',(pid,))]
    @app.get('/api/analysis-runs/{rid}')
    def run_get(rid:str):return runner.get(rid)
    @app.post('/api/analysis-runs/{rid}/{action}')
    def run_control(rid:str,action:Literal['pause','resume','cancel']):return runner.control(rid,action)
    @app.get('/api/analysis-runs/{rid}/cost')
    def run_cost(rid:str):return db.cost(runner.get(rid)['project_id'])
    @app.get('/api/analysis-runs/{rid}/takeoffs')
    def run_takeoffs(rid:str):
        runner.get(rid)
        rows=db.all('''SELECT r.document_id,r.summary,d.name FROM document_results r
                       JOIN documents d ON d.id=r.document_id WHERE r.run_id=? ORDER BY d.name''',(rid,))
        out=[]
        for row in rows:
            summary=json.loads(row['summary'])
            out.append({'document_id':row['document_id'],'file_name':row['name'],
                        'cad_level':summary.get('cad_level'),
                        'takeoffs':summary.get('takeoffs',[]),
                        'geometry_summaries':summary.get('geometry_summaries',[])})
        return out
    @app.get('/api/analysis-runs/{rid}/documents/{did}/pages/{page}/image')
    def run_page_image(rid:str,did:str,page:int,x0:float|None=None,y0:float|None=None,
                       x1:float|None=None,y1:float|None=None):
        run=runner.get(rid)
        if did not in run['document_ids']:raise DomainError('文件不属于该分析运行',404)
        doc=db.one('SELECT * FROM documents WHERE id=?',(did,))
        suffix=Path(doc['name']).suffix.lower()
        if suffix not in ('.pdf','.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp'):
            raise DomainError('该文件没有页面图像',422)
        if doc['size']>250*1024*1024:raise DomainError('页面预览暂限250MiB原文件',422)
        values=(x0,y0,x1,y1)
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise DomainError('局部图像预览需要完整裁剪坐标',422)
        crop=list(values) if all(value is not None for value in values) else None
        try:
            from app.visual_pipeline import render_visual_png
            data,_,_,_=render_visual_png(uploads.object_path(doc),doc['name'],page,crop)
        except Exception as exc:
            raise DomainError('页面图像不可用：'+type(exc).__name__,422) from exc
        return Response(data,media_type='image/png')
    @app.get('/api/projects/{pid}/unresolved-model-calls')
    def unresolved_model_calls(pid:str):return db.unresolved_calls(pid)
    @app.get('/api/projects/{pid}/call-reconciliation-events')
    def call_reconciliation_events(pid:str):return db.reconciliation_events(pid)
    @app.post('/api/model-calls/{call_id}/reconcile')
    def reconcile_model_call(call_id:str,data:ReconcileCallInput):
        return db.reconcile_call(call_id,data.resolution,data.actual_cny,data.note)
    @app.get('/api/analysis-runs/{rid}/records')
    def records_get(rid:str,kind:Literal['MATERIAL','INSPECTION','CONFLICT','MISSING']|None=None):
        runner.get(rid)
        rows=db.all('SELECT * FROM records WHERE run_id=?'+(' AND kind=?' if kind else '')+' ORDER BY kind,id',(rid,kind) if kind else (rid,))
        return [{'record':json.loads(r['envelope']),'review_version':r['review_version'],'verification':runner.verifier.get(r['id'])} for r in rows]
    @app.get('/api/analysis-runs/{rid}/record-summaries')
    def record_summaries_get(rid:str,kind:Literal['MATERIAL','INSPECTION']|None=None,
                             offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
        """Bounded reviewer list containing only tangible items and executable QA work."""
        runner.get(rid)
        rows=db.all('''SELECT r.id,r.envelope,r.review_version,
                               json_extract(vr.payload,'$.status') AS verification_status,
                               json_extract(vr.payload,'$.checked_at') AS verification_checked_at,
                               COALESCE(json_array_length(json_extract(vr.payload,'$.fields')),0) AS verification_field_count,
                               (SELECT json_extract(field.value,'$.status')
                                  FROM json_each(json_extract(vr.payload,'$.fields')) AS field
                                 WHERE json_extract(field.value,'$.path')='/name' LIMIT 1) AS name_verification_status
                        FROM records r LEFT JOIN verification_reports vr ON vr.record_id=r.id
                        WHERE r.run_id=? AND r.kind IN ('MATERIAL','INSPECTION')
                        ORDER BY r.kind,r.id''',(rid,))
        visible=[];counts={'MATERIAL':0,'INSPECTION':0}
        for row in rows:
            record=json.loads(row['envelope'])
            report={'fields':[{'path':'/name','status':row['name_verification_status']}]} if row['name_verification_status'] else {}
            if reviewer_record_is_visible(record,report):
                visible.append((row,record));counts[record['kind']]+=1
        selected=[pair for pair in visible if kind is None or pair[1]['kind']==kind]
        total=len(selected);items=[]
        keys=('name','requirement','subject','activity','reason','blocking_reason','requirement_status',
              'evidence_ids','design_properties','qa_type','csi_sections','performer_as_stated','witness_as_stated',
              'timing','frequency','acceptance_criteria','standard_reference','report_name','quantity',
              'material_kind','location','condition')
        for row,record in selected[offset:offset+limit]:
            candidate=record['candidate']
            report={'fields':[{'path':'/name','status':row['name_verification_status']}]} if row['name_verification_status'] else {}
            display=reviewer_record_display(record,report)
            items.append({'record':{'kind':record['kind'],'meta':record['meta'],'review':record['review'],
                                    'candidate':{key:candidate.get(key) for key in keys},'display':display},
                          'review_version':row['review_version'],
                          'verification':{'record_id':row['id'],'status':row['verification_status'] or 'NOT_CHECKED',
                                          'checked_at':row['verification_checked_at'],
                                          'field_count':row['verification_field_count']}})
        return {'items':items,'pagination':{'offset':offset,'limit':limit,'total':total,
                                             'next_offset':offset+len(items) if offset+len(items)<total else None},
                'counts':counts}
    @app.get('/api/records/{record_id}')
    def record_get(record_id:str):
        row=db.one('SELECT envelope,review_version FROM records WHERE id=?',(record_id,))
        return {'record':json.loads(row['envelope']),'review_version':row['review_version'],
                'verification':runner.verifier.get(record_id)}
    @app.get('/api/analysis-runs/{rid}/evidence/{eid}')
    def evidence_get(rid:str,eid:str):
        runner.get(rid)
        row=db.one('SELECT payload,status,error FROM evidence WHERE id=? AND run_id=?',(rid+':'+eid,rid))
        e=json.loads(row['payload']);doc=db.one('SELECT name FROM documents WHERE id=?',(e['document_id'],))
        return {'evidence':e,'file_name':doc['name'],'status':row['status'],'error':row['error']}
    @app.get('/api/records/{record_id}/verification')
    def verification_get(record_id:str):
        return runner.verifier.get(record_id)

    @app.post('/api/records/{record_id}/verification')
    def verification_refresh(record_id:str,data:VerificationInput):
        row=db.one('SELECT * FROM records WHERE id=?',(record_id,))
        if row['review_version']!=data.expected_version:raise DomainError('记录已变化，请刷新后核验',409)
        run=runner.get(row['run_id'])
        if run['status'] in ('RUNNING','QUEUED'):raise DomainError('分析正在运行，核验会自动进行',409)
        if data.semantic:
            return JSONResponse(runner.verifier.enqueue(record_id,data.expected_version),status_code=202)
        return runner.verifier.refresh(record_id)

    @app.get('/api/records/{record_id}/verification-history')
    def verification_history(record_id:str):
        db.one('SELECT id FROM records WHERE id=?',(record_id,))
        return [{'id':r['id'],'created_at':r['created_at'],'report':json.loads(r['payload'])}
                for r in db.all('SELECT * FROM verification_events WHERE record_id=? ORDER BY created_at',(record_id,))]

    @app.get('/api/verification-jobs/{job_id}')
    def verification_job_get(job_id:str):
        return db.one('SELECT * FROM verification_jobs WHERE id=?',(job_id,))

    def verified_citation(record_id,citation_id):
        report=runner.verifier.get(record_id)
        if report['status'] in ('STALE','NOT_CHECKED'):raise DomainError('引用已失效或尚未核验，请重新检查',409)
        item=next((q for f in report['fields'] for q in f['citations'] if q['citation_id']==citation_id),None)
        if item is None:raise DomainError('引用不存在',404)
        return report,item

    @app.get('/api/records/{record_id}/citations/{citation_id}')
    def citation_get(record_id:str,citation_id:str):
        report,item=verified_citation(record_id,citation_id)
        return {'citation':item,'run_id':report['source_run_id']}

    @app.get('/api/records/{record_id}/citations/{citation_id}/preview')
    def citation_preview(record_id:str,citation_id:str):
        import hashlib,io
        report,q=verified_citation(record_id,citation_id)
        doc=db.one('SELECT * FROM documents WHERE id=?',(q['document_id'],))
        if Path(doc['name']).suffix.lower()!='.pdf' or not q['locator'].get('page_number'):
            raise DomainError('该来源没有PDF图面预览，使用原句定位',422)
        if doc['size']>50*1024*1024:raise DomainError('图面预览暂限50MiB；原文定位及原件下载仍可用',422)
        path=uploads.object_path(doc)
        h=hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
        if h.hexdigest()!=q['file_sha256']:raise DomainError('原始文件指纹发生变化，不能显示为原引用',409)
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                page=pdf.pages[q['locator']['page_number']-1]
                from app.visual_pipeline import PDF_CROP_COORDINATE_SYSTEM, pdf_cropbox_dimensions
                width,height=pdf_cropbox_dimensions(page)
                # The preview is intentionally limited to CropBox, just like OCR
                # and vision rendering. Existing absolute PDF locators remain
                # supported; new CropBox-local locators are translated back only
                # for pdfplumber's drawing API.
                image=page.to_image(resolution=max(12,min(120,1600*72/max(width,height))),force_mediabox=False)
                boxes=q['word_boxes'] or ([q['locator']['bbox']] if q['locator'].get('bbox') else [])
                if q['locator'].get('coordinate_system')==PDF_CROP_COORDINATE_SYSTEM:
                    crop_x,crop_top,_,_=page.cropbox
                    boxes=[[box[0]+crop_x,box[1]+crop_top,box[2]+crop_x,box[3]+crop_top] for box in boxes]
                for box in boxes:image.draw_rect(box,fill=(255,210,0,65),stroke=(230,155,0,180),stroke_width=1)
                out=io.BytesIO();image.save(out,format='PNG')
        except Exception as exc:
            raise DomainError('PDF图面预览不可用：'+type(exc).__name__,422) from exc
        return Response(out.getvalue(),media_type='image/png')

    @app.get('/api/records/{record_id}/history')
    def review_history(record_id:str):
        db.one('SELECT id FROM records WHERE id=?',(record_id,))
        return db.all('SELECT * FROM review_events WHERE record_id=? ORDER BY created_at',(record_id,))
    @app.post('/api/records/{record_id}/review')
    def review(record_id:str,data:ReviewInput):
        with db.connect(True) as c:
            row=c.execute('SELECT * FROM records WHERE id=?',(record_id,)).fetchone()
            if not row:raise DomainError('结果不存在',404)
            status=c.execute('SELECT status FROM runs WHERE id=?',(row['run_id'],)).fetchone()[0]
            if status in ('QUEUED','RUNNING'):raise DomainError('分析运行中不可审核；结束或暂停后再审核',409)
            if row['review_version']!=data.expected_version:raise DomainError('记录已变化，请刷新后审核',409)
            before=json.loads(row['envelope']);after=json.loads(row['envelope'])
            if data.action=='EDITED':
                if data.candidate is None or not data.note.strip():raise DomainError('修改需要完整候选和修改说明')
                if data.candidate.get('candidate_key')!=before['candidate']['candidate_key']:raise DomainError('不可改变候选身份')
                evs={}
                for e in c.execute('SELECT payload FROM evidence WHERE run_id=?',(row['run_id'],)):
                    payload=json.loads(e[0]);evs[payload['evidence_id']]=payload
                scope=EvidenceScope('local',row['project_id'],before['meta']['input_snapshot_id'],evs)
                names={'MATERIAL':'material-item','INSPECTION':'inspection-item','CONFLICT':'conflict-item','MISSING':'missing-information-item'}
                try:validate_candidate(names[row['kind']],data.candidate,scope)
                except Exception as exc:raise DomainError('修改不符合数据/来源契约：'+type(exc).__name__)
                after['candidate']=data.candidate
            elif data.candidate is not None:raise DomainError('只有EDITED允许修改内容')
            event=uid('REVIEW');after['review']={'status':data.action,'event_id':event,'actor_id':'local-engineer'}
            if row['kind']=='MATERIAL' and after['candidate']['quantity'] is not None:
                after['quantity_review']=data.quantity_action or 'PENDING'
            elif data.quantity_action:raise DomainError('该条没有可审核的数量')
            validate_schema('record-envelope',after)
            c.execute('INSERT INTO review_events VALUES(?,?,?,?,?,?,?,?)',
                      (event,record_id,'local-engineer',data.action,dumps(before),dumps(after),data.note,now()))
            c.execute('UPDATE records SET envelope=?,review_version=review_version+1 WHERE id=?',(dumps(after),record_id))
        if data.action=='EDITED':runner.verifier.refresh(record_id)
        return {'record':after,'review_version':data.expected_version+1,'verification':runner.verifier.get(record_id)}
    @app.get('/api/analysis-runs/{rid}/exports/{fmt}')
    def export(rid:str,fmt:Literal['json','xlsx'],reviewed_only:bool=False):
        run=runner.get(rid)
        if run['status'] in ('RUNNING','QUEUED'):raise DomainError('请先暂停或等待分析结束，再导出一致快照',409)
        data=collect(db,run,reviewed_only,verifier=runner.verifier)
        raw=as_json(data) if fmt=='json' else as_xlsx(data)
        media='application/json' if fmt=='json' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return Response(raw,media_type=media,headers={'Content-Disposition':f'attachment; filename="cirp-{rid}.{fmt}"'})
    app.mount('/assets',StaticFiles(directory=ROOT/'web'),name='assets')
    @app.get('/')
    def index():return FileResponse(ROOT/'web/index.html',media_type='text/html')
    return app
