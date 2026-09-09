"""本地单用户HTTP入口。默认不调用付费API。"""
from __future__ import annotations
import json
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from fastapi import FastAPI,Request
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
from app.exporter import collect,as_json,as_xlsx
from app.remote_access import PreviewAccess
from contracts.runtime_rules import EvidenceScope,validate_candidate,validate_schema

class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class ProjectInput(Input):name:str=Field(min_length=1,max_length=150)
class UploadInput(Input):name:str=Field(min_length=1,max_length=240);size:int=Field(ge=0)
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
        return db.create_project(data.name.strip())
    @app.get('/api/projects/{pid}')
    def project_get(pid:str):return db.one('SELECT * FROM projects WHERE id=?',(pid,))
    @app.get('/api/projects/{pid}/manifest')
    def manifest(pid:str):
        db.one('SELECT * FROM projects WHERE id=?',(pid,))
        return {'uploads':db.all('SELECT * FROM uploads WHERE project_id=? ORDER BY created_at',(pid,)),
                'documents':db.all('SELECT id,project_id,name,size,sha256,created_at FROM documents WHERE project_id=? ORDER BY created_at',(pid,))}
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
    def run_create(pid:str):return runner.create(pid)
    @app.get('/api/projects/{pid}/analysis-runs')
    def runs_get(pid:str):return [runner.get(x['id']) for x in db.all('SELECT id FROM runs WHERE project_id=? ORDER BY created_at DESC',(pid,))]
    @app.get('/api/analysis-runs/{rid}')
    def run_get(rid:str):return runner.get(rid)
    @app.post('/api/analysis-runs/{rid}/{action}')
    def run_control(rid:str,action:Literal['pause','resume','cancel']):return runner.control(rid,action)
    @app.get('/api/analysis-runs/{rid}/cost')
    def run_cost(rid:str):return db.cost(runner.get(rid)['project_id'])
    @app.get('/api/analysis-runs/{rid}/records')
    def records_get(rid:str,kind:Literal['MATERIAL','INSPECTION','CONFLICT','MISSING']|None=None):
        runner.get(rid)
        rows=db.all('SELECT * FROM records WHERE run_id=?'+(' AND kind=?' if kind else '')+' ORDER BY kind,id',(rid,kind) if kind else (rid,))
        return [{'record':json.loads(r['envelope']),'review_version':r['review_version']} for r in rows]
    @app.get('/api/analysis-runs/{rid}/evidence/{eid}')
    def evidence_get(rid:str,eid:str):
        runner.get(rid)
        row=db.one('SELECT payload,status,error FROM evidence WHERE id=? AND run_id=?',(rid+':'+eid,rid))
        e=json.loads(row['payload']);doc=db.one('SELECT name FROM documents WHERE id=?',(e['document_id'],))
        return {'evidence':e,'file_name':doc['name'],'status':row['status'],'error':row['error']}
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
        return {'record':after,'review_version':data.expected_version+1}
    @app.get('/api/analysis-runs/{rid}/exports/{fmt}')
    def export(rid:str,fmt:Literal['json','xlsx'],reviewed_only:bool=False):
        run=runner.get(rid)
        if run['status'] in ('RUNNING','QUEUED'):raise DomainError('请先暂停或等待分析结束，再导出一致快照',409)
        data=collect(db,run,reviewed_only)
        raw=as_json(data) if fmt=='json' else as_xlsx(data)
        media='application/json' if fmt=='json' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return Response(raw,media_type=media,headers={'Content-Disposition':f'attachment; filename="cirp-{rid}.{fmt}"'})
    app.mount('/assets',StaticFiles(directory=ROOT/'web'),name='assets')
    @app.get('/')
    def index():return FileResponse(ROOT/'web/index.html',media_type='text/html')
    return app
