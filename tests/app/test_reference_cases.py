import json
import hashlib

import pytest

from app.db import DomainError, now, uid
from app.reference_cases import ReferenceCaseStore
from .conftest import upload
from .test_reference_results import _public_result, _saved_run


def _followup_result(db, client, project, origin_document, question, *,
                     status='ANSWERED', sent=True, result_question=None,
                     snapshot_id=None, run_id=None):
    """Seed a settled saved result without making a gateway request."""
    runner=client.app.state.runner
    run=runner.get(run_id) if run_id else runner.create(project['id'],analysis_mode='REFERENCE_QA')
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?",(run['id'],))
    run=runner.get(run['id'])
    source=db.one('SELECT payload FROM evidence WHERE run_id=? LIMIT 1',(run['id'],),False)
    if source is None:
        base=json.loads(db.one('SELECT payload FROM evidence WHERE document_id=? LIMIT 1',
                               (origin_document['document_id'],))['payload'])
        base.update({'evidence_id':'EV-FOLLOWUP-'+uid('X')[-8:], 'project_id':project['id'],
                     'input_snapshot_id':run['snapshot_id'], 'document_id':origin_document['document_id']})
        evidence_id=base['evidence_id']
        db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',(
            run['id']+':'+evidence_id,run['id'],project['id'],origin_document['document_id'],
            json.dumps(base),'PENDING',None,''))
    else:
        evidence_id=json.loads(source['payload'])['evidence_id']
    call_id='CALL-'+uid('X').split('-',1)[1];request_hash='a'*64;stamp=now()
    db.execute('''INSERT INTO model_calls(id,project_id,run_id,task_key,model,state,reserved_units,
        actual_units,input_rate,output_rate,request_hash,usage,provider_request_id,response,error,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
        call_id,project['id'],run['id'],'followup:'+call_id,'mock-no-network','SETTLED',1,1,
        '1','1',request_hash,'{}',None,None,None,stamp,stamp))
    actual_question=result_question or question
    evidence_raw=json.loads(db.one('SELECT payload FROM evidence WHERE run_id=? LIMIT 1',(run['id'],))['payload'])['raw_text']
    evidence_inputs=([{'evidence_id':evidence_id,'text_sha256':hashlib.sha256(evidence_raw.encode()).hexdigest()}] if sent else [])
    receipt={'receipt_version':'reference-model-input-receipt-2','model_call_id':call_id,
             'round':1,'request_hash':request_hash,'prompt_contract_hash':'b'*64,
             'question_hash':hashlib.sha256(' '.join(actual_question.split()).encode()).hexdigest(),
             'provider':'mock','model':'mock-no-network','api_protocol':'chat_completions',
             'structured_output_mode':'json_object','inference_mode':'disabled','cached':False,
             'source_text_included':False,'prompt_content_included':False,'chain_of_thought_included':False,
             'evidence_count':int(sent),'evidence_inputs':evidence_inputs,
             'visual_inputs':[],'system_text_bytes':1,'user_text_bytes':1,'image_bytes':0,
             'request_upper_bound_bytes':2,'max_output_tokens':1,
             'selector_version':'literal-page-selector-8','context_policy':'COMPLETE_SELECTED_SCOPE_V1',
             'source_text_clipped':False,
             'ordered_evidence_manifest_sha256':hashlib.sha256(json.dumps(evidence_inputs,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),
             'source_text_bytes':len(evidence_raw.encode()) if sent else 0}
    result=_public_result(run,actual_question,evidence_id=evidence_id)
    result['execution_receipts']=[receipt]
    result['execution_profile']={'provider':'mock','text_model':'mock-no-network',
                                 'vision_enabled':False,'vision_model':'mock-no-network'}
    if status!='ANSWERED':
        result.update({'status':status,'answer':'','claims':[],'missing':['Synthetic missing input.']})
    saved=client.app.state.reference_results.save(run,actual_question,result,'mock','mock-no-network')
    return run,saved['result_id']


def _result(store, run, question, status='ANSWERED', evidence_id='EV-REFERENCE-1'):
    saved=store.save(run,question,_public_result(run,question,evidence_id=evidence_id),'mock','mock-no-network')
    if status != 'ANSWERED':
        store.db.execute('UPDATE reference_results SET status=? WHERE id=?',(status,saved['result_id']))
    return saved['result_id']


def _evaluation_item(db,project,run,question,*,result_id=None,failed=False):
    evaluation_id=uid('QAE');item_id=uid('QAEITEM');stamp=now()
    db.execute('''INSERT INTO reference_evaluations(
        id,project_id,run_id,snapshot_id,name,question_set_hash,profile_json,created_at,updated_at,selector_version)
        VALUES(?,?,?,?,?,?,?,?,?,?)''',(evaluation_id,project['id'],run['id'],run['snapshot_id'],
        'Synthetic evaluation','0'*64,json.dumps({'provider':'mock'}),stamp,stamp,'literal-page-selector-7'))
    import hashlib
    db.execute('''INSERT INTO reference_evaluation_items
        (id,evaluation_id,ordinal,question,question_key,result_id,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?)''',(item_id,evaluation_id,0,question,
        hashlib.sha256(question.encode()).hexdigest(),result_id,stamp,stamp))
    if failed:
        db.execute('''INSERT INTO reference_evaluation_failures
            (id,evaluation_id,item_id,code,stage,detail,created_at,execution_receipt_json)
            VALUES(?,?,?,?,?,?,?,?)''',(uid('QAEFAIL'),evaluation_id,item_id,
            'MODEL_OUTPUT_REJECTED','EXECUTION','Synthetic terminal failure.',stamp,None))
    return evaluation_id,item_id


def test_case_rejects_cross_project_and_wrong_question_sources(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    question='What approved color applies to Finish key PT9?'
    result_id=_result(client.app.state.reference_results,run,question)
    other=client.post('/api/projects',json={'name':'Other case project'}).json()
    _,other_run,_,_=_saved_run(client,other,'REFERENCE_QA','EV-OTHER-CASE')
    other_result=_result(client.app.state.reference_results,other_run,question,evidence_id='EV-OTHER-CASE')
    _,same_project_other_run,_,_=_saved_run(client,project,'REFERENCE_QA','EV-SECOND-CASE-RUN')
    other_run_result=_result(client.app.state.reference_results,same_project_other_run,question,
                             evidence_id='EV-SECOND-CASE-RUN')

    with pytest.raises(DomainError,match='does not match'):
        store.create(project['id'],run['id'],question,result_id=other_result)
    with pytest.raises(DomainError,match='does not match'):
        store.create(project['id'],run['id'],question,result_id=other_run_result)
    with pytest.raises(DomainError,match='does not match'):
        store.create(project['id'],run['id'],'Which section contains the finish requirement?',result_id=result_id)


def test_case_is_idempotent_and_handles_non_answer_and_failed_without_model(client,project,monkeypatch):
    db,run,document,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    monkeypatch.setattr(client.app.state.gateway,'evidence_decision_v3',lambda *_a,**_k: (_ for _ in ()).throw(
        AssertionError('Human cases must not call the model')))
    question='What approved color applies to Finish key PT9?'
    nonanswer=_result(client.app.state.reference_results,run,question,'CANNOT_ANSWER')
    first=store.create(project['id'],run['id'],question,result_id=nonanswer,attachments=[document['document_id']])
    duplicate=store.create(project['id'],run['id'],question,result_id=nonanswer)
    assert duplicate['case_id']==first['case_id'] and first['source']['status']=='CANNOT_ANSWER'
    assert first['attachments']==[{'document_id':document['document_id'],'name':document['name']}]
    with pytest.raises(DomainError,match='use its current version'):
        store.create(project['id'],run['id'],question,result_id=nonanswer,
                     supplemental_question='Please add the missing field observation.')

    failed_question='Which section contains the finish requirement?'
    evaluation_id,item_id=_evaluation_item(db,project,run,failed_question,failed=True)
    failed=store.create(project['id'],run['id'],failed_question,evaluation_id=evaluation_id,question_id=item_id)
    assert failed['source']['status']=='FAILED' and failed['source']['result_id'] is None
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_case_versions_resolution_reopen_and_append_only_history(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    question='What approved color applies to Finish key PT9?'
    result_id=_result(client.app.state.reference_results,run,question,'NEED_USER_INPUT')
    case=store.create(project['id'],run['id'],question,result_id=result_id,note='Needs field confirmation.')
    with pytest.raises(DomainError,match='requires a non-empty'):
        store.update(case['case_id'],0,status='RESOLVED')
    resolved=store.update(case['case_id'],0,status='RESOLVED',resolution='Field engineer confirmed blue.',
                          note='Confirmed by site walk.',supplemental_question='Please attach the field photo.')
    assert resolved['status']=='RESOLVED' and resolved['version']==1
    with pytest.raises(DomainError,match='refresh'):
        store.update(case['case_id'],0,assignee='field-engineer')
    reopened=store.update(case['case_id'],1,status='OPEN',assignee='field-engineer',note='Reopened after revision.')
    assert reopened['status']=='OPEN' and reopened['resolution']==''
    assert reopened['latest_note']=='Reopened after revision.'
    assert reopened['history'][-1]['before']['resolution']=='Field engineer confirmed blue.'
    with pytest.raises(DomainError,match='requires a non-empty'):
        store.update(case['case_id'],2,status='RESOLVED')
    resolved_again=store.update(case['case_id'],2,status='RESOLVED',
                                resolution='A new field confirmation was recorded.')
    assert resolved_again['status']=='RESOLVED' and resolved_again['resolution'].startswith('A new')
    assert [event['action'] for event in reopened['history']]==[
        'CREATED','UPDATED','SUPPLEMENTAL_QUESTION','REOPENED']
    assert reopened['history'][1]['before']['status']=='OPEN'
    listing=store.list(project['id'],status='RESOLVED',run_id=run['id'])
    assert listing['total']==1 and listing['items'][0]['case_id']==case['case_id']
    assert listing['items'][0]['latest_note']=='Reopened after revision.'


def test_case_only_accepts_uploaded_same_project_attachments(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    question='What approved color applies to Finish key PT9?'
    result_id=_result(client.app.state.reference_results,run,question)
    other=client.post('/api/projects',json={'name':'Other attachment project'}).json()
    other_document=upload(client,other['id'],'other.pdf',b'synthetic')
    with pytest.raises(DomainError,match='outside this project'):
        store.create(project['id'],run['id'],question,result_id=result_id,
                     attachments=[other_document['document_id']])


def test_case_reads_are_side_effect_free_and_update_rejects_cross_project_attachment(client,project):
    db,run,document,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    question='What approved color applies to Finish key PT9?'
    result_id=_result(client.app.state.reference_results,run,question,'NEED_USER_INPUT')
    case=store.create(project['id'],run['id'],question,result_id=result_id)
    before=db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']
    assert store.get(case['case_id'])['case_id']==case['case_id']
    assert store.list(project['id'])['items'][0]['case_id']==case['case_id']
    assert db.one('SELECT COUNT(*) AS n FROM reference_case_events')['n']==before

    other=client.post('/api/projects',json={'name':'Other supplemental attachment project'}).json()
    other_document=upload(client,other['id'],'other-supplement.pdf',b'synthetic')
    with pytest.raises(DomainError,match='outside this project'):
        store.update(case['case_id'],0,attachments=[document['document_id'],other_document['document_id']],
                     supplemental_question='Attach the revised field photograph.')
    unchanged=store.get(case['case_id'])
    assert unchanged['version']==0 and unchanged['attachments']==[]
    assert len(unchanged['history'])==1


def test_case_accepts_need_user_input_and_rejects_mismatched_evaluation_result(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    question='What approved color applies to Finish key PT9?'
    result_id=_result(client.app.state.reference_results,run,question,'NEED_USER_INPUT')
    case=store.create(project['id'],run['id'],question,result_id=result_id)
    assert case['source']['status']=='NEED_USER_INPUT'

    other_question='Which section contains the finish requirement?'
    other_result=_result(client.app.state.reference_results,run,other_question)
    evaluation_id,item_id=_evaluation_item(db,project,run,question,result_id=other_result)
    with pytest.raises(DomainError,match='does not match its evaluation item'):
        store.create(project['id'],run['id'],question,result_id=result_id,
                     evaluation_id=evaluation_id,question_id=item_id)


def test_case_followup_links_saved_result_with_receipt_proof_idempotently(client,project,monkeypatch):
    db,origin,document,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    question='What approved color applies to Finish key PT9?'
    source=_result(client.app.state.reference_results,origin,question,'NEED_USER_INPUT')
    case=store.create(project['id'],origin['id'],question,result_id=source,attachments=[document['document_id']])
    followup_run,followup_result=_followup_result(db,client,project,document,question)
    monkeypatch.setattr(client.app.state.gateway,'evidence_decision_v3',lambda *_a,**_k: pytest.fail('no model call'))
    before_result=db.one('SELECT result_json,result_hash FROM reference_results WHERE id=?',(followup_result,))
    linked=store.link_followup(case['case_id'],0,followup_run['id'],followup_result,[document['document_id']],'Photo was included.')
    proof=linked['followups'][0]['proof']['documents'][0]
    assert linked['status']=='IN_REVIEW' and linked['version']==1
    assert proof['document_sha256']==db.one('SELECT sha256 FROM documents WHERE id=?',(document['document_id'],))['sha256'] and proof['text_input_rounds']==[1]
    assert proof['visual_input_count']==0 and proof['citation_count']==1
    assert linked['history'][-1]['after']['followup_id']==linked['followups'][0]['followup_id']
    replay=store.link_followup(case['case_id'],0,followup_run['id'],followup_result,[document['document_id']],'Photo was included.')
    assert replay['version']==1 and db.one('SELECT COUNT(*) AS n FROM reference_case_followups')['n']==1
    assert before_result==db.one('SELECT result_json,result_hash FROM reference_results WHERE id=?',(followup_result,))
    with pytest.raises(DomainError,match='different input'):
        store.link_followup(case['case_id'],1,followup_run['id'],followup_result,[document['document_id']],'Changed note.')


def test_case_followup_rejects_wrong_identity_missing_proof_and_resolved_case(client,project):
    db,origin,document,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    question='What approved color applies to Finish key PT9?';source=_result(client.app.state.reference_results,origin,question)
    case=store.create(project['id'],origin['id'],question,result_id=source,attachments=[document['document_id']])
    bad_run,bad_question=_followup_result(db,client,project,document,question,result_question='Which section controls this?')
    with pytest.raises(DomainError,match='does not match'):
        store.link_followup(case['case_id'],0,bad_run['id'],bad_question,[document['document_id']],'Wrong question.')
    no_proof_run,no_proof=_followup_result(db,client,project,document,question,sent=False)
    with pytest.raises(DomainError,match='was not sent'):
        store.link_followup(case['case_id'],0,no_proof_run['id'],no_proof,[document['document_id']],'No receipt input.')
    snapshot_run,snapshot_result=_followup_result(db,client,project,document,question)
    db.execute('UPDATE reference_results SET snapshot_id=? WHERE id=?',('SN-mismatch',snapshot_result))
    with pytest.raises(DomainError,match='does not match'):
        store.link_followup(case['case_id'],0,snapshot_run['id'],snapshot_result,[document['document_id']],'Wrong snapshot.')
    resolved=store.update(case['case_id'],0,status='RESOLVED',resolution='Human answer.')
    okay_run,okay=_followup_result(db,client,project,document,question)
    with pytest.raises(DomainError,match='Reopen'):
        store.link_followup(case['case_id'],resolved['version'],okay_run['id'],okay,[document['document_id']],'Must reopen.')


def test_case_followup_need_user_input_and_cross_project_are_guarded(client,project):
    db,origin,document,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    question='What approved color applies to Finish key PT9?';source=_result(client.app.state.reference_results,origin,question)
    case=store.create(project['id'],origin['id'],question,result_id=source,attachments=[document['document_id']])
    run,result=_followup_result(db,client,project,document,question,status='NEED_USER_INPUT')
    linked=store.link_followup(case['case_id'],0,run['id'],result,[document['document_id']],'Need another observation.')
    assert linked['status']=='NEEDS_INFORMATION'
    other=client.post('/api/projects',json={'name':'Follow-up other project'}).json()
    _,other_origin,other_document,_=_saved_run(client,other,'REFERENCE_QA')
    other_run,other_result=_followup_result(db,client,other,other_document,question)
    with pytest.raises(DomainError,match='invalid|does not match'):
        store.link_followup(case['case_id'],linked['version'],other_run['id'],other_result,[document['document_id']],'Cross project.')


def test_case_followup_rejects_run_missing_document_present_when_answer_saved(client,project):
    db,origin,document,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    question='What approved color applies to Finish key PT9?'
    source=_result(client.app.state.reference_results,origin,question)
    case=store.create(project['id'],origin['id'],question,result_id=source,attachments=[document['document_id']])
    run,result_id=_followup_result(db,client,project,document,question)
    omitted=upload(client,project['id'],'omitted-at-answer.pdf',b'synthetic omitted document')
    result_at=db.one('SELECT created_at FROM reference_results WHERE id=?',(result_id,))['created_at']
    db.execute('UPDATE documents SET created_at=? WHERE id=?',(result_at,omitted['document_id']))
    with pytest.raises(DomainError,match='omits documents'):
        store.link_followup(case['case_id'],0,run['id'],result_id,[document['document_id']],'Run did not include all answer-time documents.')


@pytest.mark.parametrize('mutation',[
    'provider','model','question_hash','result_hash',
])
def test_case_followup_reauthenticates_tampered_saved_receipts(client,project,mutation):
    db,origin,document,_=_saved_run(client,project,'REFERENCE_QA');store=ReferenceCaseStore(db)
    question='What approved color applies to Finish key PT9?';source=_result(client.app.state.reference_results,origin,question)
    case=store.create(project['id'],origin['id'],question,result_id=source,attachments=[document['document_id']])
    run,result_id=_followup_result(db,client,project,document,question)
    row=db.one('SELECT result_json FROM reference_results WHERE id=?',(result_id,));payload=json.loads(row['result_json'])
    if mutation=='provider':db.execute('UPDATE reference_results SET provider=? WHERE id=?',('other',result_id))
    elif mutation=='model':db.execute('UPDATE reference_results SET model=? WHERE id=?',('other-model',result_id))
    elif mutation=='result_hash':db.execute('UPDATE reference_results SET result_hash=? WHERE id=?',('0'*64,result_id))
    else:
        receipt=payload['execution_receipts'][0]
        receipt['question_hash']='0'*64 if mutation=='question_hash' else receipt['question_hash']
        raw=json.dumps(payload,separators=(',',':'),ensure_ascii=False)
        db.execute('UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?',
                   (raw,hashlib.sha256(raw.encode()).hexdigest(),result_id))
    with pytest.raises(DomainError,match='inconsistent'):
        store.link_followup(case['case_id'],0,run['id'],result_id,[document['document_id']],'Tampered.')
