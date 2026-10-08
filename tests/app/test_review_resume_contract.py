"""Backend-advertised resume eligibility and POST share the same safety checks."""
import pytest

from .conftest import upload


def _finished_run(client,project):
    upload(client,project['id'],'synthetic-resume.txt',
           b'DEMO_MATERIAL|P-1|Pump|-|location=Synthetic Building A|quantity=2')
    run=client.post(f'/api/projects/{project["id"]}/analysis-runs').json()
    client.app.state.runner.process(run['id'])
    return run['id'],client.app.state.db,client.app.state.runner


def test_local_publication_failure_advertises_resume_and_reuses_extraction(client,project,monkeypatch):
    rid,db,runner=_finished_run(client,project)
    before=db.all('SELECT id,extraction FROM evidence WHERE run_id=?',(rid,))
    db.execute("UPDATE runs SET status='FAILED',stage='生成可审核记录与设计差异' WHERE id=?",(rid,))
    assert client.get(f'/api/analysis-runs/{rid}').json()['can_resume'] is True
    assert db.all('SELECT id,extraction FROM evidence WHERE run_id=?',(rid,))==before
    monkeypatch.setattr(runner.gateway,'extract_many',lambda *a,**kw:pytest.fail('Repeated completed extraction'))
    monkeypatch.setattr(runner.gateway,'extract',lambda *a,**kw:pytest.fail('Repeated completed extraction'))
    response=client.post(f'/api/analysis-runs/{rid}/resume')
    assert response.status_code==200,response.text
    assert response.json()['can_resume'] is False  # now queued
    runner.process(rid)
    assert runner.get(rid)['status']=='PARTIAL'
    assert db.all('SELECT id,extraction FROM evidence WHERE run_id=?',(rid,))==before


@pytest.mark.parametrize('blocker',['stage','pending','deadline','provider','active','unresolved'])
def test_advertised_resume_remains_closed_at_safety_boundaries(client,project,blocker):
    rid,db,_=_finished_run(client,project)
    db.execute("UPDATE runs SET status='FAILED',stage='生成可审核记录与设计差异' WHERE id=?",(rid,))
    if blocker=='stage':db.execute("UPDATE runs SET stage='解析文件' WHERE id=?",(rid,))
    elif blocker=='pending':db.execute("UPDATE evidence SET status='PENDING' WHERE run_id=?",(rid,))
    elif blocker=='deadline':db.execute('UPDATE runs SET deadline_epoch=0 WHERE id=?',(rid,))
    elif blocker=='provider':db.execute("UPDATE runs SET provider='deepseek' WHERE id=?",(rid,))
    elif blocker=='active':
        response=client.post(f'/api/projects/{project["id"]}/analysis-runs')
        assert response.status_code==202,response.text
    else:
        db.execute('''INSERT INTO model_calls(id,project_id,run_id,task_key,model,state,reserved_units,
                      input_rate,output_rate,request_hash,created_at,updated_at)
                      VALUES(?,?,?,?,?,'UNKNOWN',1,'0','0','synthetic','synthetic','synthetic')''',
                   ('synthetic-call',project['id'],rid,'synthetic-task','synthetic-model'))
    payload=client.get(f'/api/analysis-runs/{rid}').json()
    assert payload['can_resume'] is False
    assert payload['resume_blocked_reason']
    assert client.post(f'/api/analysis-runs/{rid}/resume').status_code==409


def test_get_resume_eligibility_never_creates_recovery_authorization(client,project):
    rid,db,_=_finished_run(client,project)
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?",(rid,))
    before=db.all('SELECT * FROM call_reconciliation_events')
    for _ in range(2):
        assert client.get(f'/api/analysis-runs/{rid}').json()['can_resume'] is True
    assert db.all('SELECT * FROM call_reconciliation_events')==before
    assert db.one('SELECT status FROM runs WHERE id=?',(rid,))['status']=='PAUSED'


def test_post_rechecks_eligibility_after_get_status_drift(client,project):
    rid,db,_=_finished_run(client,project)
    db.execute("UPDATE runs SET status='PAUSED' WHERE id=?",(rid,))
    assert client.get(f'/api/analysis-runs/{rid}').json()['can_resume'] is True
    db.execute('UPDATE runs SET deadline_epoch=0 WHERE id=?',(rid,))
    assert client.post(f'/api/analysis-runs/{rid}/resume').status_code==409
    assert db.one('SELECT status FROM runs WHERE id=?',(rid,))['status']=='PAUSED'
