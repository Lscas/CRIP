"""Single-process SQLite storage and model-call safety records."""
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
from app.reference_result_migration import (
    install_reference_projection_results,
    preflight_reference_projection_results,
)
from app.reference_case_migration import (
    install_projection_human_review,
    preflight_projection_human_review,
)
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

class CallSafetyError(DomainError):
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
            c.executescript((ROOT / 'migrations/006_workflow_classification_overrides.sql').read_text(encoding="utf-8"))
            self.evidence_search_available=self._install_evidence_search(c)
            c.executescript((ROOT / 'migrations/008_canonical_document_graph.sql').read_text(encoding='utf-8'))
            self.canonical_search_available=self._install_optional_virtual_table(
                c,9,'migrations/009_canonical_search.sql','fts5','content_search')
            self.canonical_bounds_available=self._install_optional_virtual_table(
                c,10,'migrations/010_canonical_bounds.sql','rtree','content_bounds')
            # Validate old or current reference-results state before migration
            # 011's CREATE INDEX IF NOT EXISTS can otherwise silently repair a
            # malformed marked database and hide an interrupted deployment.
            self._preflight_reference_projection_results(c)
            c.executescript((ROOT/'migrations/011_reference_results.sql').read_text(encoding='utf-8'))
            c.executescript((ROOT/'migrations/012_reference_evaluations.sql').read_text(encoding='utf-8'))
            c.executescript((ROOT/'migrations/013_reference_evaluation_failures.sql').read_text(encoding='utf-8'))
            c.executescript((ROOT/'migrations/014_reference_evaluation_jobs.sql').read_text(encoding='utf-8'))
            c.executescript((ROOT/'migrations/015_reference_evaluation_adjudications.sql').read_text(encoding='utf-8'))
            self._install_reference_evaluation_selector_version(c)
            self._install_reference_failure_execution_receipt(c)
            self._install_reference_selector_v5(c)
            self._install_reference_selector_v6(c)
            self._install_reference_selector_v7(c)
            self._install_reference_selector_v8(c)
            self._install_reference_input_commitment(c)
            # Must run before 022/023 CREATE IF NOT EXISTS can hide an
            # interrupted or name-shadowed human-case migration state.
            preflight_projection_human_review(c)
            c.executescript((ROOT/'migrations/022_reference_cases.sql').read_text(encoding='utf-8'))
            c.executescript((ROOT/'migrations/023_reference_case_followups.sql').read_text(encoding='utf-8'))
            self._install_reference_selector_v9(c)
            self._install_reference_failure_execution(c)
            # The preceding legacy chain uses independent migration commits;
            # schema27 must begin only after schema26 is durable.
            self._install_reference_projection_results(c)
            # Historical migration fixtures deliberately stop before 027; do
            # not make schema28 mask or reject that isolated legacy state.
            if c.execute('SELECT 1 FROM schema_migrations WHERE version=27').fetchone():
                install_projection_human_review(c)

    @staticmethod
    def _preflight_reference_projection_results(connection: sqlite3.Connection) -> None:
        preflight_reference_projection_results(connection)

    @staticmethod
    def _install_reference_projection_results(connection: sqlite3.Connection) -> None:
        install_reference_projection_results(connection)

    @staticmethod
    def _install_reference_evaluation_selector_version(connection:sqlite3.Connection)->None:
        if connection.execute(
                'SELECT 1 FROM schema_migrations WHERE version=16').fetchone():
            return
        columns={row['name'] for row in connection.execute(
            "PRAGMA table_info('reference_evaluations')").fetchall()}
        if 'selector_version' not in columns:
            connection.executescript((
                ROOT/'migrations/016_reference_evaluation_selector_version.sql'
            ).read_text(encoding='utf-8'))
            return
        # Recover a database interrupted after ALTER TABLE but before the
        # migration marker was persisted.  Never attempt the ALTER twice.
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES(16,datetime('now'))")
        connection.commit()

    @staticmethod
    def _install_reference_failure_execution_receipt(connection:sqlite3.Connection)->None:
        if connection.execute(
                'SELECT 1 FROM schema_migrations WHERE version=17').fetchone():
            return
        columns={row['name'] for row in connection.execute(
            "PRAGMA table_info('reference_evaluation_failures')").fetchall()}
        if 'execution_receipt_json' not in columns:
            connection.executescript((
                ROOT/'migrations/017_reference_failure_execution_receipt.sql'
            ).read_text(encoding='utf-8'))
            return
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES(17,datetime('now'))")
        connection.commit()

    @staticmethod
    def _install_reference_failure_execution(connection:sqlite3.Connection)->None:
        if connection.execute('SELECT 1 FROM schema_migrations WHERE version=26').fetchone():
            return
        columns={row['name'] for row in connection.execute(
            "PRAGMA table_info('reference_evaluation_failures')").fetchall()}
        if 'failure_execution_json' not in columns:
            try:
                connection.executescript((ROOT/'migrations/026_reference_failure_execution.sql').read_text(encoding='utf-8'))
            except sqlite3.DatabaseError:
                if connection.in_transaction:
                    connection.rollback()
                raise
            return
        connection.execute("INSERT OR IGNORE INTO schema_migrations VALUES(26,datetime('now'))")
        connection.commit()

    @staticmethod
    def _install_reference_selector_v5(connection:sqlite3.Connection)->None:
        if connection.execute(
                'SELECT 1 FROM schema_migrations WHERE version=18').fetchone():
            return
        connection.executescript((
            ROOT/'migrations/018_reference_selector_v5.sql'
        ).read_text(encoding='utf-8'))
        violations=connection.execute('PRAGMA foreign_key_check').fetchall()
        if violations:
            raise RuntimeError('Reference selector v5 migration violated foreign keys')

    @staticmethod
    def _install_reference_selector_v6(connection:sqlite3.Connection)->None:
        if connection.execute(
                'SELECT 1 FROM schema_migrations WHERE version=19').fetchone():
            return
        connection.executescript((
            ROOT/'migrations/019_reference_selector_v6.sql'
        ).read_text(encoding='utf-8'))
        violations=connection.execute('PRAGMA foreign_key_check').fetchall()
        if violations:
            raise RuntimeError('Reference selector v6 migration violated foreign keys')

    @staticmethod
    def _install_reference_selector_v7(connection:sqlite3.Connection)->None:
        if connection.execute(
                'SELECT 1 FROM schema_migrations WHERE version=20').fetchone():
            return
        connection.executescript((
            ROOT/'migrations/020_reference_selector_v7.sql'
        ).read_text(encoding='utf-8'))
        violations=connection.execute('PRAGMA foreign_key_check').fetchall()
        if violations:
            raise RuntimeError('Reference selector v7 migration violated foreign keys')

    @staticmethod
    def _install_reference_selector_v8(connection:sqlite3.Connection)->None:
        if connection.execute(
                'SELECT 1 FROM schema_migrations WHERE version=21').fetchone():return
        connection.executescript((ROOT/'migrations/021_reference_selector_v8.sql').read_text(encoding='utf-8'))
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Reference selector v8 migration violated foreign keys')

    @staticmethod
    def _install_reference_selector_v9(connection:sqlite3.Connection)->None:
        if connection.execute(
                'SELECT 1 FROM schema_migrations WHERE version=25').fetchone():return
        connection.executescript((ROOT/'migrations/025_reference_selector_v9.sql').read_text(encoding='utf-8'))
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Reference selector v9 migration violated foreign keys')

    @staticmethod
    def _install_reference_input_commitment(connection:sqlite3.Connection)->None:
        """Install migration 24 safely after an interrupted ALTER TABLE.

        The new columns deliberately remain nullable: only named reference
        profiles require them, and historical calls must retain their original
        authentication semantics.
        """
        columns={row['name'] for row in connection.execute(
            "PRAGMA table_info('model_calls')").fetchall()}
        for name in ('reference_input_commitment_version',
                     'reference_input_commitment_sha256'):
            if name not in columns:
                connection.execute(f'ALTER TABLE model_calls ADD COLUMN {name} TEXT')
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES(24,datetime('now'))")
        connection.commit()

    @staticmethod
    def _install_evidence_search(connection: sqlite3.Connection) -> bool:
        installed=connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_search'"
        ).fetchone()
        migrated=connection.execute(
            'SELECT 1 FROM schema_migrations WHERE version=7'
        ).fetchone()
        if installed and migrated:
            return True
        try:
            connection.executescript(
                (ROOT / 'migrations/007_evidence_search.sql').read_text(encoding='utf-8')
            )
        except sqlite3.OperationalError as exc:
            if 'no such module: fts5' in str(exc).casefold():
                return False
            raise
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_search'"
        ).fetchone() is not None

    @staticmethod
    def _install_optional_virtual_table(connection: sqlite3.Connection,version: int,
                                        relative_path: str,module: str,table: str) -> bool:
        installed=connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)
        ).fetchone()
        migrated=connection.execute(
            'SELECT 1 FROM schema_migrations WHERE version=?',(version,)
        ).fetchone()
        if installed and migrated:return True
        try:connection.executescript((ROOT/relative_path).read_text(encoding='utf-8'))
        except sqlite3.OperationalError as exc:
            if f'no such module: {module}' in str(exc).casefold():return False
            raise
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)
        ).fetchone() is not None

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
        return self.one('SELECT * FROM projects WHERE id=?', (pid,))

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

    def model_call_stats(self, project_id: str) -> dict:
        """Operational call counts and provider-reported tokens; no money or limit state."""
        self.one('SELECT id FROM projects WHERE id=?',(project_id,))
        calls=self.all('SELECT state,usage FROM model_calls WHERE project_id=?',(project_id,))
        input_tokens=output_tokens=0
        for call in calls:
            usage=json.loads(call['usage'] or '{}')
            input_tokens+=usage.get('prompt_tokens',0)
            output_tokens+=usage.get('completion_tokens',0)
        return {'calls':len(calls),
                'unknown_calls':sum(call['state'] in ('UNKNOWN','RESERVED') for call in calls),
                'input_tokens':input_tokens,'output_tokens':output_tokens}

    def reserve(self, project_id: str, run_id: str, task_key: str, amount: Decimal,
                model: str, request_hash: str, input_rate: Decimal, output_rate: Decimal,
                verification_job_id: str | None = None, *, allow_zero: bool = False,
                interactive_question: bool = False) -> str:
        n = units(amount)
        if n == 0:
            n = 1  # Legacy schema sentinel; no spending limit is enforced.
        aid = uid('CALL')
        with self.connect(True) as c:
            run = c.execute('SELECT * FROM runs WHERE id=? AND project_id=?', (run_id,project_id)).fetchone()
            if verification_job_id and interactive_question:
                raise CallSafetyError('A model call cannot be both verification and project Q&A.',409)
            if interactive_question:
                if (not run or run['status'] not in ('PARTIAL','COMPLETED')
                        or not re.fullmatch(
                            r'answer(?:-v2(?:-vision)?|-v3-r[0-2])?:[0-9a-f]{64}',task_key)):
                    raise CallSafetyError('The project question is not bound to a completed analysis snapshot.',409)
                if c.execute("SELECT id FROM runs WHERE status IN ('RUNNING','QUEUED')").fetchone():
                    raise CallSafetyError('Wait for the active analysis to finish before asking a model question.',409)
            elif verification_job_id:
                job = c.execute('SELECT * FROM verification_jobs WHERE id=? AND run_id=?', (verification_job_id, run_id)).fetchone()
                record = c.execute('SELECT envelope,review_version FROM records WHERE id=?', (job['record_id'],)).fetchone() if job else None
                import hashlib
                candidate_hash = hashlib.sha256(dumps(json.loads(record['envelope'])['candidate']).encode()).hexdigest() if record else None
                if not run or not job or job['state'] != 'RUNNING' or job['candidate_hash'] != candidate_hash or job['review_version'] != record['review_version'] or time.time() >= run['deadline_epoch']:
                    raise CallSafetyError('核验任务已失效或到达时限，禁止新增模型请求', 409)
                if c.execute("SELECT id FROM runs WHERE status IN ('RUNNING','QUEUED')").fetchone():
                    raise CallSafetyError('已有活跃分析，禁止并发模型核验', 409)
            elif not run or run['status'] != 'RUNNING' or run['stop_requested'] or time.time() >= run['deadline_epoch']:
                raise CallSafetyError('任务已停止或到达时限，禁止新增模型请求', 409)
            # The exact task key is an idempotency boundary.  Explicit retries
            # must use a new audited generation key; a second process or thread
            # cannot race recovery and reserve the same paid task again.
            if c.execute('SELECT id FROM model_calls WHERE run_id=? AND task_key=?',
                         (run_id,task_key)).fetchone():
                raise CallSafetyError('该模型任务已有调用记录；禁止自动重复发送，需显式创建新任务代次',409)
            if verification_job_id and c.execute('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL', (project_id,)).fetchone():
                raise CallSafetyError('项目存在未确认请求，禁止新增模型调用', 409)
            pending = c.execute('SELECT 1 FROM model_calls WHERE project_id=? AND actual_units IS NULL LIMIT 1', (project_id,)).fetchone()
            if pending:
                raise CallSafetyError('项目存在未确认请求，禁止新增模型调用',409)
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
                            cache_ttl_seconds: int = 86400,
                            reference_input_commitment:tuple[str,str]|None=None) -> None:
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
            from app.answer_diagnostics import valid_numeric_diagnostic
            allowed={'kind','class','exception','path','validator','semantic_detail'}
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
            if 'semantic_detail' in diagnostic and not valid_numeric_diagnostic(diagnostic):
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
        if reference_input_commitment is not None:
            version,digest=reference_input_commitment
            if version!='reference-input-commitment-1' or not re.fullmatch(r'[0-9a-f]{64}',digest):
                raise DomainError('Reference input commitment is invalid.')
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
                        or stored_response != response or stored_diagnostic != diagnostic
                        or row['reference_input_commitment_version'] != (reference_input_commitment[0] if reference_input_commitment else None)
                        or row['reference_input_commitment_sha256'] != (reference_input_commitment[1] if reference_input_commitment else None)):
                    raise DomainError('不一致的重复终态持久化')
                return
            c.execute('''UPDATE model_calls SET actual_units=?,state=?,usage=?,provider_request_id=?,
                         response=?,error=?,reference_input_commitment_version=?,reference_input_commitment_sha256=?,updated_at=? WHERE id=?''',
                      (n,state,usage_json,provider_id,response_json,diagnostic_json,
                       *(reference_input_commitment or (None,None)),updated_at,aid))
            if cache_key is not None:
                c.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)',
                          (cache_key,row['project_id'],response_json,expires_epoch))

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
            payload={'event_type':'CALL_RECONCILED','resolution':resolution,'actual_cny':yuan(n),
                     'previous_state':row['state'],'note':note,
                     'task_family':paid_task_family(row['task_key']),
                     'previous_generation':paid_task_generation(row['task_key']),
                     'retry_policy':'EXPLICIT_NEW_GENERATION_REQUIRED_MAX_3'}
            c.execute('INSERT INTO call_reconciliation_events VALUES(?,?,?,?,?,?,?)',
                      (event_id,call_id,row['project_id'],row['run_id'],actor,dumps(payload),now()))
        result=self.one('''SELECT id,run_id,model,state,reserved_units,provider_request_id,error,created_at,updated_at
                           FROM model_calls WHERE id=?''',(call_id,))
        return {'call':self._public_call(result),'event_id':event_id}

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
