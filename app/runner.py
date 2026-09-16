"""持久化任务清单 + 单后台线程。暂停/恢复不会重跑已完成片段。"""
from __future__ import annotations
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from app.db import Database,DomainError,BudgetError,dumps,now,uid,paid_task_family
from app.settings import Settings,ROOT,live_provider
from app.security import environment_without_secrets
from app.uploads import Uploads
from app.gateway import Gateway,ProviderPaused,InvalidModelOutput
from app.parsers import PARSER_VERSION
from app.visual_pipeline import VISION_RENDER_VERSION,render_visual_png
from app.assemble import envelopes,missing,key,material_group_key
from contracts.runtime_rules import validate_schema
from app.verification import VerificationService

ACTIVE=('QUEUED','RUNNING')
EXTRACTION_BATCH_SIZE=4
EXTRACTION_BATCH_BYTES=8800

def _adjacent_evidence(left: dict, right: dict) -> bool:
    if left.get('document_id')!=right.get('document_id'):return False
    a=left.get('locator') or {};b=right.get('locator') or {}
    ap=a.get('page_number');bp=b.get('page_number')
    if type(ap) is int and type(bp) is int:return 0<=bp-ap<=1
    ae=a.get('text_line_end');bs=b.get('text_line_start')
    if type(ae) is int and type(bs) is int:return 0<=bs-ae<=1
    am=re.search(r'(\d+)$',str(a.get('native_element_id') or ''))
    bm=re.search(r'(\d+)$',str(b.get('native_element_id') or ''))
    return bool(am and bm and 0<=int(bm.group(1))-int(am.group(1))<=1)

def adjacent_extraction_batches(items: list[tuple[dict | None,dict]],
                                forced_single: set[str] | None = None) -> list[list[tuple[dict | None,dict]]]:
    """Group only physically adjacent evidence under a small deterministic byte ceiling."""
    forced_single=forced_single or set();batches=[];current=[];size=0
    for item in items:
        evidence=item[1];item_size=len(evidence.get('raw_text','').encode('utf-8'))
        if evidence.get('evidence_id') in forced_single:
            if current:batches.append(current);current=[];size=0
            batches.append([item]);continue
        fits=(current and current[-1][1].get('evidence_id') not in forced_single
              and len(current)<EXTRACTION_BATCH_SIZE and size+item_size<=EXTRACTION_BATCH_BYTES
              and _adjacent_evidence(current[-1][1],evidence))
        if current and not fits:
            batches.append(current);current=[];size=0
        current.append(item);size+=item_size
    if current:batches.append(current)
    return batches

