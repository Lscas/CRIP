"""本地单用户HTTP入口。默认不调用付费API。"""
from __future__ import annotations
import json
import hashlib
import hmac
import logging
import secrets
import threading
from functools import lru_cache
from decimal import Decimal
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from fastapi import FastAPI,Request,Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel,ConfigDict,Field,StrictInt
from filelock import FileLock,Timeout as LockTimeout
from app.settings import Settings,ROOT,VERSION
from app.local_credentials import LocalCredentialError
from app.model_configuration import (clear_active_configuration, configured_settings,
                                     public_configuration, save_active_configuration)
from app.db import Database,DomainError,dumps,now,uid
from app.uploads import Uploads
from app.gateway import Gateway,InvalidModelOutput
from app.runner import Runner
from app.exporter import (collect,as_json,as_xlsx,reviewer_record_display,
                          reviewer_record_is_visible)
from app.remote_access import PreviewAccess
from app.workflows import (WORKFLOW_SUMMARY_SQL_PATHS,apply_workflow_classification,
                           build_workflow_index,normalize_workflow_classification,
                           projected_workflow_summary,workflow_classification_view)
from app.connectors import ExternalConnectors
from app.email_attachments import (import_email_attachment,import_email_attachments,
                                   list_email_attachments)
from app.questions import (ProjectQuestions,requires_email_attachment_relation_index,
                           requires_cross_workflow_relation_index,
                           requires_email_action_index,
                           requires_email_header_index,
                           requires_email_thread_index,
                           requires_email_workflow_relation_index,
                           requires_rfi_content_index,
                           requires_rfi_spec_section_index,
                           requires_submittal_field_index,
                           requires_workflow_date_index,
                           requires_workflow_clause_reference_index,
                           requires_workflow_document_relation_index,
                           requires_workflow_drawing_reference_index,
                           requires_workflow_email_relation_index,
                           requires_workflow_inventory,
                           requires_workflow_action_index,
                           requires_workflow_field_index,
                           requires_workflow_impact_index,
                           requires_workflow_metadata_index,
                           requires_workflow_party_index,
                           requires_workflow_status_index,requires_workflow_subject_index)
from app.project_qa_v2 import ProjectQAV2
from app.evidence_loop import ProjectEvidenceLoop
from app.page_selector import (COMPLETE_SELECTOR_VERSION as SELECTOR_VERSION,
                               LAYOUT_BOUND_SELECTOR_VERSION,select_pages)
from app.reference_results import ReferenceResultStore
from app.reference_cases import ReferenceCaseStore
from app.reference_evaluations import ReferenceEvaluationStore
from app.reference_evaluation_jobs import ReferenceEvaluationJobStore
from app.reference_readiness import ReferenceReadiness
from app.reference_scorecards import ReferenceScorecardStore
from contracts.runtime_rules import EvidenceScope,validate_candidate,validate_schema

class Input(BaseModel):model_config=ConfigDict(extra='forbid')
class ProjectInput(Input):
    name:str=Field(min_length=1,max_length=150)
class RunInput(Input):
    local_workers:Literal[1,2,4]=2
    analysis_mode:Literal['LEGACY_ANALYSIS','REFERENCE_QA']='LEGACY_ANALYSIS'
class UploadInput(Input):name:str=Field(min_length=1,max_length=240);size:int=Field(ge=0)
class ConnectorInput(Input):
    access_token:str=Field(min_length=1,max_length=16384,repr=False)
    project_id:str=Field(min_length=1,max_length=2048)
    hub_id:str=Field(default='',max_length=2048)
    company_id:str=Field(default='',max_length=64)
    folder_id:str=Field(default='',max_length=2048)
    remember:bool=True
class ConnectorImportInput(Input):
    provider:Literal['autodesk','procore']
    remote_id:str=Field(min_length=1,max_length=2048)
class EmailAttachmentSelection(Input):
    attachment_index:int=Field(ge=0,le=10000)
    expected_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
class EmailAttachmentImportInput(EmailAttachmentSelection):
    document_id:str=Field(min_length=1,max_length=128)
class EmailAttachmentBatchInput(Input):
    document_id:str=Field(min_length=1,max_length=128)
    attachments:list[EmailAttachmentSelection]=Field(min_length=1,max_length=100)
class VerificationInput(Input):
    expected_version:int=Field(ge=0)
    semantic:bool=False
class WorkflowClassificationInput(Input):
    expected_version:int=Field(ge=0)
    workflow_type:Literal['DETECTED','OTHER','RFI','SUBMITTAL']
    identifier:str|None=Field(default=None,max_length=64)
    role:str|None=Field(default=None,max_length=32)
    status:str|None=Field(default=None,max_length=80)
    note:str=Field(default='',max_length=1000)
class QuestionInput(Input):
    run_id:str|None=Field(default=None,min_length=1,max_length=128)
    question:str=Field(min_length=3,max_length=1000)

class ModelConfigurationInput(Input):
    provider:Literal['mock','deepseek','gemini','openai','custom']
    api_base_url:str=Field(default='',max_length=2048)
    model:str=Field(default='',max_length=160)
    api_key:str=Field(default='',max_length=256,repr=False)
    vision_enabled:bool=False
    structured_output_mode:Literal['json_object','json_schema']='json_object'
    reasoning_effort:Literal['none','low','high','max']='none'
    remember:bool=True
    approved:bool=False

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
class ReferenceResultReviewInput(Input):
    action:Literal['ACCEPTED','REJECTED']
    expected_version:int=Field(ge=0)
    note:str=Field(default='',max_length=2000)
class ReferenceEvaluationInput(Input):
    run_id:str=Field(min_length=1,max_length=128)
    name:str=Field(min_length=1,max_length=150)
    questions:list[str]=Field(min_length=1,max_length=50)
    profile_id:Literal['FLASH_NONE','FLASH_LOW','PRO']|None=None
    selector_version:Literal['literal-page-selector-9']|None=None
