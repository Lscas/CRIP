import pytest

from .test_reference_cases import _result, _followup_result
from .test_reference_results import _saved_run


def _qa_fixture(client,project,status='NEED_USER_INPUT'):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    question='What approved color applies to Finish key PT9?'
    result_id=_result(client.app.state.reference_results,run,question,status)
    return db,run,question,result_id


def test_reference_case_api_is_local_human_only_and_read_only_gets(client,project,monkeypatch):
    db,run,question,result_id=_qa_fixture(client,project)
    monkeypatch.setattr(client.app.state.gateway,'evidence_decision_v3',lambda *_a,**_k: (_ for _ in ()).throw(
        AssertionError('Reference case API must not call the model')))
    created=client.post(f'/api/projects/{project["id"]}/reference-cases',json={
        'run_id':run['id'],'question':question,'result_id':result_id,
        'note':'Need the field observation.','actor':'not-accepted'})
    assert created.status_code==422
    created=client.post(f'/api/projects/{project["id"]}/reference-cases',json={
        'run_id':run['id'],'question':question,'result_id':result_id,
        'note':'Need the field observation.'})
    assert created.status_code==201,created.text
    case=created.json();assert case['source']['status']=='NEED_USER_INPUT'
    assert case['history'][0]['actor']=='local-user'
    event_count=db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']
    listed=client.get(f'/api/projects/{project["id"]}/reference-cases',params={'status':'OPEN','run_id':run['id']})
    detail=client.get(f'/api/reference-cases/{case["case_id"]}')
    assert listed.status_code==detail.status_code==200
    assert listed.json()['items'][0]['case_id']==case['case_id'] and 'history' not in listed.json()['items'][0]
    assert detail.json()['history'][0]['note']=='Need the field observation.'
    assert db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']==event_count
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_reference_case_api_rejects_wrong_project_and_enforces_stale_resolve_reopen(client,project):
    _,run,question,result_id=_qa_fixture(client,project)
    other=client.post('/api/projects',json={'name':'Other reference case API project'}).json()
    wrong=client.post(f'/api/projects/{other["id"]}/reference-cases',json={
        'run_id':run['id'],'question':question,'result_id':result_id})
    assert wrong.status_code==404
    created=client.post(f'/api/projects/{project["id"]}/reference-cases',json={
        'run_id':run['id'],'question':question,'result_id':result_id}).json()
    case_id=created['case_id']
    no_resolution=client.post(f'/api/reference-cases/{case_id}/update',json={
        'expected_version':0,'status':'RESOLVED'})
    assert no_resolution.status_code==409
    resolved=client.post(f'/api/reference-cases/{case_id}/update',json={
        'expected_version':0,'status':'RESOLVED','resolution':'Confirmed in the field log.'})
    assert resolved.status_code==200 and resolved.json()['status']=='RESOLVED'
    stale=client.post(f'/api/reference-cases/{case_id}/update',json={
        'expected_version':0,'status':'OPEN'})
    assert stale.status_code==409
    reopened=client.post(f'/api/reference-cases/{case_id}/update',json={
        'expected_version':1,'status':'OPEN','note':'Reopened for a revised photo.'})
    assert reopened.status_code==200 and reopened.json()['resolution']==''
    assert reopened.json()['history'][-1]['before']['resolution']=='Confirmed in the field log.'
    strict=client.post(f'/api/reference-cases/{case_id}/update',json={
        'expected_version':'2','status':'OPEN'})
    assert strict.status_code==422


def test_reference_case_followup_api_links_saved_output_without_model(client,project,monkeypatch):
    db,origin,question,source=_qa_fixture(client,project)
    document=db.one('SELECT id,name FROM documents WHERE project_id=?',(project['id'],))
    created=client.post(f'/api/projects/{project["id"]}/reference-cases',json={
        'run_id':origin['id'],'question':question,'result_id':source,'attachments':[document['id']]}).json()
    document_payload={'document_id':document['id'],'name':document['name']}
    followup_run,followup_result=_followup_result(db,client,project,document_payload,question)
    monkeypatch.setattr(client.app.state.gateway,'evidence_decision_v3',lambda *_a,**_k: pytest.fail('follow-up link called model'))
    linked=client.post(f'/api/reference-cases/{created["case_id"]}/follow-up-results',json={
        'expected_version':0,'run_id':followup_run['id'],'result_id':followup_result,
        'supplemental_document_ids':[document['id']],'note':'Linked saved field result.'})
    assert linked.status_code==200,linked.text
    assert linked.json()['status']=='IN_REVIEW' and len(linked.json()['followups'])==1
    stale=client.post(f'/api/reference-cases/{created["case_id"]}/follow-up-results',json={
        'expected_version':0,'run_id':followup_run['id'],'result_id':followup_result,
        'supplemental_document_ids':[document['id']],'note':'different'})
    assert stale.status_code==409
    before=db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']
    assert client.get(f'/api/reference-cases/{created["case_id"]}').status_code==200
    assert db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']==before
