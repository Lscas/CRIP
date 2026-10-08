"""Local candidate schema failures are visible without discarding valid siblings."""
from copy import deepcopy
import json

import pytest

from app import assemble
from app.db import dumps
from .conftest import upload


def _run(client,project):
    upload(client,project['id'],'synthetic-publication.txt',
           b'DEMO_MATERIAL|P-1|Pump|-|quantity=2\nDEMO_MATERIAL|V-1|Isolation valve|-|quantity=1')
    response=client.post(f'/api/projects/{project["id"]}/analysis-runs')
    assert response.status_code==202,response.text
    return response.json()['id'],client.app.state.db,client.app.state.runner


def _fault(monkeypatch,*,scope=False,business=False):
    original=assemble.material
    def faulty(atom,*args,**kwargs):
        candidate=original(atom,*args,**kwargs)
        if atom['subject']=='P-1':
            if scope:candidate['evidence_ids']=['EV-not-in-this-run']
            elif business:candidate['design_properties']=[{'name':'rating','value':'100','unit':None,'evidence_ids':[]}]
            else:candidate['name']=''
        return candidate
    monkeypatch.setattr(assemble,'material',faulty)
    return original


def test_bad_candidate_is_locatable_and_valid_sibling_still_publishes(client,project,monkeypatch):
    rid,db,runner=_run(client,project)
    original=_fault(monkeypatch)
    runner.process(rid)
    run=client.get(f'/api/analysis-runs/{rid}').json()
    assert run['status']=='PARTIAL',run['message']
    records=client.get(f'/api/analysis-runs/{rid}/records?kind=MATERIAL').json()
    assert len(records)==1
    assert records[0]['record']['candidate']['entity_ids']==['V-1']
    issues=run['coverage']['publication_issues']
    assert len(issues)==1 and issues[0]['kind']=='MATERIAL'
    assert issues[0]['candidate_key'] and issues[0]['issue_id']
    assert issues[0]['instance_path']=='/name' and issues[0]['validator']=='minLength'
    assert issues[0]['schema_path'].endswith('/name/minLength')
    assert issues[0]['evidence_ids']
    assert not {'message','instance','raw_text','candidate'}.intersection(issues[0])
    exported=client.get(f'/api/analysis-runs/{rid}/exports/json')
    assert exported.status_code==200 and 'Incomplete publication: 1' in exported.text
    assert all(client.get(f'/api/analysis-runs/{rid}/evidence/{eid}').status_code==200 for eid in issues[0]['evidence_ids'])
    saved=db.all('SELECT id,extraction FROM evidence WHERE run_id=?',(rid,))
    assert any('P-1' in row['extraction'] for row in saved)
    runner.update_coverage(rid)
    assert runner.get(rid)['coverage']['publication_issues']==issues
    monkeypatch.setattr(assemble,'material',original)
    runner.publish(runner.get(rid))
    runner.update_coverage(rid)
    assert runner.get(rid)['coverage']['publication_issues']==[]
    assert len(db.all("SELECT id FROM records WHERE run_id=? AND kind='MATERIAL'",(rid,)))==2
    assert db.all('SELECT id,extraction FROM evidence WHERE run_id=?',(rid,))==saved


@pytest.mark.parametrize('violation',['scope','business'])
def test_non_schema_safety_failure_is_not_quarantined(client,project,monkeypatch,violation):
    rid,db,runner=_run(client,project)
    _fault(monkeypatch,scope=violation=='scope',business=violation=='business')
    runner.process(rid)
    assert runner.get(rid)['status']=='FAILED'
    assert db.all('SELECT id FROM records WHERE run_id=?',(rid,))==[]
    assert runner.get(rid)['coverage'].get('publication_issues',[])==[]