class ReferenceEvaluationCloneInput(Input):
    profile_id:Literal['FLASH_NONE','FLASH_LOW','PRO']|None=None
class ReferenceQuestionInput(QuestionInput):
    profile_id:Literal['FLASH_NONE','FLASH_LOW','PRO']|None=None
    selector_version:Literal['literal-page-selector-9']|None=None
    preview_proof:'ReferencePreviewProofInput|None'=None
class ReferenceQuestionPreviewInput(QuestionInput):
    profile_id:Literal['FLASH_NONE','FLASH_LOW','PRO']|None=None
    selector_version:Literal['literal-page-selector-9']|None=None
class ReferencePreviewProofInput(Input):
    proof_version:Literal['reference-preview-proof-1']
    project_id:str=Field(min_length=1,max_length=128)
    run_id:str=Field(min_length=1,max_length=128)
    snapshot_id:str=Field(min_length=1,max_length=128)
    normalized_question_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    selector_version:Literal['literal-page-selector-9']
    context_policy:Literal['COMPLETE_SELECTED_SCOPE_WITH_LAYOUT_BINDING_V1']
    profile_version:Literal['reference-text-profile-1']
    profile_id:Literal['FLASH_NONE','FLASH_LOW','PRO']
    route_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    selection_id:str=Field(min_length=1,max_length=128)
    initial_evidence_manifest_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    prompt_contract_hash:str=Field(pattern=r'^[0-9a-f]{64}$')
ReferenceQuestionInput.model_rebuild()
class ReferenceEvaluationExecuteInput(Input):
    preview_proof:ReferencePreviewProofInput|None=None
    replay_only:bool=False
class ReferenceEvaluationJobInput(Input):
    confirmed:Literal[True]
class ReferenceEvaluationAdjudicationInput(Input):
    verdict:Literal['FULLY_USABLE','PARTIAL','UNUSABLE']
    unsupported_claim:bool=False
    expected_version:int=Field(ge=0)
    note:str=Field(default='',max_length=2000)
class ReferenceCaseCreateInput(Input):
    run_id:str=Field(min_length=1,max_length=128)
    question:str=Field(min_length=3,max_length=1000)
    result_id:str|None=Field(default=None,min_length=1,max_length=128)
    evaluation_id:str|None=Field(default=None,min_length=1,max_length=128)
    question_id:str|None=Field(default=None,min_length=1,max_length=128)
    attachments:list[str]=Field(default_factory=list,max_length=50)
    supplemental_question:str|None=Field(default=None,max_length=1000)
    note:str|None=Field(default=None,max_length=4000)
class ReferenceCaseUpdateInput(Input):
    expected_version:StrictInt=Field(ge=0)
    status:Literal['OPEN','NEEDS_INFORMATION','IN_REVIEW','RESOLVED']|None=None
    assignee:str|None=Field(default=None,max_length=240)
    note:str|None=Field(default=None,max_length=4000)
    resolution:str|None=Field(default=None,max_length=4000)
    attachments:list[str]=Field(default_factory=list,max_length=50)
    supplemental_question:str|None=Field(default=None,max_length=1000)
class ReferenceCaseFollowupInput(Input):
    expected_version:StrictInt=Field(ge=0)
    run_id:str=Field(min_length=1,max_length=128)
    result_id:str=Field(min_length=1,max_length=128)
    supplemental_document_ids:list[str]=Field(min_length=1,max_length=50)
    note:str=Field(default='',max_length=4000)


