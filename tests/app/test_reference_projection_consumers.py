"""Formal consumer boundaries; synthetic PDFs, temp SQLite and MockTransport only."""
from __future__ import annotations

import json

import pytest

from app.db import DomainError,now,uid
from app.reference_text_profiles import profile
from .test_projection_loop2_gateway import QUESTION,_need,_setup
from .test_reference_results import _public_result


_KIND_ERROR='Reference result kind and payload are inconsistent.'
_TABLES=(
    'reference_results','reference_result_citations','reference_result_review_events',
    'reference_evaluations','reference_evaluation_items','reference_evaluation_failures',
    'reference_evaluation_jobs','reference_evaluation_adjudications',
    'reference_evaluation_adjudication_events','reference_cases','reference_case_events',
    'reference_case_attachments','reference_case_followups','model_calls',
)


@pytest.fixture
def projection(client,project,tmp_path):
    db,run,gateway,loop,sent=_setup(
        client,project,tmp_path,lambda envelope:_need(envelope,'alpha'))
    route=profile('FLASH_NONE')
    proof=loop.preview(run,QUESTION,route)['preview_proof']
    output=loop.ask(run,QUESTION,route,preview_proof=proof)
    saved=client.app.state.reference_results.save_projection(run,QUESTION,output,route)
    assert saved['status']=='CANNOT_ANSWER'
    try:yield db,run,saved,sent
    finally:gateway.close()


def _snapshot(db):
    return {table:db.all(f'SELECT rowid,* FROM {table} ORDER BY rowid') for table in _TABLES}


def _evaluation(client,project,run):
    evaluations=client.app.state.reference_evaluations
    return evaluations.create(project['id'],run['id'],'Synthetic boundary',[QUESTION],
                              evaluations.profile(client.app.state.settings))


def _legacy_result(client,run):
    value=_public_result(run,QUESTION)
    value.update(status='CANNOT_ANSWER',answer='',claims=[],calculations=[],
                 missing=['Synthetic old non-answer.'])
    return client.app.state.reference_results.save(run,QUESTION,value,'mock','mock-no-network')


def _downgrade(db,saved,enabled):
    if enabled:
        db.execute("UPDATE reference_results SET result_kind='REFERENCE_QA_RESULT' WHERE id=?",
                   (saved['result_id'],))


def _reject(action):
    with pytest.raises(DomainError,match=_KIND_ERROR.replace('.','\\.')) as rejected:
        action()
    assert rejected.value.code==409


def _conflict(response):
    assert response.status_code==409,response.text
    assert _KIND_ERROR in response.text


def test_saved_projection_is_typed_in_existing_read_api_with_no_mutations(client,project,projection):
    db,run,saved,sent=projection
    before=_snapshot(db);sent_before=len(sent)
    detail=client.get(f"/api/reference-results/{saved['result_id']}")
    listing=client.get(f"/api/projects/{project['id']}/reference-results")
    exported=client.get(f"/api/projects/{project['id']}/reference-results/export.json")
    history=client.get(f"/api/reference-results/{saved['result_id']}/history")
    assert detail.status_code==listing.status_code==exported.status_code==history.status_code==200
    for value in (detail.json(),listing.json()['items'][0],exported.json()['results'][0]):
        assert value['result_kind']=='PROJECTION_LOOP_OUTCOME'
        assert value['status']=='CANNOT_ANSWER' and value['result']['answer']==''
        assert value['citations']==[]
        assert value['review']=={'status':'NOT_APPLICABLE','version':0,'event_id':None}
    assert history.json()==[]
    assert client.get(f'/api/reference-results/{saved["result_id"]}').json()==detail.json()
    assert _snapshot(db)==before and len(sent)==sent_before


@pytest.mark.parametrize('downgraded',[False,True])
def test_projection_direct_case_is_allowed_but_evaluation_remains_legacy_only(
        client,project,projection,downgraded):
    db,run,saved,sent=projection
    evaluation=_evaluation(client,project,run)
    item=evaluation['items'][0]
    _downgrade(db,saved,downgraded)
    before=_snapshot(db);sent_before=len(sent)
    _reject(lambda:client.app.state.reference_evaluations.attach(
        evaluation['evaluation_id'],item['item_id'],saved['result_id']))
    created=client.post(f"/api/projects/{project['id']}/reference-cases",json={
        'run_id':run['id'],'question':QUESTION,'result_id':saved['result_id']})
    if downgraded:
        _conflict(created)
        assert _snapshot(db)==before
    else:
        assert created.status_code==201,created.text
        assert created.json()['source']['result_kind']=='PROJECTION_LOOP_OUTCOME'
        before=_snapshot(db)
    db.execute('UPDATE reference_evaluation_items SET result_id=? WHERE id=?',
               (saved['result_id'],item['item_id']))
    before=_snapshot(db)
    _conflict(client.post(f"/api/projects/{project['id']}/reference-cases",json={
        'run_id':run['id'],'question':QUESTION,'evaluation_id':evaluation['evaluation_id'],
        'question_id':item['item_id']}))
    assert _snapshot(db)==before and len(sent)==sent_before


