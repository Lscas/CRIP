"""单进程原型的 SQLite 事务存储；金额使用人民币百万分之一整数。"""
from __future__ import annotations
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Iterator
from app.settings import ROOT

PAID_TASK_GENERATION_MARKER = ':manual-requeue:'
MAX_PAID_TASK_CALLS = 3


def paid_task_family(task_key: str) -> str:
    """Return the stable billing family for current and legacy task keys."""
    if not isinstance(task_key, str) or not task_key:
        raise DomainError('付费任务键格式无效')
    generation = re.fullmatch(r'(.+):manual-requeue:([1-9][0-9]*)', task_key)
    if generation:
        return generation.group(1)
    # v0.2.6 verification jobs used an opaque job suffix.  Treat all of those
    # keys as one family so an upgrade cannot reset the three-call ceiling.
    if task_key.startswith('verify:'):
        legacy = re.fullmatch(r'(verify:[0-9a-f]{64}):job:[0-9a-f]{24}', task_key)
        if legacy:
            return legacy.group(1)
    return task_key


def paid_task_generation(task_key: str) -> int:
    match = re.fullmatch(r'.+:manual-requeue:([1-9][0-9]*)', task_key)
    return int(match.group(1)) if match else 0


def paid_task_key(task_family: str, generation: int) -> str:
    if not isinstance(task_family, str) or not task_family or paid_task_family(task_family) != task_family:
        raise DomainError('付费任务族格式无效')
    if type(generation) is not int or not 0 <= generation < MAX_PAID_TASK_CALLS:
        raise DomainError('付费任务恢复代次无效')
    return task_family if generation == 0 else f'{task_family}{PAID_TASK_GENERATION_MARKER}{generation}'

class DomainError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message); self.code = code

class BudgetError(DomainError):
    pass

def uid(prefix: str) -> str:
    return prefix + '-' + uuid.uuid4().hex

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def units(amount: Decimal | str) -> int:
    d = Decimal(amount)
    if not d.is_finite() or d < 0: raise DomainError('非法金额')
    return int((d * 1_000_000).to_integral_value(rounding=ROUND_CEILING))

def yuan(n: int) -> str:
    return str((Decimal(n) / 1_000_000).quantize(Decimal('0.000001')))

