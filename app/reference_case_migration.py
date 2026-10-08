"""Atomic schema28 expansion for persisted human-case source statuses."""
from __future__ import annotations
import sqlite3
from app.settings import ROOT

VERSION=28; _STAGE_C='reference_cases__migration28'; _STAGE_F='reference_case_followups__migration28'
_CASES=('id','source_key','project_id','run_id','snapshot_id','question','question_key','result_id','evaluation_id','evaluation_item_id','source_status','status','assignee','resolution','version','created_at','updated_at')
_FOL=('id','case_id','run_id','snapshot_id','result_id','result_status','proof_json','payload_hash','created_at')
_TABLES=('reference_cases','reference_case_events','reference_case_attachments','reference_case_followups')
_CASE_IDX={'ix_reference_cases_project':'CREATE INDEX ix_reference_cases_project\n ON reference_cases(project_id,status,updated_at DESC,id)','ix_reference_cases_run':'CREATE INDEX ix_reference_cases_run\n ON reference_cases(run_id,updated_at DESC,id)'}
_EVENT_IDX={'ix_reference_case_events_case':'CREATE INDEX ix_reference_case_events_case\n ON reference_case_events(case_id,created_at,id)'}
_FOLLOW_IDX={'ix_reference_case_followups_case':'CREATE INDEX ix_reference_case_followups_case ON reference_case_followups(case_id,created_at,id)'}
_INDEXES=tuple((*_CASE_IDX,*_EVENT_IDX,*_FOLLOW_IDX)); _RESERVED=(*_TABLES,*_INDEXES,_STAGE_C,_STAGE_F)
def _sql(s):return ' '.join(s.split()).replace('CREATE TABLE "reference_cases"','CREATE TABLE reference_cases').replace('CREATE TABLE "reference_case_followups"','CREATE TABLE reference_case_followups')
def _table(c,n):
 r=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(n,)).fetchone();return None if not r else r[0]
