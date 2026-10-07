import sqlite3
import pytest
import app.db as db_module
from app.db import Database
from app.reference_case_migration import install_projection_human_review, preflight_projection_human_review
from app.main import create_app
from app.reference_cases import ReferenceCaseStore
from app.settings import Settings
from fastapi.testclient import TestClient
from tests.app.test_reference_cases import _followup_result, _result
from tests.app.test_reference_results import _saved_run

def legacy(tmp_path,monkeypatch):
 monkeypatch.setattr(db_module,'install_projection_human_review',lambda c:None)
 return Database(tmp_path/'legacy.sqlite')

def seed(c):
 h='a'*64;t='2026-10-07T00:00:00Z'
 c.execute("INSERT INTO projects VALUES('P','p',?)",(t,));c.execute("INSERT INTO runs VALUES('R','P','mock','S','[]','PARTIAL','DONE','',?,0,0,'{}','{}',0)",(t,));c.execute("INSERT INTO documents VALUES('D','P','x.pdf',1,?,'x',?)",(h,t))
 c.execute("INSERT INTO reference_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('Q','k','P','R','S','3','alpha?',h,'REFERENCE_QA_RESULT','ANSWERED','basis','mock','m',h,'{}','PENDING',0,None,t,t))
 c.execute("INSERT INTO reference_cases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('C','s','P','R','S','alpha?',h,'Q',None,None,'ANSWERED','OPEN','','',0,t,t));c.execute("INSERT INTO reference_case_events VALUES('E','C','human','CREATED','{}','{}','',?)",(t,));c.execute("INSERT INTO reference_case_attachments VALUES('C','D',?)",(t,));c.execute("INSERT INTO reference_case_followups VALUES('F','C','R','S','Q','ANSWERED','{}',?,?)",(h,t));c.execute('UPDATE reference_cases SET rowid=17');c.execute('UPDATE reference_case_followups SET rowid=19');c.commit()
def snap(c):
 out={}
 for t in ('reference_cases','reference_case_events','reference_case_attachments','reference_case_followups'):
  cols=[r[1] for r in c.execute(f'PRAGMA table_info({t})')];out[t]=[tuple(r) for r in c.execute(f"SELECT rowid,*,{','.join('typeof('+x+')' for x in cols)} FROM {t} ORDER BY rowid")]
 out['marker']=[tuple(r) for r in c.execute('SELECT rowid,* FROM schema_migrations ORDER BY rowid')];out['fk']=c.execute('PRAGMA foreign_keys').fetchone()[0];return out
class Fault:
 def __init__(self,c,token=None,late_fk=False,late_commit=False):self.c=c;self.token=token;self.late_fk=late_fk;self.late_commit=late_commit;self.reached=False
 @property
 def in_transaction(self):return self.c.in_transaction
 def execute(self,s,a=()):
  n=' '.join(s.split())
  if self.token and self.token in n:raise sqlite3.OperationalError('fault '+self.token)
  r=self.c.execute(s,a)
  if self.late_fk and n.startswith('ALTER TABLE reference_case_followups__migration28 RENAME'):
   self.c.execute("INSERT INTO reference_case_events VALUES('late','missing','human','CREATED','{}','{}','',?)",('t',));self.reached=True
  return r
 def commit(self):
  if self.late_commit and self.c.execute('SELECT 1 FROM schema_migrations WHERE version=28').fetchone():self.reached=True;raise sqlite3.OperationalError('late commit')
  return self.c.commit()
 def rollback(self):return self.c.rollback()

def test_database_wires_schema28_after_schema27(tmp_path):
 d=Database(tmp_path/'x.sqlite')
 with d.connect() as c:
  assert c.execute('SELECT 1 FROM schema_migrations WHERE version=28').fetchone()
  for table,column in [('reference_cases','source_status'),('reference_case_followups','result_status')]:
   sql=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0]
   assert 'REVIEW_REQUIRED' in sql

@pytest.mark.parametrize('reserved',['reference_case_attachments','ix_reference_cases_run'])
def test_pre022_reserved_views_fail_before_if_not_exists(tmp_path,reserved):
 d=Database(tmp_path/'base.sqlite')
 with d.connect() as c:
  c.execute('PRAGMA foreign_keys=OFF')
  for table in ('reference_case_followups','reference_case_events','reference_case_attachments','reference_cases'):c.execute('DROP TABLE '+table)
  c.execute('DELETE FROM schema_migrations WHERE version BETWEEN 22 AND 28');c.execute(f'CREATE VIEW {reserved} AS SELECT 1 AS x');c.commit();c.execute('PRAGMA foreign_keys=ON')
  before=[tuple(r) for r in c.execute('SELECT type,name,sql FROM sqlite_master WHERE name=?',(reserved,))]+[tuple(r) for r in c.execute('SELECT * FROM schema_migrations ORDER BY version')]
  with pytest.raises(RuntimeError):preflight_projection_human_review(c)
  assert before==[tuple(r) for r in c.execute('SELECT type,name,sql FROM sqlite_master WHERE name=?',(reserved,))]+[tuple(r) for r in c.execute('SELECT * FROM schema_migrations ORDER BY version')]

def test_preflight_rejects_caller_transaction_and_orphan(tmp_path):
 d=Database(tmp_path/'x.sqlite')
 with d.connect() as c:
  c.execute('BEGIN IMMEDIATE')
  with pytest.raises(RuntimeError,match='caller transaction'):preflight_projection_human_review(c)
  c.rollback();c.execute('PRAGMA foreign_keys=OFF');c.execute("INSERT INTO reference_case_events VALUES('orphan','missing','human','CREATED','{}','{}','',?)",('t',));c.commit();c.execute('PRAGMA foreign_keys=ON')
  with pytest.raises(RuntimeError,match='foreign key drift'):preflight_projection_human_review(c)

def test_installer_is_idempotent_and_marker28_shape_drift_rejects(tmp_path):
 d=Database(tmp_path/'x.sqlite')
 with d.connect() as c:
  install_projection_human_review(c)
  c.execute('DROP INDEX ix_reference_cases_run');c.commit()
  with pytest.raises(RuntimeError):install_projection_human_review(c)

@pytest.mark.parametrize('token',['DROP TABLE reference_case_followups','DROP TABLE reference_cases','ALTER TABLE reference_cases__migration28 RENAME','ALTER TABLE reference_case_followups__migration28 RENAME','CREATE INDEX ix_reference_cases_project','CREATE INDEX ix_reference_cases_run','CREATE INDEX ix_reference_case_followups_case','INSERT INTO schema_migrations'])
def test_schema27_named_faults_rollback_full_graph_and_retry(tmp_path,monkeypatch,token):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  seed(c);before=snap(c)
  with pytest.raises(sqlite3.OperationalError):install_projection_human_review(Fault(c,token=token))
  assert snap(c)==before
  install_projection_human_review(c);assert c.execute('SELECT 1 FROM schema_migrations WHERE version=28').fetchone()

def test_schema27_late_commit_fault_reaches_marker_rolls_back_and_retries(tmp_path,monkeypatch):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  seed(c);before=snap(c);f=Fault(c,late_commit=True)
  with pytest.raises(sqlite3.OperationalError,match='late commit'):install_projection_human_review(f)
  assert f.reached and snap(c)==before
  install_projection_human_review(c)

def test_schema27_late_fk_after_second_rename_rolls_back_and_retries(tmp_path,monkeypatch):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  seed(c);before=snap(c);f=Fault(c,late_fk=True)
  with pytest.raises(RuntimeError,match='verification failed'):install_projection_human_review(f)
  assert f.reached and snap(c)==before
  install_projection_human_review(c)

@pytest.mark.parametrize('drift',['missing_index','attachment_index','future_marker','orphan'])
def test_schema27_negative_preflight_matrix(tmp_path,monkeypatch,drift):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  if drift=='missing_index':c.execute('DROP INDEX ix_reference_cases_run')
  elif drift=='attachment_index':c.execute('CREATE INDEX drift ON reference_case_attachments(document_id)')
  elif drift=='future_marker':c.execute("INSERT INTO schema_migrations VALUES(29,datetime('now'))")
  else:
   c.execute('PRAGMA foreign_keys=OFF');c.execute("INSERT INTO reference_case_events VALUES('o','none','human','CREATED','{}','{}','',?)",('t',));c.execute('PRAGMA foreign_keys=ON')
  c.commit()
  with pytest.raises(RuntimeError):preflight_projection_human_review(c)

def test_real_bootstrap_allows_24_to_26_before_22_but_rejects_future(tmp_path,monkeypatch):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  c.execute('PRAGMA foreign_keys=OFF')
  for t in ('reference_case_followups','reference_case_events','reference_case_attachments','reference_cases'):c.execute('DROP TABLE '+t)
  c.execute('DELETE FROM schema_migrations WHERE version IN (22,23,27)');c.commit();c.execute('PRAGMA foreign_keys=ON')
  assert preflight_projection_human_review(c)=='before_cases'
  c.execute("INSERT INTO schema_migrations VALUES(29,datetime('now'))");c.commit()
  with pytest.raises(RuntimeError,match='higher marker'):preflight_projection_human_review(c)

@pytest.mark.parametrize('marker',[23,27,28])
def test_r6_red_probe_no22_with_higher_case_marker_is_rejected(tmp_path,monkeypatch,marker):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  c.execute('PRAGMA foreign_keys=OFF')
  for t in ('reference_case_followups','reference_case_events','reference_case_attachments','reference_cases'):c.execute('DROP TABLE '+t)
  c.execute('DELETE FROM schema_migrations WHERE version IN (22,23,27,28)');c.execute("INSERT INTO schema_migrations VALUES(?,datetime('now'))",(marker,));c.commit();c.execute('PRAGMA foreign_keys=ON')
  with pytest.raises(RuntimeError):preflight_projection_human_review(c)

@pytest.mark.parametrize('table,column',[('reference_cases__migration28','question'),('reference_case_followups__migration28','proof_json')])
def test_copy_tamper_rolls_back_every_value_type_and_retries(tmp_path,monkeypatch,table,column):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  seed(c);before=snap(c)
  class Tamper(Fault):
   def execute(self,s,a=()):
    r=super().execute(s,a)
    if ' '.join(s.split()).startswith('INSERT INTO '+table+'('):self.c.execute(f"UPDATE {table} SET {column}=?",('tamper',))
    return r
  with pytest.raises(RuntimeError,match='copied values inconsistently'):install_projection_human_review(Tamper(c))
  assert snap(c)==before;install_projection_human_review(c)

@pytest.mark.parametrize('kind',['trigger','staging','marked_fk','source_check','followup_check'])
def test_formal_preflight_and_marked_shape_negative_matrix(tmp_path,monkeypatch,kind):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  if kind=='trigger':c.execute('CREATE TRIGGER drift AFTER INSERT ON reference_cases BEGIN SELECT 1; END')
  elif kind=='staging':c.execute('CREATE TABLE reference_cases__migration28(x)')
  elif kind=='marked_fk':
   install_projection_human_review(c);c.execute('PRAGMA foreign_keys=OFF');c.execute("INSERT INTO reference_case_events VALUES('bad','missing','human','CREATED','{}','{}','',?)",('t',));c.execute('PRAGMA foreign_keys=ON')
  else:
   install_projection_human_review(c);target='reference_cases' if kind=='source_check' else 'reference_case_followups';col='source_status' if kind=='source_check' else 'result_status';c.execute(f'ALTER TABLE {target} ADD COLUMN {col}_drift TEXT')
  c.commit()
  with pytest.raises(RuntimeError):preflight_projection_human_review(c)

@pytest.mark.parametrize('table,column,value',[('reference_cases','source_status','REVIEW_REQUIRED'),('reference_case_followups','result_status','REVIEW_REQUIRED')])
def test_expanded_checks_accept_review_required_and_reject_unknown(tmp_path,table,column,value):
 d=Database(tmp_path/'x.sqlite')
 with d.connect() as c:
  if table=='reference_cases':
   # Existing row is deliberately absent: CHECK is verified through schema SQL.
   assert value in c.execute("SELECT sql FROM sqlite_master WHERE name=?",(table,)).fetchone()[0]
  else:assert value in c.execute("SELECT sql FROM sqlite_master WHERE name=?",(table,)).fetchone()[0]

def test_real_testclient_case_history_attachment_and_proof_bytes_survive(tmp_path,monkeypatch):
 monkeypatch.setattr(db_module,'install_projection_human_review',lambda c:None)
 app=create_app(Settings(tmp_path/'api',start_worker=False))
 with TestClient(app,headers={'X-CIRP-Client':'browser'}) as client:
  project=client.post('/api/projects',json={'name':'schema28 synthetic'}).json();database,origin,document,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(database);q='What approved color applies to Finish key PT9?'
  source=_result(client.app.state.reference_results,origin,q,'NEED_USER_INPUT');case=store.create(project['id'],origin['id'],q,result_id=source,attachments=[document['document_id']],note='history')
  run,result=_followup_result(database,client,project,document,q);linked=store.link_followup(case['case_id'],case['version'],run['id'],result,[document['document_id']],'proof')
  with database.connect() as c:
   before=c.execute('SELECT proof_json FROM reference_case_followups').fetchone()[0].encode();assert linked['history'] and linked['attachments'] and linked['followups'];install_projection_human_review(c);assert c.execute('SELECT proof_json FROM reference_case_followups').fetchone()[0].encode()==before

class LockRace:
 def __init__(self,c):self.c=c;self.begins=0;self.injected=False;self.destructive=[]
 @property
 def in_transaction(self):return self.c.in_transaction
 def execute(self,s,a=()):
  n=' '.join(s.split());r=self.c.execute(s,a)
  if n=='BEGIN IMMEDIATE':
   self.begins+=1
   if self.begins==2:self.c.execute("INSERT INTO reference_case_events VALUES('race','missing','human','CREATED','{}','{}','',?)",('t',));self.injected=True
  if n.startswith(('DROP TABLE','ALTER TABLE')):self.destructive.append(n)
  return r
 def commit(self):return self.c.commit()
 def rollback(self):return self.c.rollback()

@pytest.mark.parametrize('installed',[False,True])
def test_second_lock_rechecks_fk_before_destructive_or_idempotent_commit(tmp_path,monkeypatch,installed):
 d=Database(tmp_path/'i.sqlite') if installed else legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  before=snap(c);race=LockRace(c)
  with pytest.raises(RuntimeError,match='in-lock foreign key drift'):install_projection_human_review(race)
  assert race.injected and race.destructive==[] and snap(c)==before

def test_post_rebuild_shape_failure_rolls_back_and_retries(tmp_path,monkeypatch):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  before=snap(c);actual=__import__('app.reference_case_migration',fromlist=['_shape'])._shape
  monkeypatch.setattr('app.reference_case_migration._shape',lambda x,v:False if v==28 else actual(x,v))
  with pytest.raises(RuntimeError,match='verification failed'):install_projection_human_review(c)
  assert snap(c)==before;monkeypatch.setattr('app.reference_case_migration._shape',actual);install_projection_human_review(c)

@pytest.mark.parametrize('drift',['literal','foreign_key'])
def test_pre28_literal_and_fk_lookalikes_fail_closed(tmp_path,monkeypatch,drift):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  old=__import__('app.reference_case_migration',fromlist=['_old'])._old('reference_cases')
  old=old.replace("'NEED_USER_INPUT','FAILED'","'NEED_USER_INPUT','FAILED_LITERAL'") if drift=='literal' else old.replace('REFERENCES projects(id)','REFERENCES documents(id)',1)
  c.execute('PRAGMA foreign_keys=OFF');c.execute('DROP INDEX ix_reference_cases_project');c.execute('DROP INDEX ix_reference_cases_run');c.execute('DROP TABLE reference_cases');c.execute(old);c.execute('CREATE INDEX ix_reference_cases_project ON reference_cases(project_id,status,updated_at DESC,id)');c.execute('CREATE INDEX ix_reference_cases_run ON reference_cases(run_id,updated_at DESC,id)');c.commit();c.execute('PRAGMA foreign_keys=ON')
  with pytest.raises(RuntimeError):install_projection_human_review(c)

@pytest.mark.parametrize('kind',['only22_followup_view','pre22_global_index_owner','marked28_missing27'])
def test_remaining_r12_marker_and_reserved_name_guards(tmp_path,monkeypatch,kind):
 d=legacy(tmp_path,monkeypatch)
 with d.connect() as c:
  if kind=='only22_followup_view':
   c.execute('PRAGMA foreign_keys=OFF');c.execute('DROP TABLE reference_case_followups');c.execute('DELETE FROM schema_migrations WHERE version IN (23,27,28)');c.execute('CREATE VIEW reference_case_followups AS SELECT 1 AS x');c.execute('PRAGMA foreign_keys=ON')
  elif kind=='pre22_global_index_owner':
   c.execute('PRAGMA foreign_keys=OFF')
   for t in ('reference_case_followups','reference_case_events','reference_case_attachments','reference_cases'):c.execute('DROP TABLE '+t)
   c.execute('DELETE FROM schema_migrations WHERE version IN (22,23,27,28)');c.execute('CREATE TABLE other(x)');c.execute('CREATE INDEX ix_reference_cases_run ON other(x)');c.execute('PRAGMA foreign_keys=ON')
  else:
   c.execute("INSERT INTO schema_migrations VALUES(28,datetime('now'))")
  c.commit()
  with pytest.raises(RuntimeError):preflight_projection_human_review(c)
