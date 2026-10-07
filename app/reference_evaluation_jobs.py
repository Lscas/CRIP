"""Durable, explicitly confirmed sequential execution for Reference evaluations."""
from __future__ import annotations

from collections.abc import Callable

from app.db import Database,DomainError,now,uid
from app.reference_evaluations import ReferenceEvaluationStore
from contracts.runtime_rules import validate_schema


ACTIVE_JOB_STATES=('QUEUED','RUNNING','STOP_REQUESTED')
TERMINAL_JOB_STATES=('COMPLETED','STOPPED','HALTED','INTERRUPTED')


class ReferenceEvaluationJobStore:
    def __init__(self,db:Database,evaluations:ReferenceEvaluationStore,
                 current_settings:Callable[[],object],
                 execute_item:Callable[[str,str],dict]):
        self.db=db;self.evaluations=evaluations
        self.current_settings=current_settings;self.execute_item=execute_item

    def _row(self,job_id:str)->dict:
        return self.db.one('SELECT * FROM reference_evaluation_jobs WHERE id=?',(job_id,))

    def _public(self,row:dict)->dict:
        evaluation=self.evaluations.get(row['evaluation_id'])
        current=None
        if row['current_item_id']:
            item=next((value for value in evaluation['items']
                       if value['item_id']==row['current_item_id']),None)
            if item is not None:
                current={'item_id':item['item_id'],'ordinal':item['ordinal'],
                         'question':item['question']}
        value={
            'job_version':'reference-evaluation-job-1','job_id':row['id'],
            'evaluation_id':row['evaluation_id'],'project_id':row['project_id'],
            'run_id':evaluation['run_id'],'snapshot_id':evaluation['snapshot_id'],
            'question_set_hash':evaluation['question_set_hash'],
            'selector_version':evaluation['selector_version'],
            'evaluation_name':evaluation['name'],'profile':evaluation['profile'],
            'state':row['state'],'reason_code':row['reason_code'],
            'stop_requested':bool(row['stop_requested']),
            'pending_at_start':row['pending_at_start'],
            'completed_count':row['completed_count'],'failed_count':row['failed_count'],
            'remaining_count':evaluation['summary']['PENDING'],'current_item':current,
            'maximum_model_decisions':row['pending_at_start']*3,
            'execution_policy':'EXPLICIT_CONFIRMATION_SEQUENTIAL_NO_AUTOMATIC_RETRY',
            'confirmed_at':row['confirmed_at'],'created_at':row['created_at'],
            'updated_at':row['updated_at'],'started_at':row['started_at'],
            'finished_at':row['finished_at'],
        }
        validate_schema('reference-evaluation-job',value)
        return value

    def get(self,job_id:str)->dict:
        return self._public(self._row(job_id))

    def list(self,project_id:str,limit:int=20)->dict:
        self.db.one('SELECT id FROM projects WHERE id=?',(project_id,))
        total=self.db.one('''SELECT COUNT(*) AS n FROM reference_evaluation_jobs
                             WHERE project_id=?''',(project_id,))['n']
        rows=self.db.all('''SELECT * FROM reference_evaluation_jobs WHERE project_id=?
                            ORDER BY created_at DESC,rowid DESC LIMIT ?''',
                         (project_id,min(limit,20)))
        return {'items':[self._public(row) for row in rows],
                'total':total,'limit':min(limit,20)}

    def active(self)->dict|None:
        return self.db.one('''SELECT * FROM reference_evaluation_jobs
                              WHERE state IN ('QUEUED','RUNNING','STOP_REQUESTED')
                              ORDER BY created_at LIMIT 1''',required=False)

    def require_no_active(self)->None:
        if self.active() is not None:
            raise DomainError(
                'A managed Reference evaluation job is active. Stop or finish it first.',409)

    @staticmethod
    def _work_conflict(connection)->bool:
        return bool(
            connection.execute("SELECT id FROM runs WHERE status IN ('QUEUED','RUNNING') LIMIT 1").fetchone()
            or connection.execute("""SELECT id FROM verification_jobs
                                      WHERE state IN ('QUEUED','RUNNING') LIMIT 1""").fetchone())

    def create(self,evaluation_id:str,confirmed:bool)->dict:
        if confirmed is not True:
            raise DomainError(
                'Explicit confirmation is required before starting a managed Reference job.',400)
        evaluation=self.evaluations.get(evaluation_id)
        pending=[item for item in evaluation['items'] if item['state']=='PENDING']
        if not pending:
            raise DomainError('This Reference evaluation has no pending questions.',409)
        self.evaluations.require_profile(evaluation_id,self.current_settings())
        if self.db.unresolved_calls(evaluation['project_id']):
            raise DomainError(
                'Reconcile unresolved provider calls before starting a managed Reference job.',409)
        timestamp=now();job_id=uid('QAEJOB')
        with self.db.connect(True) as connection:
            if self._work_conflict(connection):
                raise DomainError(
                    'Finish the active analysis or verification before starting this job.',409)
            if connection.execute("""SELECT id FROM reference_evaluation_jobs
                                      WHERE state IN ('QUEUED','RUNNING','STOP_REQUESTED')
                                      LIMIT 1""").fetchone():
                raise DomainError('Another managed Reference evaluation job is active.',409)
            connection.execute('''INSERT INTO reference_evaluation_jobs(
                id,evaluation_id,project_id,state,reason_code,stop_requested,
                pending_at_start,completed_count,failed_count,current_item_id,
                confirmed_at,created_at,updated_at,started_at,finished_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                job_id,evaluation_id,evaluation['project_id'],'QUEUED','NONE',0,
                len(pending),0,0,None,timestamp,timestamp,timestamp,None,None))
        return self.get(job_id)

    def stop(self,job_id:str)->dict:
        row=self._row(job_id)
        if row['state'] in TERMINAL_JOB_STATES:return self._public(row)
        timestamp=now()
        if row['state']=='QUEUED':
            self.db.execute('''UPDATE reference_evaluation_jobs
                SET state='STOPPED',reason_code='USER_STOPPED',stop_requested=1,
                    current_item_id=NULL,updated_at=?,finished_at=? WHERE id=? AND state='QUEUED' ''',
                            (timestamp,timestamp,job_id))
        else:
            self.db.execute('''UPDATE reference_evaluation_jobs
                SET state='STOP_REQUESTED',reason_code='USER_STOPPED',stop_requested=1,
                    updated_at=? WHERE id=? AND state IN ('RUNNING','STOP_REQUESTED')''',
                            (timestamp,job_id))
        return self.get(job_id)

    def interrupt_active(self)->None:
        """Startup is never authorization to resume a previously confirmed job."""
        timestamp=now()
        self.db.execute('''UPDATE reference_evaluation_jobs
            SET state='INTERRUPTED',reason_code='SERVICE_INTERRUPTED',stop_requested=1,
                current_item_id=NULL,updated_at=?,finished_at=COALESCE(finished_at,?)
            WHERE state IN ('QUEUED','RUNNING','STOP_REQUESTED')''',(timestamp,timestamp))

    def request_shutdown(self)->None:
        timestamp=now()
        self.db.execute('''UPDATE reference_evaluation_jobs
            SET state=CASE WHEN state='QUEUED' THEN 'INTERRUPTED' ELSE 'STOP_REQUESTED' END,
                reason_code='SERVICE_INTERRUPTED',stop_requested=1,updated_at=?,
                finished_at=CASE WHEN state='QUEUED' THEN ? ELSE finished_at END
            WHERE state IN ('QUEUED','RUNNING','STOP_REQUESTED')''',(timestamp,timestamp))

    def finish_shutdown(self)->None:
        timestamp=now()
        self.db.execute('''UPDATE reference_evaluation_jobs
            SET state='INTERRUPTED',reason_code='SERVICE_INTERRUPTED',stop_requested=1,
                current_item_id=NULL,updated_at=?,finished_at=COALESCE(finished_at,?)
            WHERE state IN ('QUEUED','RUNNING','STOP_REQUESTED')''',(timestamp,timestamp))

    def _finish(self,job_id:str,state:str,reason_code:str)->None:
        timestamp=now()
        self.db.execute('''UPDATE reference_evaluation_jobs
            SET state=?,reason_code=?,stop_requested=CASE WHEN ?='COMPLETED' THEN 0 ELSE stop_requested END,
                current_item_id=NULL,updated_at=?,finished_at=?
            WHERE id=? AND state IN ('RUNNING','STOP_REQUESTED')''',
                        (state,reason_code,state,timestamp,timestamp,job_id))

    def _halt_reason(self,evaluation:dict)->str:
        if self.db.unresolved_calls(evaluation['project_id']):return 'UNRESOLVED_CALL'
        try:self.evaluations.require_profile(evaluation['evaluation_id'],self.current_settings())
        except DomainError:return 'PROFILE_CHANGED'
        return 'PROVIDER_HALTED'

    def process(self,job_id:str)->None:
        """Run one confirmed job synchronously on CIRP's single worker thread."""
        timestamp=now()
        with self.db.connect(True) as connection:
            row=connection.execute(
                'SELECT * FROM reference_evaluation_jobs WHERE id=?',(job_id,)).fetchone()
            if row is None or row['state']!='QUEUED':return
            if self._work_conflict(connection):
                connection.execute('''UPDATE reference_evaluation_jobs
                    SET state='HALTED',reason_code='ACTIVE_WORK_CONFLICT',updated_at=?,finished_at=?
                    WHERE id=? AND state='QUEUED' ''',(timestamp,timestamp,job_id))
                return
            connection.execute('''UPDATE reference_evaluation_jobs
                SET state='RUNNING',started_at=COALESCE(started_at,?),updated_at=?
                WHERE id=? AND state='QUEUED' ''',(timestamp,timestamp,job_id))
        while True:
            row=self._row(job_id)
            if row['state'] not in ('RUNNING','STOP_REQUESTED'):return
            evaluation=self.evaluations.get(row['evaluation_id'])
            pending=[item for item in evaluation['items'] if item['state']=='PENDING']
            if not pending:
                self._finish(job_id,'COMPLETED','COMPLETED');return
            if row['stop_requested']:
                final=('INTERRUPTED' if row['reason_code']=='SERVICE_INTERRUPTED' else 'STOPPED')
                reason=('SERVICE_INTERRUPTED' if final=='INTERRUPTED' else 'USER_STOPPED')
                self._finish(job_id,final,reason);return
            with self.db.connect() as connection:
                if self._work_conflict(connection):
                    self._finish(job_id,'HALTED','ACTIVE_WORK_CONFLICT');return
            if self.db.unresolved_calls(evaluation['project_id']):
                self._finish(job_id,'HALTED','UNRESOLVED_CALL');return
            try:self.evaluations.require_profile(
                evaluation['evaluation_id'],self.current_settings())
            except DomainError:
                self._finish(job_id,'HALTED','PROFILE_CHANGED');return
            item=pending[0];timestamp=now()
            self.db.execute('''UPDATE reference_evaluation_jobs
                SET current_item_id=?,updated_at=? WHERE id=? AND state='RUNNING' ''',
                            (item['item_id'],timestamp,job_id))
            try:
                result=self.execute_item(evaluation['evaluation_id'],item['item_id'])
            except DomainError:
                self._finish(job_id,'HALTED',self._halt_reason(evaluation));return
            except Exception:
                self._finish(job_id,'HALTED','INTERNAL_ERROR');return
            timestamp=now();failed=1 if result.get('failure') else 0
            self.db.execute('''UPDATE reference_evaluation_jobs
                SET completed_count=completed_count+1,failed_count=failed_count+?,
                    current_item_id=NULL,updated_at=?
                WHERE id=? AND state IN ('RUNNING','STOP_REQUESTED')''',
                            (failed,timestamp,job_id))