@pytest.mark.parametrize('downgraded',[False,True])
def test_corrupted_evaluation_link_cannot_be_summarized_or_adjudicated(
        client,project,projection,downgraded):
    db,run,saved,sent=projection
    evaluation=_evaluation(client,project,run);other=_evaluation(client,project,run)
    eid=evaluation['evaluation_id'];item=evaluation['items'][0]
    legacy=_legacy_result(client,run)
    client.app.state.reference_evaluations.attach(eid,item['item_id'],legacy['result_id'])
    history_path=f"/api/reference-evaluations/{eid}/items/{item['item_id']}/adjudication-history"
    reviewed=client.post(f"/api/reference-evaluations/{eid}/items/{item['item_id']}/adjudication",json={
        'verdict':'UNUSABLE','unsupported_claim':False,'expected_version':0,
        'note':'Synthetic old non-answer review.'})
    assert reviewed.status_code==200,reviewed.text
    old_history=client.get(history_path)
    assert old_history.status_code==200 and old_history.json()['total']==1
    _downgrade(db,saved,downgraded)
    db.execute('UPDATE reference_evaluation_items SET result_id=? WHERE id=?',
               (saved['result_id'],item['item_id']))
    before=_snapshot(db);sent_before=len(sent)
    for path in (
        f'/api/reference-evaluations/{eid}',
        f"/api/projects/{project['id']}/reference-evaluations",
        f'/api/reference-evaluations/{eid}/readiness',
        f'/api/reference-evaluations/{eid}/scorecard',
        history_path,
        f"/api/projects/{project['id']}/reference-evaluations/compare?baseline_evaluation_id={eid}&candidate_evaluation_id={other['evaluation_id']}",
    ):
        _conflict(client.get(path))
    _conflict(client.post(f"/api/reference-evaluations/{eid}/items/{item['item_id']}/adjudication",json={
        'verdict':'PARTIAL','unsupported_claim':False,'expected_version':0,'note':'Synthetic review must not save.'}))
    assert _snapshot(db)==before and len(sent)==sent_before


@pytest.mark.parametrize('downgraded',[False,True])
@pytest.mark.parametrize('link',['case','evaluation','followup'])
def test_existing_case_projection_link_fails_read_and_update_without_writing(
        client,project,projection,downgraded,link):
    db,run,saved,sent=projection
    legacy=_legacy_result(client,run)
    evaluation=_evaluation(client,project,run)
    eid=evaluation['evaluation_id'];item=evaluation['items'][0]
    client.app.state.reference_evaluations.attach(eid,item['item_id'],legacy['result_id'])
    cases=client.app.state.reference_cases
    case=cases.create(project['id'],run['id'],QUESTION,result_id=legacy['result_id'],
                      evaluation_id=eid,question_id=item['item_id'])
    case_id=case['case_id']
    _downgrade(db,saved,downgraded)
    if link=='case':
        db.execute('UPDATE reference_cases SET result_id=? WHERE id=?',(saved['result_id'],case_id))
    elif link=='evaluation':
        db.execute('UPDATE reference_evaluation_items SET result_id=? WHERE id=?',
                   (saved['result_id'],item['item_id']))
    else:
        db.execute('''INSERT INTO reference_case_followups(
            id,case_id,run_id,snapshot_id,result_id,result_status,proof_json,payload_hash,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',(uid('QACASEFOLLOW'),case_id,run['id'],run['snapshot_id'],
            saved['result_id'],saved['status'],'{}','f'*64,now()))
    before=_snapshot(db);sent_before=len(sent)
    if link=='followup':
        # A projection follow-up now has a separate proof contract.  This raw
        # injected row lacks that proof/event anchor and must still be rejected.
        for response in (
            client.get(f'/api/reference-cases/{case_id}'),
            client.get(f"/api/projects/{project['id']}/reference-cases"),
            client.post(f'/api/reference-cases/{case_id}/update',json={
                'expected_version':case['version'],'note':'Must not append a human event.'}),
        ):
            assert response.status_code==409,response.text
    else:
        _conflict(client.get(f'/api/reference-cases/{case_id}'))
        _conflict(client.get(f"/api/projects/{project['id']}/reference-cases"))
        _conflict(client.post(f'/api/reference-cases/{case_id}/update',json={
            'expected_version':case['version'],'note':'Must not append a human event.'}))
    assert _snapshot(db)==before and len(sent)==sent_before


@pytest.mark.parametrize('downgraded',[False,True])
def test_new_followup_result_refuses_projection_before_legacy_receipt_work(
        client,project,projection,downgraded):
    db,run,saved,sent=projection
    original=client.app.state.runner.create(project['id'],analysis_mode='REFERENCE_QA')
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?",(original['id'],))
    original=client.app.state.runner.get(original['id'])
    old=_legacy_result(client,original)
    cases=client.app.state.reference_cases
    document=db.one('SELECT id FROM documents WHERE project_id=?',(project['id'],))['id']
    case=cases.create(project['id'],original['id'],QUESTION,result_id=old['result_id'],attachments=[document])
    _downgrade(db,saved,downgraded)
    before=_snapshot(db);sent_before=len(sent)
    rejected=client.post(f"/api/reference-cases/{case['case_id']}/follow-up-results",json={
        'expected_version':0,'run_id':run['id'],'result_id':saved['result_id'],
        'supplemental_document_ids':[document],'note':'Do not link projection.'})
    assert rejected.status_code==409,rejected.text
    assert _snapshot(db)==before and len(sent)==sent_before
