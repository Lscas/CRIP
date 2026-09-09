"""单进程原型的 SQLite 事务存储；金额使用人民币百万分之一整数。"""
from __future__ import annotations
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Iterator
from app.settings import ROOT

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

    def create_project(self, name: str):
        pid = uid('P')
        with self.connect(True) as c:
            c.execute('INSERT INTO projects VALUES(?,?,?)', (pid, name, now()))
            c.execute('INSERT INTO budget_accounts(project_id) VALUES(?)', (pid,))
        return self.one('SELECT * FROM projects WHERE id=?', (pid,))

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
                'other_external_services': '未接入；没有产生付费OCR/CAD任务',
                'local_compute_cost': '用户电脑成本未计量，不是零成本保证'}

    def reserve(self, project_id: str, run_id: str, task_key: str, amount: Decimal,
                model: str, request_hash: str, input_rate: Decimal, output_rate: Decimal) -> str:
        n = units(amount)
        if n <= 0: raise DomainError('预留金额必须为正')
        aid = uid('CALL')
        with self.connect(True) as c:
            run = c.execute('SELECT * FROM runs WHERE id=? AND project_id=?', (run_id,project_id)).fetchone()
            if not run or run['status'] != 'RUNNING' or run['stop_requested'] or time.time() >= run['deadline_epoch']:
                raise BudgetError('任务已停止或到达时限，禁止新增收费请求', 409)
            a = c.execute('SELECT * FROM budget_accounts WHERE project_id=?', (project_id,)).fetchone()
            outstanding = c.execute('SELECT COALESCE(SUM(reserved_units),0) FROM model_calls WHERE project_id=? AND actual_units IS NULL', (project_id,)).fetchone()[0]
            if a['frozen'] or a['spent_units']+outstanding+n>a['limit_units']:
                raise BudgetError('项目累计预算不足，停止新增付费调用', 409)
            c.execute('''INSERT INTO model_calls(id,project_id,run_id,task_key,model,state,reserved_units,
                input_rate,output_rate,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (aid,project_id,run_id,task_key,model,'RESERVED',n,str(input_rate),str(output_rate),request_hash,now(),now()))
        return aid

    def settle(self, aid: str, amount: Decimal, usage: dict, provider_id: str | None, response: dict | None = None):
        n = units(amount)
        with self.connect(True) as c:
            row = c.execute('SELECT * FROM model_calls WHERE id=?', (aid,)).fetchone()
            if not row: raise DomainError('结算记录不存在')
            if row['actual_units'] is not None:
                if row['actual_units'] != n: raise DomainError('不一致的重复结算')
                return
            c.execute('UPDATE model_calls SET actual_units=?,state=?,usage=?,provider_request_id=?,response=?,updated_at=? WHERE id=?',
                      (n,'SETTLED',dumps(usage),provider_id,dumps(response) if response else None,now(),aid))
            c.execute('UPDATE budget_accounts SET spent_units=spent_units+?,frozen=MAX(frozen,?) WHERE project_id=?',
                      (n,int(n>row['reserved_units']),row['project_id']))
            account = c.execute('SELECT * FROM budget_accounts WHERE project_id=?',(row['project_id'],)).fetchone()
            remaining = c.execute('SELECT COALESCE(SUM(reserved_units),0) FROM model_calls WHERE project_id=? AND actual_units IS NULL',(row['project_id'],)).fetchone()[0]
            if account['spent_units'] + remaining > account['limit_units']:
                c.execute('UPDATE budget_accounts SET frozen=1 WHERE project_id=?',(row['project_id'],))

    def unknown(self, aid: str, error: str):
        self.execute('UPDATE model_calls SET state=?,error=?,updated_at=? WHERE id=? AND actual_units IS NULL',
                     ('UNKNOWN',error,now(),aid))

    def cached(self, key: str, project: str):
        r = self.one('SELECT response FROM cache WHERE key=? AND project_id=? AND expires_epoch>?', (key,project,time.time()),False)
        return json.loads(r['response']) if r else None

    def cache_put(self, key: str, project: str, result: dict):
        self.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)',(key,project,dumps(result),time.time()+86400))