def test_database_failure_rolls_back_records_and_issue_snapshot(client,project,monkeypatch):
    rid,db,runner=_run(client,project)
    _fault(monkeypatch)
    db.execute("CREATE TRIGGER synthetic_publication_failure BEFORE INSERT ON records BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    runner.process(rid)
    assert runner.get(rid)['status']=='FAILED'
    assert db.all('SELECT id FROM records WHERE run_id=?',(rid,))==[]
    assert runner.get(rid)['coverage'].get('publication_issues',[])==[]


@pytest.mark.parametrize('action',['ACCEPTED','EDITED'])
def test_unpublished_successor_preserves_but_blocks_historical_review(client,project,monkeypatch,action):
    rid,db,runner=_run(client,project)
    runner.process(rid)
    items=client.get(f'/api/analysis-runs/{rid}/records?kind=MATERIAL').json()
    pump=next(item for item in items if item['record']['candidate']['entity_ids']==['P-1'])
    record_id=pump['record']['meta']['record_id']
    request={'action':action,'expected_version':pump['review_version'],'note':'Synthetic engineer review'}
    if action=='EDITED':
        request['candidate']=deepcopy(pump['record']['candidate'])
        request['candidate']['name']='Engineer checked pump'
    response=client.post(f'/api/records/{record_id}/review',json=request)
    assert response.status_code==200,response.text
    before=db.one('SELECT envelope,review_version FROM records WHERE id=?',(record_id,))
    history=db.all('SELECT * FROM review_events WHERE record_id=?',(record_id,))
    original=_fault(monkeypatch)
    runner.publish(runner.get(rid))
    assert db.one('SELECT envelope,review_version FROM records WHERE id=?',(record_id,))==before
    assert db.all('SELECT * FROM review_events WHERE record_id=?',(record_id,))==history
    for endpoint in (f'/api/analysis-runs/{rid}/records',f'/api/analysis-runs/{rid}/record-summaries'):
        data=client.get(endpoint).json()
        rows=data['items'] if isinstance(data,dict) else data
        item=next(item for item in rows if item['record']['meta']['record_id']==record_id)
        assert item['publication_blocked'] is True
        assert item['verification']['status']=='STALE'
    detail=client.get(f'/api/records/{record_id}').json()
    assert detail['publication_blocked'] is True and detail['publication_issues'][0]['blocked_record_id']==record_id
    assert detail['verification']['status']=='STALE'
    version=before['review_version']
    assert client.post(f'/api/records/{record_id}/review',json={
        'action':'ACCEPTED','expected_version':version}).status_code==409
    for semantic in (False,True):
        assert client.post(f'/api/records/{record_id}/verification',json={
            'semantic':semantic,'expected_version':version}).status_code==409
    for fmt in ('json','xlsx'):
        for reviewed in ('true','false'):
            assert client.get(f'/api/analysis-runs/{rid}/exports/{fmt}?reviewed_only={reviewed}').status_code==409
    with pytest.raises(Exception,match='Current candidate was not published'):
        runner.verifier.refresh(record_id,allow_model=True)
    assert db.all('SELECT id FROM model_calls')==[]
    monkeypatch.setattr(assemble,'material',original)
    runner.publish(runner.get(rid))
    assert client.get(f'/api/records/{record_id}').json()['publication_blocked'] is False
    assert db.one('SELECT envelope,review_version FROM records WHERE id=?',(record_id,))==before
    assert client.get(f'/api/analysis-runs/{rid}/exports/json?reviewed_only=true').status_code==200


def test_issue_snapshot_keeps_old_state_on_ambiguous_legacy_match(client,project,monkeypatch):
    rid,db,runner=_run(client,project)
    runner.process(rid)
    row=db.one("SELECT * FROM records WHERE run_id=? AND json_extract(envelope,'$.candidate.entity_ids[0]')='P-1'",(rid,))
    envelope=json.loads(row['envelope'])
    db.execute('UPDATE records SET logical_key=? WHERE id=?',('legacy-one',row['id']))
    duplicate=deepcopy(envelope);duplicate['meta']['record_id']='REC-synthetic-duplicate'
    db.execute('INSERT INTO records(id,run_id,project_id,kind,envelope,logical_key) VALUES(?,?,?,?,?,?)',
               ('REC-synthetic-duplicate',rid,project['id'],'MATERIAL',dumps(duplicate),'legacy-two'))
    prior=db.all('SELECT id,envelope,review_version FROM records WHERE run_id=? ORDER BY id',(rid,))
    _fault(monkeypatch)
    from app.db import DomainError
    with pytest.raises(DomainError,match='multiple historical records'):
        runner.publish(runner.get(rid))
    assert db.all('SELECT id,envelope,review_version FROM records WHERE run_id=? ORDER BY id',(rid,))==prior
    assert runner.get(rid)['coverage']['publication_issues']==[]


def test_provider_pause_publishes_before_exposing_exportable_status(client,project,monkeypatch):
    rid,db,runner=_run(client,project)
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?",(rid,))
    seen=[]
    def publish(run):
        seen.append(runner.get(rid)['status'])
        assert client.get(f'/api/analysis-runs/{rid}/exports/json').status_code==409
    monkeypatch.setattr(runner,'publish',publish)
    runner._publish_before_provider_pause(runner.get(rid),RuntimeError('synthetic provider pause'))
    assert seen==['RUNNING'] and runner.get(rid)['status']=='PAUSED_PROVIDER'


def test_whole_publication_failure_marks_old_reads_stale_without_modifying_history(client,project):
    rid,db,runner=_run(client,project)
    runner.process(rid)
    row=db.one('SELECT id,envelope,review_version FROM records WHERE run_id=? ORDER BY id',(rid,))
    db.execute("UPDATE runs SET status='FAILED',stage='生成可审核记录与设计差异' WHERE id=?",(rid,))
    for endpoint in (f'/api/analysis-runs/{rid}/records',f'/api/analysis-runs/{rid}/record-summaries'):
        response=client.get(endpoint).json()
        rows=response['items'] if isinstance(response,dict) else response
        assert rows and all(item['publication_blocked'] for item in rows)
        assert all(item['verification']['status']=='STALE' for item in rows)
    assert client.get(f'/api/records/{row["id"]}').json()['publication_blocked']
    assert client.get(f'/api/records/{row["id"]}/verification').json()['status']=='STALE'
    assert db.one('SELECT id,envelope,review_version FROM records WHERE id=?',(row['id'],))==row
    assert client.get(f'/api/analysis-runs/{rid}/exports/json').status_code==409


def test_export_rechecks_active_run_when_caller_holds_an_old_run(client,project):
    from app.db import DomainError
    from app.exporter import collect
    rid,db,runner=_run(client,project)
    runner.process(rid)
    old_run=runner.get(rid)
    db.execute("UPDATE runs SET status='QUEUED' WHERE id=?",(rid,))
    with pytest.raises(DomainError,match='active analysis'):
        collect(db,old_run)


def test_export_uses_one_snapshot_during_concurrent_resume(client,project,monkeypatch):
    from contextlib import contextmanager
    from app.exporter import collect
    rid,db,runner=_run(client,project)
    runner.process(rid)
    run=runner.get(rid)
    original=db.connect
    changed=[]
    @contextmanager
    def connection(write=False):
        with original(write) as current:
            def trace(sql):
                if 'SELECT id,envelope,review_version FROM records' not in sql or changed:return
                with original(True) as writer:
                    writer.execute("UPDATE runs SET status='RUNNING',coverage=? WHERE id=?",
                                   (dumps({'publication_issues':[{'issue_id':'PUB-new','blocked_record_id':'REC-new'}]}),rid))
                    writer.execute('UPDATE records SET envelope=json_set(envelope,\'$.candidate.name\',\'Concurrent new value\') WHERE run_id=?',(rid,))
                changed.append(True)
            current.set_trace_callback(trace)
            yield current
    monkeypatch.setattr(db,'connect',connection)
    exported=collect(db,run)
    assert changed==[True]
    assert exported['run']['status']=='PARTIAL' and exported['run']['coverage']['publication_issues']==[]
    assert all(row['candidate']['name']!='Concurrent new value' for row in exported['records'])
    assert db.one('SELECT status FROM runs WHERE id=?',(rid,))['status']=='RUNNING'


def test_partial_issue_recovery_uses_public_resume_without_repeating_extraction(client,project,monkeypatch):
    rid,db,runner=_run(client,project)
    original=_fault(monkeypatch)
    runner.process(rid)
    run=client.get(f'/api/analysis-runs/{rid}').json()
    assert run['status']=='PARTIAL' and run['coverage']['publication_issues']
    assert run['can_resume'] is True
    saved=db.all('SELECT id,extraction FROM evidence WHERE run_id=? ORDER BY id',(rid,))
    monkeypatch.setattr(assemble,'material',original)
    monkeypatch.setattr(runner.gateway,'extract',lambda *a,**kw:pytest.fail('Repeated completed extraction'))
    monkeypatch.setattr(runner.gateway,'extract_many',lambda *a,**kw:pytest.fail('Repeated completed extraction'))
    response=client.post(f'/api/analysis-runs/{rid}/resume')
    assert response.status_code==200,response.text
    runner.process(rid)
    recovered=client.get(f'/api/analysis-runs/{rid}').json()
    assert recovered['status']=='PARTIAL' and recovered['coverage']['publication_issues']==[]
    assert recovered['can_resume'] is False
    assert db.all('SELECT id,extraction FROM evidence WHERE run_id=? ORDER BY id',(rid,))==saved
    assert db.all('SELECT id FROM model_calls')==[]
    assert len(client.get(f'/api/analysis-runs/{rid}/records?kind=MATERIAL').json())==2


@pytest.mark.parametrize('blocker',['no_issue','wrong_stage','pending'])
def test_partial_resume_is_limited_to_completed_publication_issues(client,project,monkeypatch,blocker):
    rid,db,runner=_run(client,project)
    if blocker!='no_issue':_fault(monkeypatch)
    runner.process(rid)
    if blocker=='wrong_stage':db.execute("UPDATE runs SET stage='解析文件' WHERE id=?",(rid,))
    elif blocker=='pending':db.execute("UPDATE evidence SET status='PENDING' WHERE run_id=?",(rid,))
    assert client.get(f'/api/analysis-runs/{rid}').json()['can_resume'] is False
    assert client.post(f'/api/analysis-runs/{rid}/resume').status_code==409