def _idx(c,n):return {r[0]:r[1] for r in c.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",(n,))}
def _mark(c,n):return c.execute('SELECT 1 FROM schema_migrations WHERE version=?',(n,)).fetchone() is not None
def _old(n):
 p=ROOT/'migrations'/('022_reference_cases.sql' if n=='reference_cases' else '023_reference_case_followups.sql');s=p.read_text(encoding='utf-8');return s[s.index('CREATE TABLE'):].split(';',1)[0].replace('CREATE TABLE IF NOT EXISTS','CREATE TABLE',1)
def _child(n):
 s=(ROOT/'migrations'/'022_reference_cases.sql').read_text(encoding='utf-8');return s[s.index('CREATE TABLE IF NOT EXISTS '+n):].split(';',1)[0].replace('CREATE TABLE IF NOT EXISTS','CREATE TABLE',1)
def _expected(a='reference_cases',b='reference_case_followups'):
 s=(ROOT/'migrations'/'028_projection_human_review.sql').read_text(encoding='utf-8');return [x.strip().replace('{{CASES}}',a).replace('{{FOLLOWUPS}}',b) for x in s.split(';') if x.strip()]
def _same(c,n,s):return _sql(_table(c,n) or '')==_sql(s)
def _trigger(c,n):return c.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?",(n,)).fetchone() is not None
def _maps(c,v):
 e=_expected() if v==28 else None; cases={'ix_reference_cases_project':e[2],'ix_reference_cases_run':e[3]} if e else _CASE_IDX; follow={'ix_reference_case_followups_case':e[4]} if e else _FOLLOW_IDX
 return _idx(c,'reference_cases')==cases and _idx(c,'reference_case_events')==_EVENT_IDX and _idx(c,'reference_case_attachments')=={} and _idx(c,'reference_case_followups')==follow
def _shape(c,v):
 a,b=(_expected()[:2] if v==28 else (_old('reference_cases'),_old('reference_case_followups')))
 return _same(c,'reference_cases',a) and _same(c,'reference_case_followups',b) and _maps(c,v)
def _foreign_clean(c):return not c.execute('PRAGMA foreign_key_check').fetchall()
def _validate(c):
 # 024–026 are an established pre-022 Database bootstrap baseline.  Apart
 # from that baseline, case markers must remain a closed dependency chain.
 if c.execute('SELECT 1 FROM schema_migrations WHERE version>28').fetchone():raise RuntimeError('migration28 higher marker drift')
 has22,has23,has27,has28=(_mark(c,n) for n in (22,23,27,28))
 if (not has22 and (has23 or has27 or has28)) or (has23 and not has22) or (has27 and (not has22 or not has23)) or (has28 and (not has22 or not has23 or not has27)):
  raise RuntimeError('migration28 marker prefix drift')
 if c.execute("SELECT 1 FROM sqlite_master WHERE name IN (%s)"%(','.join('?'*len((_STAGE_C,_STAGE_F)))),(_STAGE_C,_STAGE_F)).fetchone():raise RuntimeError('migration28 residual staging')
 if not has22:
  if c.execute("SELECT 1 FROM sqlite_master WHERE name IN (%s)"%(','.join('?'*len(_RESERVED))),_RESERVED).fetchone():raise RuntimeError('migration28 reserved object drift')
  return 'before_cases'
 if any(_trigger(c,n) for n in _TABLES):raise RuntimeError('migration28 trigger drift')
 children=_same(c,'reference_case_events',_child('reference_case_events')) and _same(c,'reference_case_attachments',_child('reference_case_attachments'))
 if has28:
  if not has23 or not has27 or not children or not _shape(c,28):raise RuntimeError('migration28 marked shape drift')
  return 'installed'
 if not has23:
  if c.execute("SELECT 1 FROM sqlite_master WHERE name IN (?,?)",('reference_case_followups','ix_reference_case_followups_case')).fetchone() or not children or not _same(c,'reference_cases',_old('reference_cases')) or _idx(c,'reference_cases')!=_CASE_IDX or _idx(c,'reference_case_events')!=_EVENT_IDX or _idx(c,'reference_case_attachments')!={}:raise RuntimeError('migration28 case drift')
  return 'before_followups'
 if not children or not _shape(c,27):raise RuntimeError('migration28 pre28 drift')
 return 'ready' if has27 else 'pre28'
def preflight_projection_human_review(c):
 if c.in_transaction:raise RuntimeError('migration28 preflight requires no caller transaction')
 c.execute('BEGIN IMMEDIATE')
 try:
  state=_validate(c)
  if c.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('migration28 foreign key drift')
  c.commit();return state
 except Exception:c.rollback();raise
def install_projection_human_review(c):
 if c.in_transaction:raise RuntimeError('migration28 requires no caller transaction')
 preflight_projection_human_review(c);fk=c.execute('PRAGMA foreign_keys').fetchone()[0]
 try:
  c.execute('PRAGMA foreign_keys=OFF');c.execute('BEGIN IMMEDIATE')
  # Public preflight releases its lock. Recheck staging and FK state after
  # this second lock acquisition, before either idempotent commit or rebuild.
  if not _foreign_clean(c):raise RuntimeError('migration28 in-lock foreign key drift')
  state=_validate(c)
  if state=='installed':c.commit();return
  if state!='ready':raise RuntimeError('migration28 requires exact schema27')
  e=_expected(_STAGE_C,_STAGE_F);c.execute(e[0]);c.execute(e[1])
  for src,dst,cols in (('reference_cases',_STAGE_C,_CASES),('reference_case_followups',_STAGE_F,_FOL)):
   names=','.join(cols);c.execute(f'INSERT INTO {dst}(rowid,{names}) SELECT rowid,{names} FROM {src} ORDER BY rowid');typed='rowid,'+names+','+','.join('typeof('+x+')' for x in cols)
   if c.execute(f'SELECT {typed} FROM {src} EXCEPT SELECT {typed} FROM {dst}').fetchone() or c.execute(f'SELECT {typed} FROM {dst} EXCEPT SELECT {typed} FROM {src}').fetchone():raise RuntimeError('migration28 copied values inconsistently')
  c.execute('DROP TABLE reference_case_followups');c.execute('DROP TABLE reference_cases');c.execute(f'ALTER TABLE {_STAGE_C} RENAME TO reference_cases');c.execute(f'ALTER TABLE {_STAGE_F} RENAME TO reference_case_followups')
  for x in _expected()[2:]:c.execute(x)
  if not _shape(c,28) or c.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('migration28 verification failed')
  c.execute("INSERT INTO schema_migrations VALUES(28,datetime('now'))");c.commit()
 except Exception:c.rollback();raise
 finally:c.execute('PRAGMA foreign_keys='+('ON' if fk else 'OFF'))