def create_app(settings:Settings|None=None)->FastAPI:
    s=settings or Settings.from_env();s.data_dir.mkdir(parents=True,exist_ok=True)
    instance_id=secrets.token_hex(12);provider_change_lock=threading.Lock();restarting=[False]
    db=Database(s.data_dir/'cirp.sqlite3');uploads=Uploads(db,s);gateway=Gateway(s,db);runner=Runner(db,s,uploads,gateway)
    questions=ProjectQuestions(db,gateway);questions_v2=ProjectQAV2(db,gateway,uploads)
    questions_v3=ProjectEvidenceLoop(db,gateway,uploads)
    reference_results=ReferenceResultStore(db,uploads)
    reference_cases=ReferenceCaseStore(db,uploads)
    reference_evaluations=ReferenceEvaluationStore(db,reference_results)
    reference_readiness=ReferenceReadiness(
        db,reference_evaluations,lambda:gateway.s,select_pages)
    reference_scorecards=ReferenceScorecardStore(db,reference_evaluations)
    reference_evaluation_jobs=ReferenceEvaluationJobStore(
        db,reference_evaluations,lambda:gateway.s,
        lambda evaluation_id,item_id:_execute_reference_evaluation_item(
            evaluation_id,item_id,managed=True))
    runner.reference_evaluation_jobs=reference_evaluation_jobs
    connectors=ExternalConnectors(s,uploads)
    @asynccontextmanager
    async def lifespan(app):
        lock=FileLock(str(s.data_dir/'server.lock'))
        try:lock.acquire(timeout=0)
        except LockTimeout:raise RuntimeError('该数据目录已有CIRP进程；原型只运行一个服务实例。')
        try:
            if s.start_worker:runner.start()
            yield
        finally:
            runner.close();gateway.close();connectors.close();lock.release()
    app=FastAPI(title='CIRP 开发原型',version=VERSION,lifespan=lifespan,
                docs_url=None if s.remote_enabled else '/docs',
                redoc_url=None if s.remote_enabled else '/redoc',
                openapi_url=None if s.remote_enabled else '/openapi.json')
    access=PreviewAccess(s)
    app.state.db=db;app.state.runner=runner;app.state.gateway=gateway;app.state.uploads=uploads
    app.state.connectors=connectors;app.state.settings=s;app.state.reference_results=reference_results
    app.state.reference_cases=reference_cases
    app.state.reference_evaluations=reference_evaluations
    app.state.reference_readiness=reference_readiness
    app.state.reference_scorecards=reference_scorecards
    app.state.reference_evaluation_jobs=reference_evaluation_jobs
    def _require_reference_layout_enabled()->None:
        # This is a startup release gate, deliberately independent of mutable
        # gateway/provider settings.
        if not s.reference_layout_enabled:
            raise DomainError('Reference layout v9 is not enabled for this CIRP startup.',409)
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
    @app.exception_handler(RequestValidationError)
    async def validation_handler(request,exc):
        # FastAPI's default includes the rejected input, which could echo a malformed access token.
        errors=[{key:value for key,value in item.items() if key not in {'input','ctx'}} for item in exc.errors()]
        return JSONResponse({'detail':errors},status_code=422)

    @app.get('/api/health/version')
    def health():return {'app_version':VERSION,'spec_version':VERSION,'schema_version':'0.2.0','provider':s.provider}
    @app.get('/api/settings')
    def settings_view():return {**s.public(),'service_instance':instance_id}
    @app.get('/api/model-settings')
    def model_settings_view():
        return {**public_configuration(s),
                'restart_supported':callable(getattr(app.state,'request_model_restart',None)),
                'service_instance':instance_id}
    @app.post('/api/model-settings',status_code=202)
    def model_settings_update(data:ModelConfigurationInput):
        if s.remote_enabled or s.render_free_preview:
            raise DomainError('Model settings can be changed only from the local single-user application.',403)
        if data.provider!='mock' and not data.approved:
            raise DomainError('Confirm the endpoint, document transfer, and possible provider charges.')
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is already restarting with a new model configuration.',409)
            restart=getattr(app.state,'request_model_restart',None)
            if not callable(restart):
                raise DomainError('Restart CIRP with the standard local launcher before changing model settings.',409)
            if db.one("SELECT id FROM runs WHERE status IN ('QUEUED','RUNNING') LIMIT 1",required=False):
                raise DomainError('Pause or finish the active analysis before changing model settings.',409)
            if db.one("SELECT id FROM verification_jobs WHERE state IN ('QUEUED','RUNNING') LIMIT 1",required=False):
                raise DomainError('Finish the active semantic verification before changing model settings.',409)
            if reference_evaluation_jobs.active() is not None:
                raise DomainError('Stop or finish the managed Reference evaluation job first.',409)
            if db.one('SELECT id FROM model_calls WHERE actual_units IS NULL LIMIT 1',required=False):
                raise DomainError('Reconcile unresolved API calls before changing model settings.',409)
            try:
                candidate=configured_settings(
                    s,provider=data.provider,api_base_url=data.api_base_url,model=data.model,
                    api_key=data.api_key,vision_enabled=data.vision_enabled,
                    structured_output_mode=data.structured_output_mode,
                    reasoning_effort=data.reasoning_effort)
                if data.remember and candidate.provider!='mock':save_active_configuration(candidate)
                else:clear_active_configuration(s.data_dir)
            except (ValueError,LocalCredentialError) as exc:
                raise DomainError(str(exc)) from exc
            except OSError as exc:
                raise DomainError('The model settings could not be saved locally.') from exc
            restarting[0]=True
            restart(candidate)
        return {'status':'RESTARTING','provider':candidate.provider,
                'model':candidate.cheap_model if candidate.provider!='mock' else 'mock-no-network'}
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
        upload_rows=db.all('''SELECT u.*,s.source_document_id,s.source_kind,s.source_detail,
                              d.name AS source_document_name FROM uploads u
                              LEFT JOIN upload_sources s ON s.upload_id=u.id
                              LEFT JOIN documents d ON d.id=s.source_document_id
                              WHERE u.project_id=? ORDER BY u.created_at''',(pid,))
        for row in upload_rows:row['source_detail']=json.loads(row['source_detail']) if row['source_detail'] else None
        return {'uploads':upload_rows,
                'documents':db.all('SELECT id,project_id,name,size,sha256,created_at FROM documents WHERE project_id=? ORDER BY created_at',(pid,))}
    @app.get('/api/connectors')
    def connector_status():return connectors.status()
    @app.post('/api/connectors/{provider}')
    def connector_configure(provider:Literal['autodesk','procore'],data:ConnectorInput):
        return connectors.configure(provider,data.access_token,data.model_dump(exclude={'access_token','remember'}),data.remember)
    @app.delete('/api/connectors/{provider}')
    def connector_disconnect(provider:Literal['autodesk','procore']):return connectors.disconnect(provider)
    @app.get('/api/connectors/{provider}/items')
    def connector_items(provider:Literal['autodesk','procore'],folder_id:str|None=Query(None,max_length=2048)):
        return connectors.items(provider,folder_id)
    @app.post('/api/projects/{pid}/connector-imports',status_code=201)
    def connector_import(pid:str,data:ConnectorImportInput):
        db.one('SELECT id FROM projects WHERE id=?',(pid,))
        return connectors.import_file(data.provider,data.remote_id,pid)
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
    @app.get('/api/documents/{did}/email-attachments')
    def email_attachment_list(did:str):
        doc=db.one('SELECT * FROM documents WHERE id=?',(did,))
        return {'document_id':did,'file_name':doc['name'],
                'attachments':list_email_attachments(uploads.object_path(doc),doc['name'])}
    @app.post('/api/projects/{pid}/email-attachment-imports',status_code=201)
    def email_attachment_import(pid:str,data:EmailAttachmentImportInput):
        doc=db.one('SELECT * FROM documents WHERE id=? AND project_id=?',(data.document_id,pid))
        return import_email_attachment(uploads,doc,data.attachment_index,data.expected_sha256)
    @app.post('/api/projects/{pid}/email-attachment-imports/batch',status_code=201)
    def email_attachment_batch_import(pid:str,data:EmailAttachmentBatchInput):
        doc=db.one('SELECT * FROM documents WHERE id=? AND project_id=?',(data.document_id,pid))
        imported=import_email_attachments(
            uploads,doc,[(item.attachment_index,item.expected_sha256) for item in data.attachments])
        return {'count':len(imported),'imports':imported}
    @app.post('/api/projects/{pid}/analysis-runs',status_code=202)
    def run_create(pid:str,data:RunInput|None=None):
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
            request=data or RunInput()
            return runner.create(pid,request.local_workers,request.analysis_mode)
    @app.get('/api/projects/{pid}/analysis-runs')
    def runs_get(pid:str):return [runner.get(x['id']) for x in db.all('SELECT id FROM runs WHERE project_id=? ORDER BY created_at DESC',(pid,))]
    def _analysis_mode(row:dict)->str:
        try:capabilities=json.loads(row['capabilities'])
        except (TypeError,ValueError,json.JSONDecodeError):return 'LEGACY_ANALYSIS'
        return capabilities.get('analysis_mode','LEGACY_ANALYSIS')
    def project_knowledge(pid:str,analysis_mode:str='LEGACY_ANALYSIS',
                          include_active_run:bool=False)->dict:
        db.one('SELECT id FROM projects WHERE id=?',(pid,))
        documents=[dict(row) for row in db.all(
            'SELECT id,sha256 FROM documents WHERE project_id=? ORDER BY id',(pid,))]
        snapshot='SN-'+hashlib.sha256(dumps(documents).encode()).hexdigest()[:32]
        terminal=next((row for row in db.all("""SELECT id,capabilities FROM runs
                       WHERE project_id=? AND status IN ('PARTIAL','COMPLETED')
                       ORDER BY created_at DESC,rowid DESC""",(pid,))
                       if _analysis_mode(row)==analysis_mode),None)
        active_row=next((row for row in db.all("""SELECT id,capabilities FROM runs
                         WHERE project_id=? AND status IN ('QUEUED','RUNNING')
                         ORDER BY created_at DESC,rowid DESC""",(pid,))
                         if _analysis_mode(row)==analysis_mode),None)
        active=active_row is not None
        if not terminal:
            value={'available':False,'run_id':None,'current':False,'active_update':active,
                   'created_at':None,'status':None,'document_count':0}
        else:
            run=runner.get(terminal['id'])
            value={'available':True,'run_id':run['id'],'current':run['snapshot_id']==snapshot,
                   'active_update':active,'created_at':run['created_at'],'status':run['status'],
                   'document_count':len(run['document_ids'])}
        if include_active_run:
            value['active_run_id']=active_row['id'] if active_row else None
            if s.reference_layout_enabled:
                value['snapshot_id']=run['snapshot_id'] if terminal else None
        return value
    @app.get('/api/projects/{pid}/knowledge')
    def project_knowledge_get(pid:str):return project_knowledge(pid)
    @app.get('/api/projects/{pid}/reference-knowledge')
    def reference_knowledge_get(pid:str):
        return project_knowledge(pid,'REFERENCE_QA',True)
    @app.post('/api/projects/{pid}/questions')
    def question_ask(pid:str,data:QuestionInput):
        run_id=data.run_id
        if run_id is None:
            knowledge=project_knowledge(pid)
            if not knowledge['available']:
                raise DomainError('Analyze this project once before asking a question.',409)
            run_id=knowledge['run_id']
        run=runner.get(run_id)
        if run['project_id']!=pid:raise DomainError('The selected analysis run does not belong to this project.',404)
        question=data.question.strip()
        if len(question)<3:raise DomainError('Enter a question with at least three non-space characters.')
        workflow_index=(terminal_workflow_index(run['id'])
                        if run['status'] in ('PARTIAL','COMPLETED')
                        and (requires_workflow_status_index(question)
                             or requires_workflow_inventory(question)
                             or requires_workflow_subject_index(question)
                             or requires_rfi_content_index(question)
                             or requires_rfi_spec_section_index(question)
                             or requires_submittal_field_index(question)
                             or requires_workflow_date_index(question)
                             or requires_workflow_clause_reference_index(question)
                             or requires_workflow_action_index(question)
                             or requires_workflow_field_index(question)
                             or requires_workflow_impact_index(question)
                             or requires_workflow_metadata_index(question)
                             or requires_workflow_party_index(question)
                             or requires_workflow_document_relation_index(question)
                             or requires_workflow_drawing_reference_index(question)
                             or requires_email_attachment_relation_index(question)
                             or requires_email_thread_index(question)
                             or requires_email_workflow_relation_index(question)
                             or requires_workflow_email_relation_index(question)
                             or requires_cross_workflow_relation_index(question)
                             or requires_email_action_index(question)
                             or requires_email_header_index(question)) else None)
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
            reference_evaluation_jobs.require_no_active()
            return questions.ask(run,question,workflow_index)
    @app.post('/api/projects/{pid}/questions-v2/preview')
    def question_v2_preview(pid:str,data:QuestionInput):
        run_id=data.run_id
        if run_id is None:
            knowledge=project_knowledge(pid)
            if not knowledge['available']:
                raise DomainError('Analyze this project once before previewing QA V2.',409)
            run_id=knowledge['run_id']
        run=runner.get(run_id)
        if run['project_id']!=pid:
            raise DomainError('The selected analysis run does not belong to this project.',404)
        return questions_v2.preview(run,data.question.strip())
    @app.post('/api/projects/{pid}/questions-v2')
    def question_v2_ask(pid:str,data:QuestionInput):
        run_id=data.run_id
        if run_id is None:
            knowledge=project_knowledge(pid)
            if not knowledge['available']:
                raise DomainError('Analyze this project once before asking QA V2.',409)
            run_id=knowledge['run_id']
        run=runner.get(run_id)
        if run['project_id']!=pid:
            raise DomainError('The selected analysis run does not belong to this project.',404)
        question=data.question.strip()
        if len(question)<3:raise DomainError('Enter a question with at least three non-space characters.')
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
            reference_evaluation_jobs.require_no_active()
            return questions_v2.ask(run,question)
    @app.post('/api/projects/{pid}/questions-v3/preview')
    def question_v3_preview(pid:str,data:ReferenceQuestionPreviewInput):
        run_id=data.run_id
        if run_id is None:
            knowledge=project_knowledge(pid,'REFERENCE_QA')
            if not knowledge['available']:
                raise DomainError('Analyze this project once before previewing QA V3.',409)
            run_id=knowledge['run_id']
        run=runner.get(run_id)
        if run['project_id']!=pid:
            raise DomainError('The selected analysis run does not belong to this project.',404)
        question=data.question.strip()
        if len(question)<3:raise DomainError('Enter a question with at least three non-space characters.')
        if data.selector_version is None:
            return questions_v3.preview(run,question)
        _require_reference_layout_enabled()
        if data.profile_id is None:
            raise DomainError('Selector v9 preview requires a canonical named text profile.',409)
        from app.reference_text_profiles import profile as reference_text_profile
        return questions_v3.preview(
            run,question,selector_version=LAYOUT_BOUND_SELECTOR_VERSION,
            route=reference_text_profile(data.profile_id))
    @app.post('/api/projects/{pid}/page-selections/preview')
    def page_selection_preview(pid:str,data:QuestionInput):
        run_id=data.run_id
        if run_id is None:
            knowledge=project_knowledge(pid,'REFERENCE_QA')
            if not knowledge['available']:
                raise DomainError('Prepare this project once before selecting pages.',409)
            run_id=knowledge['run_id']
        run=runner.get(run_id)
        if run['project_id']!=pid:
            raise DomainError('The selected analysis run does not belong to this project.',404)
        question=data.question.strip()
        if len(question)<3:raise DomainError('Enter a question with at least three non-space characters.')
        return select_pages(db,run,question,selector_version=SELECTOR_VERSION).public()
    @app.post('/api/projects/{pid}/questions-v3')
    def question_v3_ask(pid:str,data:ReferenceQuestionInput):
        if data.selector_version is not None and data.run_id is None:
            raise DomainError('Selector v9 Ask requires an explicit analysis run.',409)
        run_id=data.run_id
        if run_id is None:
            knowledge=project_knowledge(pid,'REFERENCE_QA')
            if not knowledge['available']:
                raise DomainError('Analyze this project once before asking QA V3.',409)
            run_id=knowledge['run_id']
        run=runner.get(run_id)
        if run['project_id']!=pid:
            raise DomainError('The selected analysis run does not belong to this project.',404)
        question=data.question.strip()
        if len(question)<3:raise DomainError('Enter a question with at least three non-space characters.')
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
            reference_evaluation_jobs.require_no_active()
            if data.selector_version is not None:
                _require_reference_layout_enabled()
                if data.profile_id is None or data.preview_proof is None:
                    raise DomainError('Selector v9 Ask requires a named profile and preview proof.',409)
            route=(reference_evaluations.profile_for_id(data.profile_id,gateway.s)
                   if data.profile_id else None)
            result,saved=_execute_reference_question(
                run,question,
                LAYOUT_BOUND_SELECTOR_VERSION if data.selector_version is not None else SELECTOR_VERSION,
                route=route,
                preview_proof=(data.preview_proof.model_dump() if data.preview_proof else None))
            return {**result,'result_id':saved['result_id'],'review':saved['review']}
    def _execute_reference_question(
            run:dict,question:str,selector_version:str=SELECTOR_VERSION,route:dict|None=None,
            preview_proof:dict|None=None)->tuple[dict,dict]:
        if preview_proof is not None:
            result=questions_v3.ask(
                run,question,selector_version,route=route,preview_proof=preview_proof)
        else:
            result=(questions_v3.ask(run,question,selector_version,route=route)
                    if route is not None else questions_v3.ask(run,question,selector_version))
        current=gateway.s
        receipts=result.get('execution_receipts',[])
        model=(receipts[-1]['model'] if result.get('execution_profile') and receipts else
               ('mock-no-network' if current.provider=='mock' else
                current.vision_model if result.get('answer_basis')==
                'MODEL_QA_V3_MULTIMODAL_EVIDENCE_LOOP' else current.cheap_model))
        saved=reference_results.save(run,question,result,current.provider,model)
        return result,saved
    @app.get('/api/projects/{pid}/reference-results')
    def reference_result_list(pid:str,run_id:str|None=None,
                              offset:int=Query(default=0,ge=0),
                              limit:int=Query(default=20,ge=1,le=50)):
        return reference_results.list(pid,run_id,offset,limit)
    @app.get('/api/projects/{pid}/reference-results/export.json')
    def reference_result_export(
            pid:str,run_id:str|None=None,
            review_status:Literal['PENDING','ACCEPTED','REJECTED','NOT_APPLICABLE']|None=None):
        value=reference_results.export(pid,run_id,review_status);raw=dumps(value)
        suffix=f'-{run_id}' if run_id else ''
        return Response(raw,media_type='application/json',headers={
            'Content-Disposition':f'attachment; filename="cirp-reference-{pid}{suffix}.json"'})
    @app.get('/api/projects/{pid}/reference-results/compare')
    def reference_result_compare(pid:str,baseline_run_id:str,candidate_run_id:str):
        return reference_results.compare(pid,baseline_run_id,candidate_run_id)
    @app.get('/api/reference-results/{result_id}')
    def reference_result_get(result_id:str):return reference_results.get(result_id)
    @app.get('/api/reference-results/{result_id}/history')
    def reference_result_history(result_id:str):return reference_results.history(result_id)
    @app.get('/api/reference-results/{result_id}/projection-review')
    def reference_projection_review(result_id:str,request:Request):
        if request.query_params:
            raise DomainError('Projection source review does not accept client source parameters.',422)
        return JSONResponse(reference_results.get_projection_review_view(result_id),
                            headers={'Cache-Control':'no-store'})
    @app.get('/api/reference-results/{result_id}/projection-review/sources/{ordinal}/image')
    def reference_projection_review_image(result_id:str,ordinal:int,request:Request):
        if request.query_params:
            raise DomainError('Projection source review does not accept client source parameters.',422)
        return Response(reference_results.get_projection_review_image(result_id,ordinal),
                        media_type='image/png',headers={'Cache-Control':'no-store'})
    @app.post('/api/reference-results/{result_id}/review')
    def reference_result_review(result_id:str,data:ReferenceResultReviewInput):
        return reference_results.review(
            result_id,data.action,data.expected_version,data.note)
    @app.get('/api/projects/{pid}/reference-cases')
    def reference_case_list(pid:str,
                            status:Literal['OPEN','NEEDS_INFORMATION','IN_REVIEW','RESOLVED']|None=None,
                            assignee:str|None=Query(default=None,max_length=240),
                            run_id:str|None=Query(default=None,max_length=128),
                            offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100)):
        return reference_cases.list(pid,status=status,assignee=assignee,run_id=run_id,offset=offset,limit=limit)
    @app.post('/api/projects/{pid}/reference-cases',status_code=201)
    def reference_case_create(pid:str,data:ReferenceCaseCreateInput):
        return reference_cases.create(
            pid,data.run_id,data.question,result_id=data.result_id,evaluation_id=data.evaluation_id,
            question_id=data.question_id,attachments=data.attachments,
            supplemental_question=data.supplemental_question,note=data.note,actor='local-user')
    @app.get('/api/reference-cases/{case_id}')
    def reference_case_get(case_id:str):return reference_cases.get(case_id)
    @app.post('/api/reference-cases/{case_id}/update')
    def reference_case_update(case_id:str,data:ReferenceCaseUpdateInput):
        return reference_cases.update(
            case_id,data.expected_version,status=data.status,assignee=data.assignee,note=data.note,
            resolution=data.resolution,attachments=data.attachments,
            supplemental_question=data.supplemental_question,actor='local-user')
    @app.post('/api/reference-cases/{case_id}/follow-up-results')
    def reference_case_followup_result(case_id:str,data:ReferenceCaseFollowupInput):
        return reference_cases.link_followup(
            case_id,data.expected_version,data.run_id,data.result_id,
            data.supplemental_document_ids,data.note,actor='local-user')
    @app.post('/api/projects/{pid}/reference-evaluations',status_code=201)
    def reference_evaluation_create(pid:str,data:ReferenceEvaluationInput):
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
            selector_version=data.selector_version or SELECTOR_VERSION
            if data.selector_version is not None:
                _require_reference_layout_enabled()
                if data.profile_id is None:
                    raise DomainError('Reference layout evaluation requires a canonical named text profile.',409)
            return reference_evaluations.create(
                pid,data.run_id,data.name,data.questions,
                reference_evaluations.profile_for_id(data.profile_id,gateway.s),selector_version)
    @app.get('/api/projects/{pid}/reference-evaluations')
    def reference_evaluation_list(pid:str,offset:int=Query(default=0,ge=0),
                                  limit:int=Query(default=10,ge=1,le=20)):
        return reference_evaluations.list(pid,offset,limit)
    @app.get('/api/projects/{pid}/reference-evaluations/compare')
    def reference_evaluation_compare(
            pid:str,baseline_evaluation_id:str,candidate_evaluation_id:str):
        return reference_evaluations.compare(
            pid,baseline_evaluation_id,candidate_evaluation_id)
    @app.get('/api/projects/{pid}/reference-evaluation-jobs')
    def reference_evaluation_job_list(
            pid:str,limit:int=Query(default=20,ge=1,le=20)):
        return reference_evaluation_jobs.list(pid,limit)
    @app.get('/api/reference-evaluations/{evaluation_id}')
    def reference_evaluation_get(evaluation_id:str):
        return reference_evaluations.get(evaluation_id)
    @app.get('/api/reference-evaluations/{evaluation_id}/readiness')
    def reference_evaluation_readiness(evaluation_id:str):
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
            return reference_readiness.build(evaluation_id)
    @app.get('/api/reference-evaluations/{evaluation_id}/scorecard')
    def reference_evaluation_scorecard(evaluation_id:str):
        return reference_scorecards.scorecard(evaluation_id)
    @app.post('/api/reference-evaluations/{evaluation_id}/items/{item_id}/adjudication')
    def reference_evaluation_adjudicate(
            evaluation_id:str,item_id:str,data:ReferenceEvaluationAdjudicationInput):
        return reference_scorecards.adjudicate(
            evaluation_id,item_id,data.verdict,data.unsupported_claim,
            data.expected_version,data.note)
    @app.get('/api/reference-evaluations/{evaluation_id}/items/{item_id}/adjudication-history')
    def reference_evaluation_adjudication_history(
            evaluation_id:str,item_id:str,offset:int=Query(default=0,ge=0),
            limit:int=Query(default=50,ge=1,le=100)):
        return reference_scorecards.history(evaluation_id,item_id,offset,limit)
    @app.post('/api/reference-evaluations/{evaluation_id}/clone',status_code=201)
    def reference_evaluation_clone(evaluation_id:str,data:ReferenceEvaluationCloneInput|None=None):
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
            if reference_evaluations.get(evaluation_id)['selector_version']==LAYOUT_BOUND_SELECTOR_VERSION:
                _require_reference_layout_enabled()
            return reference_evaluations.clone_for_profile(
                evaluation_id,reference_evaluations.profile_for_id(
                    data.profile_id if data else None,gateway.s))
    @app.post('/api/reference-evaluations/{evaluation_id}/jobs',status_code=202)
    def reference_evaluation_job_create(
            evaluation_id:str,data:ReferenceEvaluationJobInput):
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
            if reference_evaluations.get(evaluation_id)['selector_version']==LAYOUT_BOUND_SELECTOR_VERSION:
                _require_reference_layout_enabled()
            return reference_evaluation_jobs.create(evaluation_id,data.confirmed)
    @app.get('/api/reference-evaluation-jobs/{job_id}')
    def reference_evaluation_job_get(job_id:str):
        return reference_evaluation_jobs.get(job_id)
    @app.post('/api/reference-evaluation-jobs/{job_id}/stop')
    def reference_evaluation_job_stop(job_id:str):
        return reference_evaluation_jobs.stop(job_id)
    @app.post('/api/reference-evaluations/{evaluation_id}/items/{item_id}/preview')
    def reference_evaluation_preview(evaluation_id:str,item_id:str):
        context=reference_evaluations.item_context(evaluation_id,item_id)
        if context['selector_version']==LAYOUT_BOUND_SELECTOR_VERSION:
            _require_reference_layout_enabled()
            return {
                'evaluation_id':evaluation_id,'item_id':item_id,
                'question':context['item']['question'],
                **questions_v3.preview(
                    context['run'],context['item']['question'],
                    selector_version=LAYOUT_BOUND_SELECTOR_VERSION,route=context['profile']),
            }
        return {
            'evaluation_id':evaluation_id,'item_id':item_id,
            'question':context['item']['question'],
            'page_selection':select_pages(
                db,context['run'],context['item']['question'],
                selector_version=context['selector_version']).public(),
        }
    def _execute_reference_evaluation_item(
            evaluation_id:str,item_id:str,managed:bool=False,
            preview_proof:dict|None=None,replay_only:bool=False)->dict:
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
            context=reference_evaluations.item_context(evaluation_id,item_id)
            layout_bound=context['selector_version']==LAYOUT_BOUND_SELECTOR_VERSION
            if preview_proof is not None and not layout_bound:
                raise DomainError('Preview proof is only supported for selector v9 evaluations.',409)
            terminal=context['saved_result'] is not None or context['saved_failure'] is not None
            if replay_only and not terminal:
                raise DomainError('REPLAY_ONLY requires a saved terminal evaluation item.',409)
            # A pending v9 proof goes straight to ask(), where _prepare_initial
            # runs exactly once before reservation. Terminal replay has no ask,
            # so it locally rebuilds the proof before authenticating saved data.
            if terminal and preview_proof is not None:
                current=questions_v3.preview(
                    context['run'],context['item']['question'],
                    selector_version=LAYOUT_BOUND_SELECTOR_VERSION,route=context['profile'])
                if preview_proof!=current['preview_proof']:
                    raise DomainError('PREVIEW_STALE: Preview this evaluation item again before executing.',409)
            if context['saved_result'] is not None:
                authenticated=reference_evaluations.authenticate_item_result(
                    context['evaluation'],context['item'],context['item']['result_id'])
                return {'evaluation':reference_evaluations.get(evaluation_id),
                        'result':authenticated,'replayed':True,
                        'execution_telemetry':{
                            'scope':'CURRENT_EXECUTE','model_call_count':0,
                            'decision_count':0,'cached_decision_count':0}}
            if context['saved_failure'] is not None:
                saved_execution=reference_evaluations.authenticate_item_failure(
                    context['evaluation'],context['item'],context['profile'],context['saved_failure'])
                return {'evaluation':reference_evaluations.get(evaluation_id),
                        'result':None,'failure':context['saved_failure'],'replayed':True,
                        **({'failure_execution':saved_execution} if saved_execution is not None else {}),
                        'execution_telemetry':{
                            'scope':'CURRENT_EXECUTE','model_call_count':0,
                            'decision_count':0,'cached_decision_count':0}}
            if layout_bound:
                _require_reference_layout_enabled()
            if not managed:reference_evaluation_jobs.require_no_active()
            route=reference_evaluations.require_profile(evaluation_id,gateway.s)
            try:
                runtime_result,saved=_execute_reference_question(
                    context['run'],context['item']['question'],
                    context['selector_version'],route=route,preview_proof=preview_proof)
            except InvalidModelOutput as exc:
                if db.unresolved_calls(context['evaluation']['project_id']):raise
                evaluation,failure=reference_evaluations.fail(
                    evaluation_id,item_id,'MODEL_OUTPUT_REJECTED',
                    exc.execution_receipt,exc.failure_execution)
                response={'evaluation':evaluation,'result':None,'failure':failure,'replayed':False}
                if exc.failure_execution is not None:
                    receipts=exc.failure_execution['execution_receipts']
                    response.update({
                        'failure_execution':exc.failure_execution,
                        'execution_telemetry':{
                            'scope':'CURRENT_EXECUTE',
                            'model_call_count':sum(not receipt.get('cached',False) for receipt in receipts),
                            'decision_count':len(receipts),
                            'cached_decision_count':sum(bool(receipt.get('cached',False)) for receipt in receipts)}})
                return response
            evaluation=reference_evaluations.attach(
                evaluation_id,item_id,saved['result_id'])
            receipts=runtime_result.get('execution_receipts',[])
            return {'evaluation':evaluation,'result':saved,'replayed':False,
                    'execution_telemetry':{
                        'scope':'CURRENT_EXECUTE',
                        'model_call_count':runtime_result.get('model_call_count',0),
                        'decision_count':len(receipts),
                        'cached_decision_count':sum(bool(item.get('cached')) for item in receipts)}}
    @app.post('/api/reference-evaluations/{evaluation_id}/items/{item_id}/execute')
    def reference_evaluation_execute(
            evaluation_id:str,item_id:str,data:ReferenceEvaluationExecuteInput|None=None):
        return _execute_reference_evaluation_item(
            evaluation_id,item_id,
            preview_proof=(data.preview_proof.model_dump() if data and data.preview_proof else None),
            replay_only=bool(data and data.replay_only))
    @app.get('/api/analysis-runs/{rid}')
    def run_get(rid:str):return runner.get(rid)
    @app.post('/api/analysis-runs/{rid}/{action}')
    def run_control(rid:str,action:Literal['pause','resume','cancel']):
        if action!='resume':return runner.control(rid,action)
        with provider_change_lock:
            if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
            return runner.control(rid,action)
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
    def build_run_workflow_index(rid:str,run:dict):
        rows=db.all(f'''SELECT r.document_id,d.name,
                       json_extract(r.summary,{WORKFLOW_SUMMARY_SQL_PATHS}) AS workflow_values
                       FROM document_results r JOIN documents d ON d.id=r.document_id
                       WHERE r.run_id=? ORDER BY d.name''',(rid,))
        overrides={row['document_id']:row for row in db.all(
            '''SELECT document_id,workflow_type,identifier,role,status,version,note,updated_at
               FROM workflow_classification_overrides WHERE run_id=?''',(rid,))}
        for row in rows:
            summary=projected_workflow_summary(row.pop('workflow_values'));override=overrides.get(row['document_id'])
            row['summary']=apply_workflow_classification(summary,override)
            row['classification_source']='MANUAL' if override and override['workflow_type']!='DETECTED' else 'DETECTED'
        links=db.all('''SELECT s.source_kind,s.source_document_id,u.document_id,s.source_detail
                        FROM upload_sources s JOIN uploads u ON u.id=s.upload_id
                        JOIN document_results child ON child.run_id=? AND child.document_id=u.document_id
                        JOIN document_results parent ON parent.run_id=? AND parent.document_id=s.source_document_id
                        WHERE u.project_id=? AND s.source_kind='EMAIL_ATTACHMENT'
                        ORDER BY s.source_document_id,u.document_id,s.upload_id''',(rid,rid,run['project_id']))
        for link in links:
            detail=json.loads(link.pop('source_detail'));link.update({
                'attachment_index':detail.get('attachment_index'),
                'content_type':detail.get('content_type')})
        return build_workflow_index(rows,links)
    # ponytail: eight terminal runs bound memory; add shared persistence only for multi-process deployment.
    @lru_cache(maxsize=8)
    def terminal_workflow_index(rid:str):
        return build_run_workflow_index(rid,runner.get(rid))
    def classification_payload(rid:str,did:str):
        run=runner.get(rid)
        row=db.one('''SELECT r.summary,d.name FROM document_results r
                      JOIN documents d ON d.id=r.document_id
                      WHERE r.run_id=? AND r.document_id=? AND d.project_id=?''',
                   (rid,did,run['project_id']))
        override=db.one('''SELECT workflow_type,identifier,role,status,version,note,updated_at
                           FROM workflow_classification_overrides
                           WHERE run_id=? AND document_id=?''',(rid,did),required=False)
        return {'run_id':rid,'document_id':did,'file_name':row['name'],
                **workflow_classification_view(json.loads(row['summary']),override)}
    @app.get('/api/analysis-runs/{rid}/documents/{did}/workflow-classification')
    def workflow_classification_get(rid:str,did:str):
        """Read detected/effective workflow metadata without parsing or model work."""
        return classification_payload(rid,did)
    @app.post('/api/analysis-runs/{rid}/documents/{did}/workflow-classification')
    def workflow_classification_set(rid:str,did:str,data:WorkflowClassificationInput):
        normalized=normalize_workflow_classification(
            data.workflow_type,data.identifier,data.role,data.status)
        timestamp=now()
        with db.connect(True) as connection:
            run=connection.execute('SELECT project_id,status FROM runs WHERE id=?',(rid,)).fetchone()
            if not run:raise DomainError('Analysis run was not found',404)
            if run['status'] not in ('PARTIAL','COMPLETED'):
                raise DomainError('Workflow classification can be corrected only after the run finishes',409)
            result=connection.execute('''SELECT 1 FROM document_results r JOIN documents d ON d.id=r.document_id
                                         WHERE r.run_id=? AND r.document_id=? AND d.project_id=?''',
                                      (rid,did,run['project_id'])).fetchone()
            if not result:raise DomainError('Document does not belong to this analysis run',404)
            current=connection.execute('''SELECT workflow_type,identifier,role,status,version,note,updated_at
                                          FROM workflow_classification_overrides
                                          WHERE run_id=? AND document_id=?''',(rid,did)).fetchone()
            version=current['version'] if current else 0
            if version!=data.expected_version:
                raise DomainError('Workflow classification changed; refresh before saving',409)
            saved={**normalized,'version':version+1,'note':data.note,'updated_at':timestamp}
            connection.execute('''INSERT INTO workflow_classification_overrides
                (run_id,document_id,project_id,workflow_type,identifier,role,status,version,note,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id,document_id) DO UPDATE SET
                 workflow_type=excluded.workflow_type,identifier=excluded.identifier,
                 role=excluded.role,status=excluded.status,version=excluded.version,
                 note=excluded.note,updated_at=excluded.updated_at''',
                (rid,did,run['project_id'],saved['workflow_type'],saved['identifier'],saved['role'],
                 saved['status'],saved['version'],saved['note'],saved['updated_at']))
            before=dict(current) if current else None
            connection.execute('''INSERT INTO workflow_classification_events
                (id,run_id,document_id,actor,before_json,after_json,note,created_at)
                VALUES(?,?,?,?,?,?,?,?)''',
                (uid('WCLASS'),rid,did,'local-user',dumps(before),dumps(saved),data.note,timestamp))
        terminal_workflow_index.cache_clear()
        return classification_payload(rid,did)
    @app.get('/api/analysis-runs/{rid}/workflows')
    def run_workflows(rid:str,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
        """Read-only deterministic workflow relationships; never calls a model."""
        run=runner.get(rid)
        result=(terminal_workflow_index(rid) if run['status'] in ('PARTIAL','COMPLETED')
                else build_run_workflow_index(rid,run))
        items=result['items'];page=items[offset:offset+limit]
        return {'items':page,'summary':result['summary'],
                'pagination':{'offset':offset,'limit':limit,'total':len(items),
                              'next_offset':offset+len(page) if offset+len(page)<len(items) else None}}
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
            with provider_change_lock:
                if restarting[0]:raise DomainError('CIRP is restarting with a new model configuration.',409)
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