class Database:
    def __init__(self, path: Path):
        self.path = path; path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript((ROOT / 'migrations/001_initial.sql').read_text(encoding="utf-8"))
            c.executescript((ROOT / 'migrations/002_verification.sql').read_text(encoding="utf-8"))
            c.executescript((ROOT / 'migrations/003_call_reconciliation.sql').read_text(encoding="utf-8"))
            c.executescript((ROOT / 'migrations/004_performance_budget.sql').read_text(encoding="utf-8"))
            c.executescript((ROOT / 'migrations/005_upload_sources.sql').read_text(encoding="utf-8"))

    @contextmanager
    def connect(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            if write: c.execute('BEGIN IMMEDIATE')
            yield c
            if write: c.commit()
        except BaseException:
            if write: c.rollback()
            raise
        finally: c.close()

    def one(self, query: str, args=(), required: bool = True):
        with self.connect() as c: row = c.execute(query, args).fetchone()
        if row is None and required: raise DomainError('未找到记录', 404)
        return dict(row) if row else None

    def all(self, query: str, args=()):
        with self.connect() as c: return [dict(r) for r in c.execute(query, args).fetchall()]

    def execute(self, query: str, args=()):
        with self.connect(True) as c: c.execute(query, args)

    def create_project(self, name: str, budget_cny: Decimal = Decimal('300')):
        pid = uid('P')
        limit = units(budget_cny)
        if limit < units(Decimal('0.01')): raise DomainError('项目预算不得低于0.01元')
        with self.connect(True) as c:
            c.execute('INSERT INTO projects VALUES(?,?,?)', (pid, name, now()))
            c.execute('INSERT INTO budget_accounts(project_id,limit_units) VALUES(?,?)', (pid,limit))
        return {**self.one('SELECT * FROM projects WHERE id=?', (pid,)), 'budget_limit_cny':yuan(limit)}

    def set_budget_limit(self, project_id: str, amount: Decimal, actor: str = 'local-user') -> dict:
        limit=units(amount)
        if limit<units(Decimal('0.01')): raise DomainError('项目预算不得低于0.01元')
        with self.connect(True) as c:
            account=c.execute('SELECT * FROM budget_accounts WHERE project_id=?',(project_id,)).fetchone()
            if not account:raise DomainError('未找到项目',404)
            outstanding=c.execute('''SELECT COALESCE(SUM(reserved_units),0) FROM model_calls
                                     WHERE project_id=? AND actual_units IS NULL''',(project_id,)).fetchone()[0]
            committed=account['spent_units']+outstanding
            if limit<committed:
                raise BudgetError('项目预算不能低于已支出和未结算预留合计',409)
            if limit!=account['limit_units']:
                c.execute('UPDATE budget_accounts SET limit_units=?,frozen=0 WHERE project_id=?',(limit,project_id))
                c.execute('INSERT INTO budget_limit_events VALUES(?,?,?,?,?,?)',
                          (uid('BUDGET'),project_id,actor,account['limit_units'],limit,now()))
        return self.cost(project_id)

    def record_metric(self, run_id: str, metric: str, elapsed_ms: float) -> None:
        if not isinstance(metric,str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,63}',metric):
            raise DomainError('运行指标名称无效')
        if not isinstance(elapsed_ms,(int,float)) or not math.isfinite(elapsed_ms) or elapsed_ms<0:
            raise DomainError('运行指标耗时无效')
        milliseconds=max(0,round(elapsed_ms))
        with self.connect(True) as c:
            c.execute('''INSERT INTO run_metrics(run_id,metric,samples,total_ms,max_ms) VALUES(?,?,?,?,?)
                         ON CONFLICT(run_id,metric) DO UPDATE SET
                           samples=samples+1,
                           total_ms=total_ms+excluded.total_ms,
                           max_ms=MAX(max_ms,excluded.max_ms)''',
                      (run_id,metric,1,milliseconds,milliseconds))

    def performance(self, run_id: str) -> dict:
        rows=self.all('SELECT metric,samples,total_ms,max_ms FROM run_metrics WHERE run_id=? ORDER BY metric',(run_id,))
        return {row['metric']:{'samples':row['samples'],'total_ms':row['total_ms'],
                               'average_ms':round(row['total_ms']/row['samples']),
                               'max_ms':row['max_ms']} for row in rows}

    def cost(self, project_id: str) -> dict:
        account = self.one('SELECT * FROM budget_accounts WHERE project_id=?', (project_id,))
        calls = self.all('SELECT state,reserved_units,actual_units,usage FROM model_calls WHERE project_id=?', (project_id,))
        reserved = sum(c['reserved_units'] for c in calls if c['actual_units'] is None)
        input_tokens = output_tokens = 0
        for c in calls:
            usage = json.loads(c['usage'] or '{}')
            input_tokens += usage.get('prompt_tokens', 0)
            output_tokens += usage.get('completion_tokens', 0)
        return {'limit_cny': yuan(account['limit_units']), 'spent_cny': yuan(account['spent_units']),
                'reserved_cny': yuan(reserved),
                'available_cny': yuan(max(0, account['limit_units']-account['spent_units']-reserved)),
                'frozen': bool(account['frozen']), 'calls': len(calls),
                'unknown_calls': sum(c['state'] in ('UNKNOWN','RESERVED') for c in calls),
                'input_tokens': input_tokens, 'output_tokens': output_tokens,
                'other_external_services': 'Not connected; no paid OCR or CAD task was created',
                'local_compute_cost': 'Local computer cost is not measured; this is not a zero-cost guarantee'}

    def reserve(self, project_id: str, run_id: str, task_key: str, amount: Decimal,
                model: str, request_hash: str, input_rate: Decimal, output_rate: Decimal,
                verification_job_id: str | None = None, *, allow_zero: bool = False) -> str:
        n = units(amount)
        if n == 0:
            if not allow_zero: raise DomainError('预留金额必须为正')
            n = 1  # SQLite schema requires a positive in-flight idempotency reservation.
        aid = uid('CALL')
        with self.connect(True) as c:
            run = c.execute('SELECT * FROM runs WHERE id=? AND project_id=?', (run_id,project_id)).fetchone()
            if verification_job_id:
                job = c.execute('SELECT * FROM verification_jobs WHERE id=? AND run_id=?', (verification_job_id, run_id)).fetchone()
                record = c.execute('SELECT envelope,review_version FROM records WHERE id=?', (job['record_id'],)).fetchone() if job else None
                import hashlib
                candidate_hash = hashlib.sha256(dumps(json.loads(record['envelope'])['candidate']).encode()).hexdigest() if record else None
                if not run or not job or job['state'] != 'RUNNING' or job['candidate_hash'] != candidate_hash or job['review_version'] != record['review_version'] or time.time() >= run['deadline_epoch']:
                    raise BudgetError('核验任务已失效或到达时限，禁止新增收费请求', 409)
                if c.execute("SELECT id FROM runs WHERE status IN ('RUNNING','QUEUED')").fetchone():
                    raise BudgetError('已有活跃分析，禁止并发核验收费', 409)
            elif not run or run['status'] != 'RUNNING' or run['stop_requested'] or time.time() >= run['deadline_epoch']:
                raise BudgetError('任务已停止或到达时限，禁止新增收费请求', 409)
            # The exact task key is an idempotency boundary.  Explicit retries
            # must use a new audited generation key; a second process or thread
            # cannot race recovery and reserve the same paid task again.
            if c.execute('SELECT id FROM model_calls WHERE run_id=? AND task_key=?',
                         (run_id,task_key)).fetchone():
                raise BudgetError('该付费任务已有调用记录；禁止自动重复收费，需显式创建新任务代次',409)
            if verification_job_id and c.execute('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL', (project_id,)).fetchone():
                raise BudgetError('项目存在未对账请求，禁止新增付费调用', 409)
            a = c.execute('SELECT * FROM budget_accounts WHERE project_id=?', (project_id,)).fetchone()
            pending = c.execute('SELECT 1 FROM model_calls WHERE project_id=? AND actual_units IS NULL LIMIT 1', (project_id,)).fetchone()
            if pending:
                raise BudgetError('项目存在未对账请求，禁止新增付费调用',409)
            outstanding = 0
            if a['frozen'] or a['spent_units']+outstanding+n>a['limit_units']:
                raise BudgetError('项目累计预算不足，停止新增付费调用', 409)
            c.execute('''INSERT INTO model_calls(id,project_id,run_id,task_key,model,state,reserved_units,
                input_rate,output_rate,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (aid,project_id,run_id,task_key,model,'RESERVED',n,str(input_rate),str(output_rate),request_hash,now(),now()))
        return aid

    def settle(self, aid: str, amount: Decimal, usage: dict, provider_id: str | None, response: dict | None = None):
        """Compatibility settlement without a cache publication.

        Paid gateway success and contract-failure paths must use
        :meth:`finalize_model_call` so accounting and the durable terminal
        result are one transaction.  This wrapper remains for reconciliation
        tests and older callers which intentionally have no cache entry.
        """
        self.finalize_model_call(aid, amount, usage, provider_id, response=response)

    def finalize_model_call(self, aid: str, amount: Decimal, usage: dict,
                            provider_id: str | None, *, response: dict | None = None,
                            cache_key: str | None = None, diagnostic: dict | None = None,
                            cache_ttl_seconds: int = 86400) -> None:
        """Atomically persist one billed provider response and its terminal result.

        Exactly one of ``response`` or ``diagnostic`` is required for gateway
        finalization.  ``response`` may additionally be published to ``cache``;
        a diagnostic is terminal and is never cached.  Serialisation happens
        before acquiring the write transaction, so a JSON error cannot leave a
        partially settled call.

        A response-less/diagnostic-less call is accepted only for the legacy
        :meth:`settle` compatibility wrapper.  Gateway code does not use that
        form.
        """
        if response is not None and diagnostic is not None:
            raise DomainError('模型调用不能同时保存响应和失败诊断')
        if response is not None and not isinstance(response,dict):
            raise DomainError('模型有效响应必须是JSON对象')
        if diagnostic is not None:
            allowed={'kind','class','exception','path','validator'}
            safe_code=re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
            safe_path=re.compile(r'^[A-Za-z0-9_.-]{0,160}$')
            if (not isinstance(diagnostic,dict) or set(diagnostic)-allowed
                    or diagnostic.get('kind') not in ('CONTRACT_ERROR','POLICY_ERROR')
                    or not isinstance(diagnostic.get('class'),str)
                    or not safe_code.fullmatch(diagnostic['class'])
                    or any(not isinstance(diagnostic.get(key),str)
                           or not (safe_path if key=='path' else safe_code).fullmatch(diagnostic[key])
                           for key in ('exception','path','validator') if key in diagnostic)):
                raise DomainError('模型失败诊断不符合安全终态格式')
        if cache_key is not None and response is None:
            raise DomainError('没有有效响应时禁止写入缓存')
        if cache_key is not None and (not isinstance(cache_key,str) or not 1 <= len(cache_key) <= 512):
            raise DomainError('缓存键格式无效')
        if not isinstance(cache_ttl_seconds, int) or cache_ttl_seconds <= 0:
            raise DomainError('缓存有效期必须为正整数')
        if (provider_id is not None
                and (not isinstance(provider_id,str)
                     or not re.fullmatch(r'[A-Za-z0-9._:/=+-]{1,160}',provider_id))):
            raise DomainError('供应商请求编号格式无效')

        n = units(amount)
        usage_json = dumps(usage)
        response_json = dumps(response) if response is not None else None
        diagnostic_json = dumps(diagnostic) if diagnostic is not None else None
        state = 'SETTLED_ERROR' if diagnostic is not None else 'SETTLED'
        updated_at = now()
        expires_epoch = time.time() + cache_ttl_seconds
        with self.connect(True) as c:
            row = c.execute('SELECT * FROM model_calls WHERE id=?', (aid,)).fetchone()
            if not row: raise DomainError('结算记录不存在')
            if row['actual_units'] is not None:
                if row['actual_units'] != n: raise DomainError('不一致的重复结算')
                try:
                    stored_usage=json.loads(row['usage'])
                    stored_response=json.loads(row['response']) if row['response'] is not None else None
                    stored_diagnostic=json.loads(row['error']) if row['error'] is not None else None
                except (TypeError,ValueError,json.JSONDecodeError) as exc:
                    raise DomainError('已存在的模型调用终态损坏') from exc
                if (row['state'] != state or stored_usage != usage
                        or row['provider_request_id'] != provider_id
                        or stored_response != response or stored_diagnostic != diagnostic):
                    raise DomainError('不一致的重复终态持久化')
                return
            c.execute('''UPDATE model_calls SET actual_units=?,state=?,usage=?,provider_request_id=?,
                         response=?,error=?,updated_at=? WHERE id=?''',
                      (n,state,usage_json,provider_id,response_json,diagnostic_json,updated_at,aid))
            if cache_key is not None:
                c.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)',
                          (cache_key,row['project_id'],response_json,expires_epoch))
            c.execute('UPDATE budget_accounts SET spent_units=spent_units+?,frozen=MAX(frozen,?) WHERE project_id=?',
                      (n,int(n>row['reserved_units']),row['project_id']))
            account = c.execute('SELECT * FROM budget_accounts WHERE project_id=?',(row['project_id'],)).fetchone()
            remaining = c.execute('SELECT COALESCE(SUM(reserved_units),0) FROM model_calls WHERE project_id=? AND actual_units IS NULL',(row['project_id'],)).fetchone()[0]
            if account['spent_units'] + remaining > account['limit_units']:
                c.execute('UPDATE budget_accounts SET frozen=1 WHERE project_id=?',(row['project_id'],))

    def unknown(self, aid: str, error: str, provider_id: str | None = None):
        self.execute('''UPDATE model_calls SET state=?,error=?,
                     provider_request_id=COALESCE(?,provider_request_id),updated_at=?
                     WHERE id=? AND actual_units IS NULL''',
                     ('UNKNOWN',error,provider_id,now(),aid))

    @staticmethod
    def _public_call(row: dict) -> dict:
        try:
            diagnostic = json.loads(row.get('error') or '')
            if not isinstance(diagnostic, dict): raise ValueError
        except (ValueError, json.JSONDecodeError):
            diagnostic = {'kind': 'LEGACY_UNKNOWN'}
        kind = diagnostic.get('kind') if diagnostic.get('kind') in {
            'HTTP_STATUS','NETWORK_ERROR','INVALID_RESPONSE','CLIENT_ERROR','LEGACY_UNKNOWN'} else None
        status = diagnostic.get('status')
        safe_code = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
        safe_request_id = re.compile(r'^[A-Za-z0-9._:/=+-]{1,160}$')
        safe_retry_after = re.compile(
            r'^(?:[0-9]{1,10}|[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT)$')
        diagnostic = {
            **({'kind':kind} if kind else {}),
            **({'status':status} if type(status) is int and 100 <= status <= 599 else {}),
            **{key:value for key in ('class','provider_code')
               if isinstance((value:=diagnostic.get(key)),str) and safe_code.fullmatch(value)},
            **({'retry_after':diagnostic['retry_after']} if isinstance(diagnostic.get('retry_after'),str)
               and safe_retry_after.fullmatch(diagnostic['retry_after']) else {}),
            **({'provider_request_id':diagnostic['provider_request_id']}
               if isinstance(diagnostic.get('provider_request_id'),str)
               and safe_request_id.fullmatch(diagnostic['provider_request_id']) else {}),
        }
        provider_request_id=row.get('provider_request_id')
        if not isinstance(provider_request_id,str) or not safe_request_id.fullmatch(provider_request_id):
            provider_request_id=None
        return {'id':row['id'],'run_id':row['run_id'],'model':row['model'],'state':row['state'],
                'reserved_cny':yuan(row['reserved_units']),
                'provider_request_id':provider_request_id,
                'diagnostic':diagnostic,'created_at':row['created_at'],'updated_at':row['updated_at']}

    def unresolved_calls(self, project_id: str) -> list[dict]:
        self.one('SELECT id FROM projects WHERE id=?',(project_id,))
        rows=self.all('''SELECT id,run_id,model,state,reserved_units,provider_request_id,error,created_at,updated_at
                         FROM model_calls WHERE project_id=? AND actual_units IS NULL
                         ORDER BY created_at''',(project_id,))
        return [self._public_call(row) for row in rows]

    @staticmethod
    def _audit_payload(row) -> dict:
        try:
            value=json.loads(row['payload'])
        except (TypeError,ValueError,json.JSONDecodeError):
            return {}
        return value if isinstance(value,dict) else {}

    @staticmethod
    def _family_calls(connection: sqlite3.Connection, run_id: str, task_family: str) -> list[dict]:
        rows=connection.execute('''SELECT id,task_key,state,actual_units,response,error,created_at
                                   FROM model_calls WHERE run_id=? ORDER BY created_at,id''',(run_id,)).fetchall()
        return [dict(row) for row in rows if paid_task_family(row['task_key']) == task_family]

    @classmethod
    def _recovery_events(cls, connection: sqlite3.Connection, run_id: str,
                         task_family: str | None = None) -> list[dict]:
        rows=connection.execute('''SELECT id,call_id,payload,created_at FROM call_reconciliation_events
                                   WHERE run_id=? ORDER BY created_at,id''',(run_id,)).fetchall()
        result=[]
        for row in rows:
            payload=cls._audit_payload(row)
            if payload.get('event_type') != 'RECOVERY_GENERATION_AUTHORIZED':
                continue
            if task_family is not None and payload.get('task_family') != task_family:
                continue
            result.append({**dict(row),'payload':payload})
        return result

    @classmethod
    def _reconciliation_for_call(cls, connection: sqlite3.Connection, call_id: str) -> dict | None:
        rows=connection.execute('''SELECT id,payload,created_at FROM call_reconciliation_events
                                   WHERE call_id=? ORDER BY created_at,id''',(call_id,)).fetchall()
        for row in reversed(rows):
            payload=cls._audit_payload(row)
            # Events written before event_type was introduced are valid legacy
            # reconciliation facts when they contain the constrained resolution.
            if (payload.get('event_type') in (None,'CALL_RECONCILED')
                    and payload.get('resolution') in ('BILLED','NOT_BILLED')):
                return {**dict(row),'payload':payload}
        return None

    @classmethod
    def _authorize_reconciled_generation(cls, connection: sqlite3.Connection, *,
                                         project_id: str, run_id: str, task_family: str,
                                         trigger: str, context_id: str | None,
                                         actor: str = 'local-user') -> dict | None:
        """Authorize exactly one new generation from one reconciled no-result call.

        The old model call and its reconciliation event remain immutable.  This
        appends a second audit event; it does not itself reserve budget or send
        HTTP.  Re-entering with the same context is idempotent.
        """
        if trigger not in ('RUN_RESUME','VERIFICATION_ENQUEUE'):
            raise DomainError('未知的付费任务恢复入口')
        existing=cls._recovery_events(connection,run_id,task_family)
        for event in reversed(existing):
            if event['payload'].get('context_id') == context_id and event['payload'].get('trigger') == trigger:
                return event['payload']

        calls=cls._family_calls(connection,run_id,task_family)
        used_sources={event['payload'].get('source_call_id') for event in existing}
        candidates=[]
        for call in calls:
            if (call['id'] in used_sources or call['response'] is not None
                    or call['state'] not in ('RECONCILED_ZERO','RECONCILED_CHARGED')):
                continue
            reconciliation=cls._reconciliation_for_call(connection,call['id'])
            if reconciliation is not None:
                candidates.append((call,reconciliation))
        if not candidates:
            return None
        if len(candidates) != 1:
            raise DomainError('同一付费任务族有多条未关联的对账失败调用，不能安全选择恢复来源',409)
        if len(calls) >= MAX_PAID_TASK_CALLS:
            raise DomainError('该付费任务族已达到三次累计调用上限；保留旧记录且不再收费',409)
        call,reconciliation=candidates[0]
        known_generations=[paid_task_generation(item['task_key']) for item in calls]
        known_generations.extend(
            event['payload']['generation'] for event in existing
            if type(event['payload'].get('generation')) is int)
        generation=max(max(known_generations,default=0)+1,len(calls))
        if generation >= MAX_PAID_TASK_CALLS:
            raise DomainError('该付费任务族已达到三次累计调用上限；保留旧记录且不再收费',409)
        next_key=paid_task_key(task_family,generation)
        if any(item['task_key']==next_key for item in calls):
            raise DomainError('恢复代次已存在调用记录；禁止自动重复收费',409)
        resolution=reconciliation['payload']['resolution']
        payload={
            'event_type':'RECOVERY_GENERATION_AUTHORIZED',
            'policy':'EXPLICIT_ONLY_MAX_3',
            'task_family':task_family,
            'generation':generation,
            'attempt_number':len(calls)+1,
            'source_call_id':call['id'],
            'source_task_key':call['task_key'],
            'source_state':call['state'],
            'reconciliation_event_id':reconciliation['id'],
            'reconciliation_resolution':resolution,
            'trigger':trigger,
            'context_id':context_id,
            'next_task_key':next_key,
        }
        connection.execute('INSERT INTO call_reconciliation_events VALUES(?,?,?,?,?,?,?)',
                           (uid('RECOVERY'),call['id'],project_id,run_id,actor,dumps(payload),now()))
        return payload

    def authorize_verification_generation(self, project_id: str, run_id: str,
                                          task_family: str, job_id: str) -> dict | None:
        """Bind a user-created verification job to a reconciled retry generation."""
        with self.connect(True) as connection:
            job=connection.execute('''SELECT id,state FROM verification_jobs
                                      WHERE id=? AND run_id=?''',(job_id,run_id)).fetchone()
            if not job or job['state']!='RUNNING':
                raise DomainError('核验任务未处于可授权恢复的运行状态',409)
            return self._authorize_reconciled_generation(
                connection,project_id=project_id,run_id=run_id,task_family=task_family,
                trigger='VERIFICATION_ENQUEUE',context_id=job_id)

    def authorized_generation(self, run_id: str, task_family: str,
                              *, context_id: str | None = None) -> int:
        with self.connect() as connection:
            events=self._recovery_events(connection,run_id,task_family)
        if context_id is not None:
            events=[event for event in events if event['payload'].get('context_id')==context_id]
        else:
            events=[event for event in events if event['payload'].get('trigger')=='RUN_RESUME']
        generations=[event['payload'].get('generation') for event in events]
        valid=[value for value in generations if type(value) is int and 0 < value < MAX_PAID_TASK_CALLS]
        return max(valid,default=0)

    def authorize_run_resume_generations(self, connection: sqlite3.Connection, run: dict,
                                         *, context_id: str) -> list[dict]:
        """Atomically bind an explicit Resume to each real pending reconciled task."""
        rows=connection.execute('''SELECT id,task_key,state,response FROM model_calls
                                   WHERE run_id=? AND state IN ('RECONCILED_ZERO','RECONCILED_CHARGED')
                                   AND response IS NULL ORDER BY created_at,id''',(run['id'],)).fetchall()
        used={event['payload'].get('source_call_id')
              for event in self._recovery_events(connection,run['id'])}
        pending_sources=[dict(row) for row in rows if row['id'] not in used]
        families={paid_task_family(row['task_key']) for row in pending_sources}
        authorized=[]
        for family in sorted(families):
            if family.startswith('EV-'):
                pending=connection.execute('''SELECT id FROM evidence
                                              WHERE id=? AND run_id=? AND status='PENDING' ''',
                                           (run['id']+':'+family,run['id'])).fetchone()
                if not pending:
                    raise DomainError('已对账提取调用不对应真实待处理证据；不能安全创建恢复代次',409)
            elif family.startswith('extract-batch:'):
                try:
                    prefix,digest=family.rsplit(':',1)
                    primary=prefix[len('extract-batch:'):]
                except ValueError:
                    primary=digest=''
                if not primary.startswith('EV-') or not re.fullmatch(r'[0-9a-f]{16}',digest):
                    raise DomainError('已对账批量提取任务键无效；不能安全创建恢复代次',409)
                pending=connection.execute('''SELECT id FROM evidence
                                              WHERE id=? AND run_id=? AND status='PENDING' ''',
                                           (run['id']+':'+primary,run['id'])).fetchone()
                if not pending:
                    raise DomainError('已对账批量提取调用不对应真实待处理证据；不能安全创建恢复代次',409)
            elif family.startswith('vision:'):
                try:
                    document_id,page_text=family[len('vision:'):].rsplit(':',1)
                    page=int(page_text)
                except (ValueError,TypeError):
                    raise DomainError('已对账视觉调用的任务键无效；不能安全创建恢复代次',409)
                row=connection.execute('''SELECT summary FROM document_results
                                          WHERE run_id=? AND document_id=?''',(run['id'],document_id)).fetchone()
                if not row:
                    raise DomainError('已对账视觉调用不对应真实页面任务；不能安全创建恢复代次',409)
                try:
                    summary=json.loads(row['summary'])
                except (TypeError,ValueError,json.JSONDecodeError) as exc:
                    raise DomainError('视觉任务摘要损坏；不能安全创建恢复代次',409) from exc
                tasks=summary.get('visual_tasks') if isinstance(summary,dict) else None
                matches=[task for task in tasks or [] if isinstance(task,dict) and task.get('page')==page
                         and task.get('status')=='PAUSED_PROVIDER']
                if len(matches)!=1:
                    raise DomainError('已对账视觉调用不对应唯一的暂停页面；不能安全创建恢复代次',409)
            elif family.startswith('verify:'):
                if not connection.execute('SELECT id FROM records WHERE run_id=? LIMIT 1',(run['id'],)).fetchone():
                    raise DomainError('已对账核验调用不对应当前运行记录；不能安全创建恢复代次',409)
            else:
                raise DomainError('已对账调用的任务族未知；不能安全创建恢复代次',409)
            event=self._authorize_reconciled_generation(
                connection,project_id=run['project_id'],run_id=run['id'],task_family=family,
                trigger='RUN_RESUME',context_id=context_id)
            if event is None:
                raise DomainError('已对账调用缺少可验证的对账审计；不能安全创建恢复代次',409)
            authorized.append(event)
            if family.startswith('vision:'):
                task=matches[0]
                task['billing_generation']=event['generation']
                task['status']='PENDING'
                task['recovery_event_id']=event['reconciliation_event_id']
                connection.execute('UPDATE document_results SET summary=? WHERE run_id=? AND document_id=?',
                                   (dumps(summary),run['id'],document_id))
        return authorized

    def reconcile_call(self, call_id: str, resolution: str, amount: Decimal, note: str,
                       actor: str = 'local-user') -> dict:
        if resolution not in ('NOT_BILLED','BILLED'):
            raise DomainError('未知对账结论')
        n=units(amount)
        if (resolution=='NOT_BILLED' and n!=0) or (resolution=='BILLED' and n<=0):
            raise DomainError('未收费必须为0元；已收费必须填写大于0的实际人民币金额')
        event_id=uid('RECON')
        with self.connect(True) as c:
            row=c.execute('SELECT * FROM model_calls WHERE id=?',(call_id,)).fetchone()
            if not row:raise DomainError('未找到模型调用',404)
            if row['actual_units'] is not None or row['state']!='UNKNOWN':
                raise DomainError('该调用已结算或仍在途，不能重复对账',409)
            state='RECONCILED_ZERO' if resolution=='NOT_BILLED' else 'RECONCILED_CHARGED'
            usage={'manual_reconciliation':resolution,'actual_cny':yuan(n)}
            c.execute('UPDATE model_calls SET actual_units=?,state=?,usage=?,updated_at=? WHERE id=?',
                      (n,state,dumps(usage),now(),call_id))
            c.execute('UPDATE budget_accounts SET spent_units=spent_units+?,frozen=MAX(frozen,?) WHERE project_id=?',
                      (n,int(n>row['reserved_units']),row['project_id']))
            outstanding=c.execute('''SELECT COALESCE(SUM(reserved_units),0) FROM model_calls
                                     WHERE project_id=? AND actual_units IS NULL''',(row['project_id'],)).fetchone()[0]
            account=c.execute('SELECT * FROM budget_accounts WHERE project_id=?',(row['project_id'],)).fetchone()
            if account['spent_units']+outstanding>account['limit_units']:
                c.execute('UPDATE budget_accounts SET frozen=1 WHERE project_id=?',(row['project_id'],))
            payload={'event_type':'CALL_RECONCILED','resolution':resolution,'actual_cny':yuan(n),
                     'previous_state':row['state'],'note':note,
                     'task_family':paid_task_family(row['task_key']),
                     'previous_generation':paid_task_generation(row['task_key']),
                     'retry_policy':'EXPLICIT_NEW_GENERATION_REQUIRED_MAX_3'}
            c.execute('INSERT INTO call_reconciliation_events VALUES(?,?,?,?,?,?,?)',
                      (event_id,call_id,row['project_id'],row['run_id'],actor,dumps(payload),now()))
        result=self.one('''SELECT id,run_id,model,state,reserved_units,provider_request_id,error,created_at,updated_at
                           FROM model_calls WHERE id=?''',(call_id,))
        return {'call':self._public_call(result),'event_id':event_id,'cost':self.cost(row['project_id'])}

    def reconciliation_events(self, project_id: str) -> list[dict]:
        self.one('SELECT id FROM projects WHERE id=?',(project_id,))
        rows=self.all('''SELECT id,call_id,run_id,actor,payload,created_at FROM call_reconciliation_events
                         WHERE project_id=? ORDER BY created_at''',(project_id,))
        return [{**row,'payload':json.loads(row['payload'])} for row in rows]

    def cached(self, key: str, project: str):
        r = self.one('SELECT response FROM cache WHERE key=? AND project_id=? AND expires_epoch>?', (key,project,time.time()),False)
        return json.loads(r['response']) if r else None

    def cache_put(self, key: str, project: str, result: dict):
        self.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)',(key,project,dumps(result),time.time()+86400))
