"""持久化任务清单 + 单后台线程。暂停/恢复不会重跑已完成片段。"""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from app.db import Database,DomainError,BudgetError,dumps,now,uid
from app.settings import Settings,ROOT
from app.uploads import Uploads
from app.gateway import Gateway,ProviderPaused,InvalidModelOutput
from app.assemble import envelopes,missing,key
from contracts.runtime_rules import validate_schema

ACTIVE=('QUEUED','RUNNING')

class Runner:
    def __init__(self,db:Database,settings:Settings,uploads:Uploads,gateway:Gateway):
        self.db=db;self.s=settings;self.uploads=uploads;self.gateway=gateway
        self.stop_event=threading.Event();self.thread=None
        self.parse_dir=settings.data_dir/'parsed';self.parse_dir.mkdir(exist_ok=True)

    def create(self,project_id):
        self.db.one('SELECT * FROM projects WHERE id=?',(project_id,))
        if self.s.provider not in ('mock','deepseek'):raise DomainError('Provider未支持',409)
        if self.s.provider=='deepseek' and self.s.live_errors():raise DomainError('；'.join(self.s.live_errors()),409)
        with self.db.connect(True) as c:
            if c.execute("SELECT id FROM runs WHERE status IN ('QUEUED','RUNNING')").fetchone():raise DomainError('当前仅允许一个活跃项目分析',409)
            docs=[dict(d) for d in c.execute('SELECT id,sha256 FROM documents WHERE project_id=? ORDER BY id',(project_id,))]
            if not docs:raise DomainError('没有已完成上传的文件',409)
            rid=uid('RUN'); snapshot='SN-'+hashlib.sha256(dumps(docs).encode()).hexdigest()[:32]
            timestamp=time.time()
            c.execute('''INSERT INTO runs(id,project_id,provider,snapshot_id,document_ids,status,stage,created_at,
                         started_epoch,deadline_epoch,capabilities) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                      (rid,project_id,self.s.provider,snapshot,dumps([d['id'] for d in docs]),'QUEUED','等待处理',now(),timestamp,
                       timestamp+self.s.deadline_seconds,dumps(self.s.public()['capabilities'])))
        return self.get(rid)

    def get(self,rid):
        r=self.db.one('SELECT * FROM runs WHERE id=?',(rid,))
        for k in ('document_ids','coverage','capabilities'):r[k]=json.loads(r[k])
        return r

    def control(self,rid,action):
        with self.db.connect(True) as c:
            r=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            if not r:raise DomainError('任务不存在',404)
            if action=='resume':
                if r['status'] not in ('PAUSED','PAUSED_PROVIDER','PAUSED_BUDGET','INTERRUPTED'):raise DomainError('当前任务不可恢复；已结束任务需新建分析',409)
                if time.time()>=r['deadline_epoch']:raise DomainError('已到原运行24小时时限，需明确新建运行；项目预算不重置',409)
                if c.execute("SELECT id FROM runs WHERE status IN ('QUEUED','RUNNING')").fetchone():raise DomainError('已有活跃任务',409)
                if c.execute('SELECT id FROM model_calls WHERE run_id=? AND actual_units IS NULL',(rid,)).fetchone():raise DomainError('有待对账API请求，先核对账单；不会自动再次付费',409)
                c.execute('UPDATE runs SET status=?,stop_requested=0,message=? WHERE id=?',('QUEUED','恢复未完成任务',rid))
            elif action in ('pause','cancel'):
                if r['status'] not in ACTIVE:raise DomainError('当前没有活跃任务',409)
                status='CANCELLED' if action=='cancel' else 'PAUSED'
                c.execute('UPDATE runs SET stop_requested=1,status=?,message=? WHERE id=?',(status,'已请求停止；在途请求仍需结算',rid))
            else:raise DomainError('未知操作')
        return self.get(rid)

    def start(self):
        self.db.execute("UPDATE runs SET status='INTERRUPTED',message='服务曾中断；已完成片段保留，付费请求需对账' WHERE status='RUNNING'")
        self.thread=threading.Thread(target=self.loop,name='cirp-worker',daemon=True);self.thread.start()

    def close(self):
        self.stop_event.set()
        self.db.execute("UPDATE runs SET stop_requested=1 WHERE status='RUNNING'")
        if self.thread:self.thread.join(timeout=135)

    def loop(self):
        while not self.stop_event.wait(.25):
            row=self.db.one("SELECT id FROM runs WHERE status='QUEUED' ORDER BY created_at LIMIT 1",required=False)
            if row:self.process(row['id'])

    def checkpoint(self,rid):
        r=self.get(rid)
        if self.stop_event.is_set() or r['stop_requested'] or r['status']!='RUNNING':return False
        if time.time()>=r['deadline_epoch']:
            self.db.execute('UPDATE runs SET status=?,message=? WHERE id=?',('PAUSED_DEADLINE','到达24小时目标，停止新增任务；未完成范围保留',rid));return False
        return True

    def process(self,rid):
        with self.db.connect(True) as c:
            row=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            if not row or row['status']!='QUEUED':return
            c.execute("UPDATE runs SET status='RUNNING',stage='解析文件' WHERE id=?",(rid,))
        run=self.get(rid);run['model']=self.s.cheap_model
        try:
            for did in run['document_ids']:
                if not self.checkpoint(rid):break
                if self.db.one('SELECT 1 FROM document_results WHERE run_id=? AND document_id=?',(rid,did),False):continue
                self.parse_one(run,did);self.update_coverage(rid)
            if not self.checkpoint(rid):return
            self.db.execute('UPDATE runs SET stage=? WHERE id=?',('一次读取，联合提取材料与检查要求',rid))
            todo=self.db.all("SELECT * FROM evidence WHERE run_id=? AND status='PENDING' ORDER BY id",(rid,))
            for e in todo:
                if not self.checkpoint(rid):break
                ev=json.loads(e['payload'])
                try:
                    result=self.gateway.extract(run,ev)
                    self.db.execute('UPDATE evidence SET extraction=?,status=?,error=? WHERE id=?',
                        (dumps({'data':result.data,'request_id':result.request_id,'cached':result.cached}),
                         'EXTRACTED' if result.data['disposition']=='CANDIDATES' else 'NEEDS_REVIEW','',e['id']))
                except InvalidModelOutput as exc:
                    self.db.execute('UPDATE evidence SET status=?,error=? WHERE id=?',('NEEDS_REVIEW',str(exc),e['id']))
                self.update_coverage(rid)
            if not self.checkpoint(rid):return
            self.db.execute('UPDATE runs SET stage=? WHERE id=?',('生成可审核记录与设计差异',rid))
            self.publish(run)
            self.db.execute('UPDATE runs SET status=?,stage=?,message=? WHERE id=?',
                ('PARTIAL','本轮基线任务已结束',
                 '仅完成当前文字处理能力；视觉/CAD/几何数量、复杂选项与跨专业关联尚未完成。'
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

    def parse_one(self,run,did):
        doc=self.db.one('SELECT * FROM documents WHERE id=?',(did,))
        output=self.parse_dir/(run['id']+'-'+did+'.json')
        env=os.environ.copy()
        # 解析进程不继承API密钥；任何文字不会被执行。
        for k in list(env):
            if any(word in k.upper() for word in ('API_KEY','SECRET','TOKEN')):env.pop(k,None)
        try:
            subprocess.run([sys.executable,'-m','app.parser_worker',str(self.uploads.object_path(doc)),doc['name'],str(output)],
                           cwd=ROOT,env=env,timeout=self.s.parser_timeout,check=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            result=json.loads(output.read_text(encoding='utf-8'))
        except Exception as exc:
            result={'status':'FAILED','fragments':[],'pages':[],'warnings':['解析进程失败或超时：'+type(exc).__name__]}
        with self.db.connect(True) as c:
            for i,f in enumerate(result.pop('fragments',[])):
                eid='EV-'+key(run['snapshot_id'],did,i)
                ev={'evidence_id':eid,'tenant_id':'local','project_id':run['project_id'],'input_snapshot_id':run['snapshot_id'],
                    'document_id':did,'component_id':f'{did}:page:{f["locator"].get("page_number") or 1}',
                    'file_sha256':doc['sha256'],'internal_revision_date':f['internal_revision_date'],
                    'revision_label':f['revision_label'],'locator':f['locator'],'raw_text':f['text'],
                    'image_crop_uri':None,'extraction_method':f['method'],'confidence':None}
                validate_schema('evidence',ev)
                # 同快照重跑保持EV逻辑标识；数据库主键按运行隔离。
                storage_id=run['id']+':'+eid
                c.execute('INSERT OR IGNORE INTO evidence(id,run_id,project_id,document_id,payload) VALUES(?,?,?,?,?)',
                          (storage_id,run['id'],run['project_id'],did,dumps(ev)))
            c.execute('INSERT INTO document_results VALUES(?,?,?,?)',(run['id'],did,result['status'],dumps(result)))

    def publish(self,run):
        rows=self.db.all('SELECT * FROM evidence WHERE run_id=?',(run['id'],));evs={};extracted=[];missing_items=[]
        for row in rows:
            e=json.loads(row['payload']);evs[e['evidence_id']]=e
            if row['extraction']:extracted.append((e,json.loads(row['extraction'])['data']))
            elif row['error']:missing_items.append(missing(e['document_id'],row['error'],'NOT_LOCATED',[e['evidence_id']],e['evidence_id']))
        for row in self.db.all('SELECT * FROM document_results WHERE run_id=?',(run['id'],)):
            s=json.loads(row['summary'])
            if row['status']!='SUCCESS':
                missing_items.append(missing(row['document_id'],'；'.join(s.get('warnings',[])) or '文件内容未完整解析','PARSE_FAILED',target=row['document_id']))
        for record in envelopes(run,evs,extracted,missing_items):
            logical=record['candidate']['candidate_key']
            self.db.execute('INSERT OR IGNORE INTO records(id,run_id,project_id,kind,envelope,logical_key) VALUES(?,?,?,?,?,?)',
                            (record['meta']['record_id'],run['id'],run['project_id'],record['kind'],dumps(record),logical))

    def update_coverage(self,rid):
        run=self.get(rid)
        docs=self.db.all('SELECT status,summary FROM document_results WHERE run_id=?',(rid,))
        evidence=self.db.all('SELECT status FROM evidence WHERE run_id=?',(rid,))
        summary={'files_total':len(run['document_ids']),'files_processed':len(docs),
                 'files_success':sum(d['status']=='SUCCESS' for d in docs),
                 'files_partial_or_failed':sum(d['status']!='SUCCESS' for d in docs),
                 'fragments_total':len(evidence),'fragments_extracted':sum(e['status']=='EXTRACTED' for e in evidence),
                 'fragments_need_review':sum(e['status']=='NEEDS_REVIEW' for e in evidence),
                 'fragments_pending':sum(e['status']=='PENDING' for e in evidence),
                 'warnings':[w for d in docs for w in json.loads(d['summary']).get('warnings',[])],
                 'scope_limitations':['视觉、CAD、几何Takeoff未完成','复杂选项/父条款/跨专业关联仍待实现',
                                      '没有做施工准确率评测；完成调用不代表完整理解']}
        self.db.execute('UPDATE runs SET coverage=? WHERE id=?',(dumps(summary),rid))