class Runner:
    def __init__(self,db:Database,settings:Settings,uploads:Uploads,gateway:Gateway):
        self.db=db;self.s=settings;self.uploads=uploads;self.gateway=gateway
        self.stop_event=threading.Event();self.thread=None
        self.verifier=VerificationService(db,settings,gateway)
        self.parse_dir=settings.data_dir/'parsed';self.parse_dir.mkdir(exist_ok=True)

    def create(self,project_id,local_workers=2):
        if local_workers not in (1,2,4):raise DomainError('本地工作进程数必须为1、2或4')
        self.db.one('SELECT * FROM projects WHERE id=?',(project_id,))
        if self.s.provider!='mock' and not live_provider(self.s.provider):raise DomainError('Provider未支持',409)
        if self.s.provider!='mock' and self.s.live_errors():raise DomainError('；'.join(self.s.live_errors()),409)
        with self.db.connect(True) as c:
            if c.execute("SELECT id FROM runs WHERE status IN ('QUEUED','RUNNING')").fetchone():raise DomainError('当前仅允许一个活跃项目分析',409)
            if c.execute("SELECT id FROM verification_jobs WHERE state IN ('QUEUED','RUNNING')").fetchone():raise DomainError('当前有核验任务，结束后再分析',409)
            if c.execute('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',(project_id,)).fetchone():
                raise DomainError('项目有待对账API请求，核对账单前不能新建或恢复付费分析',409)
            docs=[dict(d) for d in c.execute('SELECT id,sha256 FROM documents WHERE project_id=? ORDER BY id',(project_id,))]
            if not docs:raise DomainError('没有已完成上传的文件',409)
            rid=uid('RUN'); snapshot='SN-'+hashlib.sha256(dumps(docs).encode()).hexdigest()[:32]
            timestamp=time.time()
            capabilities=self.s.public()['capabilities'];capabilities['local_workers']=local_workers
            c.execute('''INSERT INTO runs(id,project_id,provider,snapshot_id,document_ids,status,stage,created_at,
                         started_epoch,deadline_epoch,capabilities) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                      (rid,project_id,self.s.provider,snapshot,dumps([d['id'] for d in docs]),'QUEUED','等待处理',now(),timestamp,
                       timestamp+self.s.deadline_seconds,dumps(capabilities)))
        return self.get(rid)

    def get(self,rid):
        r=self.db.one('SELECT * FROM runs WHERE id=?',(rid,))
        for k in ('document_ids','coverage','capabilities'):r[k]=json.loads(r[k])
        r['performance']=self.db.performance(rid)
        r['progress']=self.progress(r)
        return r

    def progress(self,run,at_epoch=None):
        """Return a conservative UI estimate; it is not a completion guarantee."""
        current=time.time() if at_epoch is None else at_epoch
        coverage=run.get('coverage') or {};status=run['status'];stage=run['stage']
        if status in ('PARTIAL','COMPLETED'):
            percent=100
        elif stage=='解析文件':
            total=max(1,len(run['document_ids']))
            completed_ids={row['document_id'] for row in self.db.all('SELECT document_id FROM document_results WHERE run_id=?',(run['id'],))}
            completed=len(completed_ids);partial=0.0
            for did in run['document_ids']:
                if did in completed_ids:continue
                path=self.parse_dir/(run['id']+'-'+did+'.json')
                try:
                    payload=json.loads(path.read_text(encoding='utf-8'))
                    count=payload.get('page_count');pages=payload.get('pages',[])
                    if type(count) is int and count>0:
                        partial+=min(1,sum(p.get('status')!='NOT_PROCESSED' for p in pages if isinstance(p,dict))/count)
                    elif payload.get('status')=='SUCCESS':partial+=1
                except (OSError,ValueError,TypeError):
                    pass
            percent=round(20*min(1,(completed+partial)/total))
        elif stage=='本机OCR完成，处理图纸视觉页':
            total=coverage.get('visual_pages_total',0);done=coverage.get('visual_pages_completed',0)
            percent=20+round(15*(done/total if total else 1))
        elif stage=='一次读取，联合提取材料与检查要求':
            total=coverage.get('fragments_total',0)
            done=coverage.get('fragments_extracted',0)+coverage.get('fragments_need_review',0)
            percent=35+round(45*(done/total if total else 0))
        elif stage=='生成可审核记录与设计差异':percent=85
        elif stage=='逐字段核验原文支持性':percent=92
        else:percent=0
        percent=max(0,min(100,percent))
        remaining=None;finish=None
        if status in ACTIVE and 0<percent<100:
            elapsed=max(0,current-run['started_epoch'])
            remaining=min(max(0,run['deadline_epoch']-current),elapsed*(100-percent)/percent)
            if elapsed<2:remaining=None
            elif remaining is not None:finish=current+remaining
        return {'percent':percent,'estimated_finish_epoch':finish,
                'remaining_seconds':remaining,'estimate':bool(finish),'method':'stage_weighted_elapsed'}

    @contextmanager
    def timed(self,rid,metric):
        started=time.perf_counter()
        try:yield
        finally:self.db.record_metric(rid,metric,(time.perf_counter()-started)*1000)

    def control(self,rid,action):
        with self.db.connect(True) as c:
            r=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            if not r:raise DomainError('任务不存在',404)
            if action=='resume':
                resumable_failure=(r['status']=='FAILED'
                    and r['stage']=='生成可审核记录与设计差异'
                    and not c.execute("SELECT id FROM evidence WHERE run_id=? AND status='PENDING' LIMIT 1",(rid,)).fetchone())
                if r['status'] not in ('PAUSED','PAUSED_PROVIDER','PAUSED_BUDGET','INTERRUPTED') and not resumable_failure:
                    raise DomainError('当前任务不可恢复；已结束任务需新建分析',409)
                if r['provider'] != self.s.provider:raise DomainError('原运行Provider与当前服务不一致，请新建分析',409)
                if time.time()>=r['deadline_epoch']:raise DomainError('已到原运行24小时时限，需明确新建运行；项目预算不重置',409)
                if c.execute("SELECT id FROM runs WHERE status IN ('QUEUED','RUNNING')").fetchone():raise DomainError('已有活跃任务',409)
                if c.execute("SELECT id FROM verification_jobs WHERE state IN ('QUEUED','RUNNING')").fetchone():raise DomainError('当前有核验任务，请结束后再恢复分析',409)
                if c.execute('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',(r['project_id'],)).fetchone():raise DomainError('有待对账API请求，先核对账单；不会自动再次付费',409)
                generations=self.db.authorize_run_resume_generations(c,dict(r),context_id=uid('RESUME'))
                message=('修复本地生成错误后重新生成审核记录；已完成的模型调用不会重发'
                         if resumable_failure else
                         ('恢复未完成任务；已为人工对账且仍待处理的任务创建'
                         f'{len(generations)}个显式付费恢复代次，每个任务族累计最多3次'
                         if generations else '恢复未完成任务；没有创建新的付费恢复代次'))
                c.execute('UPDATE runs SET status=?,stop_requested=0,message=? WHERE id=?',('QUEUED',message,rid))
            elif action in ('pause','cancel'):
                if r['status'] not in ACTIVE:raise DomainError('当前没有活跃任务',409)
                status='CANCELLED' if action=='cancel' else 'PAUSED'
                c.execute('UPDATE runs SET stop_requested=1,status=?,message=? WHERE id=?',(status,'已请求停止；在途请求仍需结算',rid))
            else:raise DomainError('未知操作')
        return self.get(rid)

    def start(self):
        # A previous process may have died after reservation but before durable
        # settlement. Its remaining calls are uncertain, not safely retryable.
        self.db.execute("""UPDATE model_calls SET state='UNKNOWN',error=?,updated_at=?
                         WHERE state='RESERVED' AND actual_units IS NULL""",
                        (dumps({'kind':'CLIENT_ERROR','class':'PROCESS_INTERRUPTED'}),now()))
        self.db.execute("UPDATE runs SET status='INTERRUPTED',message='服务曾中断；已完成片段保留，付费请求需对账' WHERE status='RUNNING'")
        self.db.execute("UPDATE verification_jobs SET state='INTERRUPTED',message='服务中断；不自动重复付费' WHERE state='RUNNING'")
        self.thread=threading.Thread(target=self.loop,name='cirp-worker',daemon=True);self.thread.start()

    def close(self):
        self.stop_event.set()
        self.db.execute("UPDATE runs SET stop_requested=1 WHERE status='RUNNING'")
        self.db.execute("""UPDATE verification_jobs SET state='INTERRUPTED',message=?,updated_at=?
                         WHERE state IN ('QUEUED','RUNNING')""",
                        ('服务已停止；未发送的新请求保持未执行',now()))
        if self.thread:self.thread.join(timeout=self.s.parser_timeout+15)

    def loop(self):
        while not self.stop_event.wait(.25):
            row=self.db.one("SELECT id FROM runs WHERE status='QUEUED' ORDER BY created_at LIMIT 1",required=False)
            if row:self.process(row['id'])
            else:
                job=self.db.one("SELECT id FROM verification_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1",required=False)
                if job:self.verifier.process_job(job['id'])

    def checkpoint(self,rid):
        r=self.db.one('SELECT stop_requested,status,deadline_epoch FROM runs WHERE id=?',(rid,))
        if self.stop_event.is_set() or r['stop_requested'] or r['status']!='RUNNING':return False
        if time.time()>=r['deadline_epoch']:
            self.db.execute('UPDATE runs SET status=?,message=? WHERE id=?',('PAUSED_DEADLINE','到达24小时目标，停止新增任务；未完成范围保留',rid));return False
        return True

    def process(self,rid):
        with self.db.connect(True) as c:
            row=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            if not row or row['status']!='QUEUED':return
            if row['provider'] != self.s.provider:
                c.execute('UPDATE runs SET status=?,message=? WHERE id=?',
                          ('PAUSED_PROVIDER','原运行Provider与当前服务不一致；未调用API，请新建分析',rid))
                return
            if c.execute('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',(row['project_id'],)).fetchone():
                c.execute('UPDATE runs SET status=?,message=? WHERE id=?',
                          ('PAUSED_PROVIDER','项目有待对账API请求；未调用API，请先核对账单',rid))
                return
            c.execute("UPDATE runs SET status='RUNNING',stage='解析文件' WHERE id=?",(rid,))
        run=self.get(rid);run['model']=self.s.cheap_model
        try:
            with self.timed(rid,'stage.parse'):
                self.parse_documents(run)
            if not self.checkpoint(rid):return
            self.db.execute('UPDATE runs SET stage=? WHERE id=?',('本机OCR完成，处理图纸视觉页',rid))
            with self.timed(rid,'stage.vision'):
                self.process_visual_tasks(run)
            if not self.checkpoint(rid):return
            self.db.execute('UPDATE runs SET stage=? WHERE id=?',('一次读取，联合提取材料与检查要求',rid))
            with self.timed(rid,'stage.extract'):
                todo=self.db.all("SELECT rowid AS sequence,* FROM evidence WHERE run_id=? AND status='PENDING' ORDER BY document_id,sequence",(rid,))
                pending=[(row,json.loads(row['payload'])) for row in todo]
                families={paid_task_family(row['task_key']) for row in self.db.all(
                    'SELECT task_key FROM model_calls WHERE run_id=?',(rid,))}
                forced_single={family for family in families if family.startswith('EV-')}
                for batch in adjacent_extraction_batches(pending,forced_single):
                    if not self.checkpoint(rid):break
                    evidences=[item[1] for item in batch]
                    try:
                        result=(self.gateway.extract(run,evidences[0]) if len(evidences)==1 else
                                self.gateway.extract_many(run,evidences))
                        with self.db.connect(True) as connection:
                            for index,(row,evidence) in enumerate(batch):
                                data=result.data if index==0 else {
                                    'disposition':'NO_REQUIREMENTS','requirements':[],
                                    'reason':'Processed with adjacent evidence batch '+evidences[0]['evidence_id']+'.'}
                                extraction={'data':data,'request_id':result.request_id,'cached':result.cached,
                                            'batch_primary_evidence_id':evidences[0]['evidence_id']}
                                connection.execute('UPDATE evidence SET extraction=?,status=?,error=? WHERE id=?',
                                    (dumps(extraction),'EXTRACTED' if result.data['disposition']=='CANDIDATES' else 'NEEDS_REVIEW','',row['id']))
                    except InvalidModelOutput as exc:
                        with self.db.connect(True) as connection:
                            for row,_ in batch:
                                connection.execute('UPDATE evidence SET status=?,error=? WHERE id=?',
                                                   ('NEEDS_REVIEW',str(exc),row['id']))
                    self.update_coverage(rid)
            if not self.checkpoint(rid):return
            self.db.execute('UPDATE runs SET stage=? WHERE id=?',('生成可审核记录与设计差异',rid))
            with self.timed(rid,'stage.assemble'):
                self.publish(run,seed_verifications=False)
            self.db.execute('UPDATE runs SET stage=? WHERE id=?',('逐字段核验原文支持性',rid))
            with self.timed(rid,'stage.verify'):
                items=self.db.all('SELECT id FROM records WHERE run_id=? ORDER BY id',(rid,))
                if items and self.checkpoint(rid):
                    self.verifier.refresh_many([item['id'] for item in items],allow_model=run['provider']!='mock')
            self.db.execute('UPDATE runs SET status=?,stage=?,message=? WHERE id=?',
                ('PARTIAL','本轮基线任务已结束',
                 '已完成可用文字、本机OCR、已启用页面视觉与可用CAD对象处理；PDF原始矢量审计不等于材料净量，原生DWG取决于本机合法转换器。复杂选项与跨专业关联仍待完善。'
                 +('模拟模式仅验证流程，不代表真实施工分析。' if run['provider']=='mock' else '真实API结果尚需人工核验。'),rid))
        except BudgetError as exc:
            self.db.execute('UPDATE runs SET status=?,message=? WHERE id=?',('PAUSED_BUDGET',str(exc),rid));self.publish(run)
        except ProviderPaused as exc:
            self.db.execute('UPDATE runs SET status=?,message=? WHERE id=?',('PAUSED_PROVIDER',str(exc),rid));self.publish(run)
        except Exception as exc:
            import logging;logging.getLogger('cirp').exception('运行失败 %s',rid)
            self.db.execute('UPDATE runs SET status=?,message=? WHERE id=?',('FAILED','内部处理错误：'+type(exc).__name__,rid))
        finally:
            r=self.get(rid)
            if r['status']=='RUNNING' and (r['stop_requested'] or self.stop_event.is_set()):
                self.db.execute("UPDATE runs SET status='PAUSED',message='服务停止，未完成任务保留' WHERE id=?",(rid,))
            self.update_coverage(rid)

    def parse_documents(self,run):
        rid=run['id']
        pending=[did for did in run['document_ids']
                 if not self.db.one('SELECT 1 FROM document_results WHERE run_id=? AND document_id=?',(rid,did),False)]
        workers=run.get('capabilities',{}).get('local_workers',2)
        for offset in range(0,len(pending),workers):
            if not self.checkpoint(rid):return
            batch=pending[offset:offset+workers]
            if len(batch)==1:
                self.parse_one(run,batch[0],workers);self.update_coverage(rid);continue
            # ponytail: one small batch bounds memory; add a persistent local queue only after measured demand.
            with ThreadPoolExecutor(max_workers=len(batch),thread_name_prefix='cirp-parser') as pool:
                futures=[pool.submit(self.parse_one,run,did) for did in batch]
                for future in futures:
                    future.result();self.update_coverage(rid)

    def parse_one(self,run,did,page_workers=1):
        doc=self.db.one('SELECT * FROM documents WHERE id=?',(did,))
        output=self.parse_dir/(run['id']+'-'+did+'.json')
        # 解析进程不继承任何常见凭证环境变量；任何文字不会被执行。
        env=environment_without_secrets()
        try:
            subprocess.run([sys.executable,'-m','app.parser_worker',str(self.uploads.object_path(doc)),doc['name'],
                            str(page_workers),str(output)],
                           cwd=ROOT,env=env,timeout=self.s.parser_timeout,check=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            result=json.loads(output.read_text(encoding='utf-8'))
        except subprocess.TimeoutExpired:
            try:
                result=json.loads(output.read_text(encoding='utf-8'))
                if not isinstance(result,dict) or not all(isinstance(result.get(k),list) for k in ('fragments','pages','warnings')):
                    raise ValueError('解析进度文件无效')
                result['status']='PARTIAL'
                page_count=result.get('page_count')
                if type(page_count) is int and page_count>=0:
                    completed={p.get('page') for p in result['pages'] if isinstance(p,dict) and type(p.get('page')) is int}
                    result['pages'].extend({'page':number,'status':'NOT_PROCESSED'}
                                           for number in range(1,page_count+1) if number not in completed)
                    result['warnings'].append(
                        f'解析超过{self.s.parser_timeout}秒；已保留{len(completed)}/{page_count}页，未完成页已标记NOT_PROCESSED。')
                else:
                    result['warnings'].append(
                        f'解析超过{self.s.parser_timeout}秒；已保留超时前完成的页面，剩余内容未处理。')
            except (OSError,ValueError,json.JSONDecodeError):
                result={'status':'FAILED','fragments':[],'pages':[],
                        'warnings':[f'解析超过{self.s.parser_timeout}秒，且没有可保留的页面进度。'],
                        'parser_version':PARSER_VERSION}
        except Exception as exc:
            result={'status':'FAILED','fragments':[],'pages':[],
                    'warnings':['解析进程失败或超时：'+type(exc).__name__],'parser_version':PARSER_VERSION}
        with self.db.connect(True) as c:
            evidence_by_fragment={}
            for i,f in enumerate(result.pop('fragments',[])):
                eid='EV-'+key(run['snapshot_id'],did,i)
                evidence_by_fragment[i]=eid
                ev={'evidence_id':eid,'tenant_id':'local','project_id':run['project_id'],'input_snapshot_id':run['snapshot_id'],
                    'document_id':did,'component_id':f'{did}:page:{f["locator"].get("page_number") or 1}',
                    'file_sha256':doc['sha256'],'internal_revision_date':f['internal_revision_date'],
                    'revision_label':f['revision_label'],'locator':f['locator'],'raw_text':f['text'],
                    'image_crop_uri':(f'/api/analysis-runs/{run["id"]}/documents/{did}/pages/{f["locator"].get("page_number")}/image'
                                      if f['method']=='OCR' and f['locator'].get('page_number') else None),
                    'extraction_method':f['method'],'confidence':f.get('confidence'),
                    'text_map':f.get('text_map',[]),'parser_version':result.get('parser_version','text-baseline-1')}
                validate_schema('evidence',ev)
                # 同快照重跑保持EV逻辑标识；数据库主键按运行隔离。
                storage_id=run['id']+':'+eid
                c.execute('INSERT OR IGNORE INTO evidence(id,run_id,project_id,document_id,payload) VALUES(?,?,?,?,?)',
                          (storage_id,run['id'],run['project_id'],did,dumps(ev)))
            for takeoff in result.get('takeoffs',[]):
                source_index=takeoff.pop('source_fragment_index',None)
                evidence_id=evidence_by_fragment.get(source_index)
                takeoff['evidence_ids']=[evidence_id] if evidence_id else []
                calibration=takeoff.get('calibration')
                if calibration is not None:
                    calibration['evidence_ids']=[evidence_id] if evidence_id else []
            c.execute('INSERT INTO document_results VALUES(?,?,?,?)',(run['id'],did,result['status'],dumps(result)))

    @staticmethod
    def _vision_text(data: dict) -> str:
        parts=[f"Visual page type: {data['page_type']}"]
        if data.get('sheet_id'):parts.append('Visible sheet identifier: '+data['sheet_id'])
        if data.get('important_visible_text'):parts.append('Important visible text:\n'+data['important_visible_text'])
        parts.extend('Visual observation: '+value for value in data.get('observations',[]))
        parts.extend('Explicit quantity text: '+value for value in data.get('explicit_quantity_texts',[]))
        if data.get('scale_text'):parts.append('Visible scale text: '+data['scale_text'])
        parts.extend('Visual limitation: '+value for value in data.get('limitations',[]))
        return '\n'.join(parts)

    def process_visual_tasks(self,run):
        rows=self.db.all('''SELECT r.document_id,r.status,r.summary,d.name,d.sha256,d.size,d.object_key
                            FROM document_results r JOIN documents d ON d.id=r.document_id
                            WHERE r.run_id=? ORDER BY r.document_id''',(run['id'],))
        for row in rows:
            summary=json.loads(row['summary']);tasks=summary.get('visual_tasks',[])
            if not tasks:continue
            if run['provider']=='mock' or not self.s.vision_enabled:
                for task in tasks:
                    if task.get('status')=='PENDING':task['status']='BLOCKED_NOT_ENABLED'
                if '页面视觉任务未启用；本机OCR结果仍保留。' not in summary['warnings']:
                    summary['warnings'].append('页面视觉任务未启用；本机OCR结果仍保留。')
                self.db.execute('UPDATE document_results SET summary=?,status=? WHERE run_id=? AND document_id=?',
                                (dumps(summary),'PARTIAL',run['id'],row['document_id']))
                continue
            for task in tasks:
                if task.get('status') not in ('PENDING','PAUSED_PROVIDER'):continue
                if not self.checkpoint(run['id']):return
                page=int(task['page']);region_id=task.get('region_id')
                eid=('EV-'+key(run['snapshot_id'],row['document_id'],'VISION',page,region_id)
                     if region_id else 'EV-'+key(run['snapshot_id'],row['document_id'],'VISION',page))
                storage_id=run['id']+':'+eid
                if self.db.one('SELECT id FROM evidence WHERE id=?',(storage_id,),False):
                    task['status']='VISION_EXTRACTED'
                    self.db.execute('UPDATE document_results SET summary=?,status=? WHERE run_id=? AND document_id=?',
                                    (dumps(summary),'PARTIAL',run['id'],row['document_id']))
                    self.update_coverage(run['id'])
                    continue
                try:
                    crop=task.get('bbox')
                    image,width,height,coordinate_system=render_visual_png(
                        self.uploads.object_path({'object_key':row['object_key']}),row['name'],page,crop)
                    task_key=f'{row["document_id"]}:{page}'+(f':{region_id}' if region_id else '')
                    result=self.gateway.vision(run,task_key,image,{
                        'document_id':row['document_id'],'page':page,'coordinate_system':coordinate_system,
                        'width':width,'height':height,'page_type_hint':task.get('page_type'),
                        'region_id':region_id,'region_type':task.get('region_type'),'crop_bbox':crop},
                        billing_generation=task.get('billing_generation',0))
                    data=result.data
                    locator_bbox=crop or [0,0,width,height]
                    crop_query=('?x0={0:g}&y0={1:g}&x1={2:g}&y1={3:g}'.format(*crop) if crop else '')
                    ev={'evidence_id':eid,'tenant_id':'local','project_id':run['project_id'],
                        'input_snapshot_id':run['snapshot_id'],'document_id':row['document_id'],
                        'component_id':f'{row["document_id"]}:page:{page}:vision:{region_id or "overview"}','file_sha256':row['sha256'],
                        'internal_revision_date':None,'revision_label':None,
                        'locator':{'page_number':page,'sheet':data.get('sheet_id'),'section':None,'paragraph':None,
                                   'bbox':locator_bbox,'coordinate_system':coordinate_system,
                                   'text_line_start':None,'text_line_end':None,'native_element_id':region_id},
                        'raw_text':self._vision_text(data),
                        # Retained model observation, never parser-extracted source prose.
                        'content_basis':'MODEL_VISION_OUTPUT',
                        'image_crop_uri':f'/api/analysis-runs/{run["id"]}/documents/{row["document_id"]}/pages/{page}/image{crop_query}',
                        'extraction_method':'VISION','confidence':None,'text_map':[],
                        'parser_version':VISION_RENDER_VERSION}
                    validate_schema('evidence',ev)
                    self.db.execute('INSERT OR IGNORE INTO evidence(id,run_id,project_id,document_id,payload) VALUES(?,?,?,?,?)',
                                    (storage_id,run['id'],run['project_id'],row['document_id'],dumps(ev)))
                    task.update(status='VISION_EXTRACTED',cached=result.cached,request_id=result.request_id)
                except InvalidModelOutput as exc:
                    task.update(status='FAILED_CONTRACT',error=str(exc))
                    summary['warnings'].append(f'PDF/图片第{page}页视觉结果未通过契约；不自动重复收费。')
                except (OSError,ValueError) as exc:
                    task.update(status='FAILED_RENDER',error=type(exc).__name__)
                    summary['warnings'].append(f'PDF/图片第{page}页无法生成有界视觉派生图；其余页面继续处理。')
                except ProviderPaused:
                    task['status']='PAUSED_PROVIDER'
                    self.db.execute('UPDATE document_results SET summary=?,status=? WHERE run_id=? AND document_id=?',
                                    (dumps(summary),'PARTIAL',run['id'],row['document_id']))
                    raise
                self.db.execute('UPDATE document_results SET summary=?,status=? WHERE run_id=? AND document_id=?',
                                (dumps(summary),'PARTIAL',run['id'],row['document_id']))
                self.update_coverage(run['id'])

    def publish(self,run,seed_verifications=True):
        rows=self.db.all('SELECT * FROM evidence WHERE run_id=? ORDER BY id',(run['id'],));evs={};extracted=[];missing_items=[]
        for row in rows:
            e=json.loads(row['payload']);evs[e['evidence_id']]=e
            if row['extraction']:extracted.append((e,json.loads(row['extraction'])['data']))
            elif row['error']:missing_items.append(missing(e['document_id'],row['error'],'NOT_LOCATED',[e['evidence_id']],e['evidence_id']))
        for row in self.db.all('SELECT * FROM document_results WHERE run_id=?',(run['id'],)):
            s=json.loads(row['summary'])
            if row['status']!='SUCCESS':
                missing_items.append(missing(row['document_id'],'；'.join(s.get('warnings',[])) or '文件内容未完整解析','PARSE_FAILED',target=row['document_id']))
        generated=list(envelopes(run,evs,extracted,missing_items));refresh_ids=[]
        with self.db.connect(True) as c:
            def generated_baseline(row):
                """Recover the latest system-generated candidate behind human edits."""
                events=c.execute('''SELECT action,before_json,after_json FROM review_events
                                    WHERE record_id=? ORDER BY created_at,id''',(row['id'],)).fetchall()
                for event in reversed(events):
                    if event['action']=='INVALIDATED_BY_NEW_EVIDENCE':
                        return json.loads(event['after_json'])['candidate']
                if events:return json.loads(events[0]['before_json'])['candidate']
                return json.loads(row['envelope'])['candidate']

            for record in generated:
                record_id=record['meta']['record_id'];logical=record['candidate']['candidate_key']
                existing=c.execute('''SELECT id,envelope,review_version,logical_key FROM records
                                      WHERE id=? OR (run_id=? AND kind=? AND logical_key=?)''',
                                   (record_id,run['id'],record['kind'],logical)).fetchone()
                if not existing and record['kind']=='MATERIAL' and logical.startswith('MG-'):
                    # v0.2.6 pre-fix builds used the first evidence-derived C-
                    # key for a merged tagged material.  Match a single legacy
                    # record by its stable business group and update it in
                    # place, retaining its primary id and all review/verification
                    # foreign-key history.
                    legacy=[]
                    for row in c.execute("SELECT id,envelope,review_version,logical_key FROM records WHERE run_id=? AND kind='MATERIAL'",(run['id'],)):
                        if material_group_key(generated_baseline(row))==logical:legacy.append(row)
                    if len(legacy)>1:
                        raise DomainError('同一材料组存在多条旧记录；为避免覆盖审核历史，已停止自动恢复',409)
                    if legacy:existing=legacy[0]
                if not existing:
                    c.execute('INSERT INTO records(id,run_id,project_id,kind,envelope,logical_key) VALUES(?,?,?,?,?,?)',
                              (record_id,run['id'],run['project_id'],record['kind'],dumps(record),logical))
                    refresh_ids.append(record_id);continue
                record_id=existing['id'];record['meta']['record_id']=record_id
                before=json.loads(existing['envelope'])
                comparable=deepcopy(generated_baseline(existing));comparable['candidate_key']=logical
                if comparable==record['candidate']:
                    # Identity-only legacy migration: preserve the current
                    # human decision and version while refreshing candidate-
                    # hash-bound verification data.
                    if before['candidate'].get('candidate_key')==logical and existing['logical_key']==logical:continue
                    migrated=deepcopy(before);migrated['candidate']['candidate_key']=logical
                    c.execute('UPDATE records SET envelope=?,logical_key=? WHERE id=?',(dumps(migrated),logical,record_id))
                    c.execute("UPDATE verification_jobs SET state='STALE',message=?,updated_at=? WHERE record_id=? AND state IN ('QUEUED','RUNNING')",
                              ('候选身份已迁移，旧核验任务失效',now(),record_id))
                    refresh_ids.append(record_id);continue
                # The logical record now includes more processed evidence. Keep
                # its identity/history, invalidate any prior human decision,
                # and make stale browser edits fail their review_version check.
                record['meta']['created_at']=before['meta']['created_at']
                if before['review']['status']!='PENDING':
                    event=uid('REVIEW')
                    c.execute('INSERT INTO review_events VALUES(?,?,?,?,?,?,?,?)',
                              (event,record_id,'cirp-system','INVALIDATED_BY_NEW_EVIDENCE',
                               dumps(before),dumps(record),'恢复分析后新增证据改变候选；原人工决定保留在历史中，当前记录重置为待审核。',now()))
                c.execute('UPDATE records SET envelope=?,logical_key=?,review_version=review_version+1 WHERE id=?',
                           (dumps(record),logical,record_id))
                c.execute("UPDATE verification_jobs SET state='STALE',message=?,updated_at=? WHERE record_id=? AND state IN ('QUEUED','RUNNING')",
                          ('候选因新增证据变化，旧核验任务失效',now(),record_id))
                refresh_ids.append(record_id)
        if seed_verifications:
            for record_id in refresh_ids:self.verifier.refresh(record_id)

    def update_coverage(self,rid):
        run=self.get(rid)
        docs=self.db.all('SELECT status,summary FROM document_results WHERE run_id=?',(rid,))
        evidence=self.db.all('SELECT status,payload FROM evidence WHERE run_id=?',(rid,))
        summaries=[json.loads(d['summary']) for d in docs]
        pages=[page for summary in summaries for page in summary.get('pages',[])]
        methods=[json.loads(e['payload']).get('extraction_method') for e in evidence]
        visual_tasks=[task for summary in summaries for task in summary.get('visual_tasks',[])]
        geometry=[item for summary in summaries for item in summary.get('geometry_summaries',[])]
        takeoffs=[item for summary in summaries for item in summary.get('takeoffs',[])]
        limitations=['复杂选项/父条款/跨专业关联仍待完善','没有做施工准确率评测；完成调用不代表完整理解']
        if any(task.get('status')!='VISION_EXTRACTED' for task in visual_tasks):
            limitations.append('仍有页面视觉任务未完成或未启用')
        if any(summary.get('cad_level')=='UNAVAILABLE' for summary in summaries):
            limitations.append('原生DWG对象解析需要本机合法转换器；不可用时未伪装为CAD量算')
        if any(item.get('material_quantity') is None for item in geometry):
            limitations.append('PDF矢量仅为整页图元审计；未分离图框/视图或映射材料时不输出设计净量')
        summary={'files_total':len(run['document_ids']),'files_processed':len(docs),
                 'files_success':sum(d['status']=='SUCCESS' for d in docs),
                 'files_partial_or_failed':sum(d['status']!='SUCCESS' for d in docs),
                 'fragments_total':len(evidence),'fragments_extracted':sum(e['status']=='EXTRACTED' for e in evidence),
                 'fragments_need_review':sum(e['status']=='NEEDS_REVIEW' for e in evidence),
                 'fragments_pending':sum(e['status']=='PENDING' for e in evidence),
                 'ocr_fragments':sum(method=='OCR' for method in methods),
                 'vision_fragments':sum(method=='VISION' for method in methods),
                  'cad_fragments':sum(method=='CAD_OBJECT' for method in methods),
                  'pages_total':len(pages),
                  'pages_processed':sum(page.get('status')!='NOT_PROCESSED' for page in pages),
                 'visual_pages_total':len(visual_tasks),
                 'visual_pages_completed':sum(task.get('status')=='VISION_EXTRACTED' for task in visual_tasks),
                 'geometry_pages':len(geometry),
                 'geometry_pages_calibrated':sum(item.get('status')=='CALIBRATED_RAW_GEOMETRY' for item in geometry),
                 'takeoff_candidates':len(takeoffs),
                 'warnings':[w for summary_item in summaries for w in summary_item.get('warnings',[])],
                 'scope_limitations':limitations}
        self.db.execute('UPDATE runs SET coverage=? WHERE id=?',(dumps(summary),rid))
