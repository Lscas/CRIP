"""Offline v8 receipt routing identity tests."""
import hashlib
import json
from dataclasses import replace

import pytest

from app.db import DomainError,now,uid
from .test_reference_results import _public_result,_saved_run


def _result_with_receipts(db,run,question,*,models,visual_rounds=()):
    evidence=json.loads(db.one('SELECT payload FROM evidence WHERE run_id=? LIMIT 1',
                              (run['id'],))['payload'])
    evidence_inputs=[{'evidence_id':evidence['evidence_id'],
                      'text_sha256':hashlib.sha256(evidence['raw_text'].encode()).hexdigest()}]
    receipts=[]
    for round_number,model in enumerate(models,1):
        call_id='CALL-'+uid('X').split('-',1)[1];request_hash=(str(round_number)*64)[:64]
        stamp=now()
        db.execute('''INSERT INTO model_calls(id,project_id,run_id,task_key,model,state,reserved_units,
            actual_units,input_rate,output_rate,request_hash,usage,provider_request_id,response,error,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
            call_id,run['project_id'],run['id'],'identity:'+call_id,model,'SETTLED',1,1,
            '1','1',request_hash,'{}',None,None,None,stamp,stamp))
        images=[] if round_number not in visual_rounds else [{'region_id':'VR-'+str(round_number),
            'image_sha256':'0'*64,'image_bytes':1}]
        receipts.append({'receipt_version':'reference-model-input-receipt-2','model_call_id':call_id,
            'round':round_number,'request_hash':request_hash,'prompt_contract_hash':'b'*64,
            'question_hash':hashlib.sha256(' '.join(question.split()).encode()).hexdigest(),
            'provider':'mock','model':model,'api_protocol':'chat_completions',
            'structured_output_mode':'json_object','inference_mode':'disabled','cached':False,
            'source_text_included':False,'prompt_content_included':False,'chain_of_thought_included':False,
            'evidence_count':1,'evidence_inputs':evidence_inputs,'visual_inputs':images,
            'system_text_bytes':1,'user_text_bytes':1,'image_bytes':len(images),
            'request_upper_bound_bytes':2,'max_output_tokens':1,
            'selector_version':'literal-page-selector-8','context_policy':'COMPLETE_SELECTED_SCOPE_V1',
            'source_text_clipped':False,
            'ordered_evidence_manifest_sha256':hashlib.sha256(
                json.dumps(evidence_inputs,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),
            'source_text_bytes':len(evidence['raw_text'].encode())})
    result=_public_result(run,question,evidence_id=evidence['evidence_id'])
    result['execution_receipts']=receipts
    result['execution_profile']={'provider':'mock','text_model':'text-model',
                                 'vision_enabled':True,'vision_model':'vision-model'}
    regions=[{'region_id':'VR-'+str(round_number),'image_sha256':'0'*64,
              'document_id':evidence['document_id'],
              'page_number':evidence['locator']['page_number'],
              'source_evidence_ids':[evidence['evidence_id']]}
             for round_number in visual_rounds]
    if regions:result['sent_visual_regions']=regions
    return result


@pytest.mark.parametrize(('models','visual_rounds'),[
    (['vision-model'],[1]),
    (['text-model','vision-model'],[2]),
    (['vision-model','text-model'],[1]),
])
def test_v8_accepts_per_round_frozen_model_route(client,project,models,visual_rounds):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');question='What approved color applies?'
    result=_result_with_receipts(db,run,question,models=models,visual_rounds=visual_rounds)
    saved=client.app.state.reference_results.save(run,question,result,'mock','untrusted-caller-model')

    row=db.one('SELECT * FROM reference_results WHERE id=?',(saved['result_id'],))
    assert row['model']==models[-1]
    client.app.state.reference_results.authenticate_saved_result(run,row)


@pytest.mark.parametrize('mutation',[
    lambda result: result['execution_receipts'][0].update(model='wrong-model'),
    lambda result: result['execution_profile'].update(vision_enabled=False),
])
def test_v8_rejects_receipt_model_or_profile_route_mismatch(client,project,mutation):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');question='What approved color applies?'
    result=_result_with_receipts(db,run,question,models=['vision-model'],visual_rounds=[1]);mutation(result)

    with pytest.raises(DomainError,match='execution profile'):
        client.app.state.reference_results.save(run,question,result,'mock','vision-model')


def test_v8_visual_cannot_answer_keeps_the_vision_terminal_model(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');question='What cannot be determined?'
    result=_result_with_receipts(db,run,question,models=['vision-model'],visual_rounds=[1])
    result.update({'status':'CANNOT_ANSWER','answer':'','claims':[],
                   'missing':['The selected page does not establish this.']})
    saved=client.app.state.reference_results.save(run,question,result,'mock','text-model')

    assert saved['model']=='vision-model'


def test_v8_rejects_settled_call_model_mismatch(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');question='What approved color applies?'
    result=_result_with_receipts(db,run,question,models=['text-model'])
    db.execute('UPDATE model_calls SET model=? WHERE id=?',(
        'wrong-settled-model',result['execution_receipts'][0]['model_call_id']))

    with pytest.raises(DomainError,match='does not match its settled call'):
        client.app.state.reference_results.save(run,question,result,'mock','text-model')


def test_v8_rejects_tampered_terminal_row_model(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');question='What approved color applies?'
    result=_result_with_receipts(db,run,question,models=['text-model','vision-model'],visual_rounds=[2])
    saved=client.app.state.reference_results.save(run,question,result,'mock','text-model')
    db.execute('UPDATE reference_results SET model=? WHERE id=?',('text-model',saved['result_id']))
    row=db.one('SELECT * FROM reference_results WHERE id=?',(saved['result_id'],))

    with pytest.raises(DomainError,match='terminal model'):
        client.app.state.reference_results.authenticate_saved_result(run,row)


def test_legacy_v7_mixed_receipts_keep_historical_row_model(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');question='What approved color applies?'
    result=_result_with_receipts(db,run,question,models=['text-model','vision-model'],visual_rounds=[])
    result.pop('execution_profile')
    for receipt in result['execution_receipts']:
        receipt['receipt_version']='reference-model-input-receipt-1'
        for key in ('selector_version','context_policy','source_text_clipped',
                    'ordered_evidence_manifest_sha256','source_text_bytes'):
            receipt.pop(key)
    saved=client.app.state.reference_results.save(run,question,result,'mock','historical-row-model')
    row=db.one('SELECT * FROM reference_results WHERE id=?',(saved['result_id'],))

    assert row['model']=='historical-row-model'
    client.app.state.reference_results.authenticate_saved_result(run,row)


def test_v8_rejects_out_of_order_receipts(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');question='What approved color applies?'
    result=_result_with_receipts(db,run,question,models=['text-model','vision-model'],visual_rounds=[2])
    result['execution_receipts'].reverse()
    with pytest.raises(DomainError,match='rounds are not ordered'):
        client.app.state.reference_results.save(run,question,result,'mock','ignored-label')


def test_v8_receipts_cannot_bypass_model_routing_by_removing_profile(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA');question='What approved color applies?'
    result=_result_with_receipts(db,run,question,models=['text-model'])
    result.pop('execution_profile')
    with pytest.raises(DomainError,match='require an execution profile'):
        client.app.state.reference_results.save(run,question,result,'mock','ignored-label')


def test_v8_no_gateway_keeps_model_disabled_and_makes_no_call(client,project):
    from app.evidence_loop import ProjectEvidenceLoop
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    result=ProjectEvidenceLoop(db,None).ask(run,'What approved color applies?',
                                          selector_version='literal-page-selector-8')
    assert result['status']=='MODEL_DISABLED' and result['model_call_count']==0
    assert result['execution_receipts']==[]
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_v8_mock_profile_matches_disabled_execution_and_validates_provider(client,project):
    from app.evidence_loop import ProjectEvidenceLoop
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    question='What approved color applies?'
    result=ProjectEvidenceLoop(db,client.app.state.gateway).ask(run,question)
    assert result['execution_profile']=={'provider':'mock','text_model':'mock-no-network',
                                         'vision_enabled':False,'vision_model':None}
    with pytest.raises(DomainError,match='profile provider'):
        client.app.state.reference_results.save(run,question,result,'deepseek','deepseek-flash')


@pytest.mark.parametrize(('status','basis','expected_model'),[
    ('ANSWERED','MODEL_QA_V3_MULTIMODAL_EVIDENCE_LOOP','deepseek-v4-flash-vision-exp'),
    ('CANNOT_ANSWER','MODEL_QA_V3_EVIDENCE_LOOP','deepseek-flash'),
    ('ANSWERED','MODEL_QA_V3_EVIDENCE_LOOP','deepseek-flash'),
])
def test_frozen_v7_api_preserves_historical_basis_model_selection(
        client,project,monkeypatch,status,basis,expected_model):
    from app.evidence_loop import ProjectEvidenceLoop
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=replace(gateway.s,provider='deepseek',cheap_model='deepseek-flash',
                       vision_enabled=True,vision_model='deepseek-v4-flash-vision-exp')
    question='What approved color applies?'
    def legacy_answer(_self,saved_run,value,selector_version):
        assert selector_version=='literal-page-selector-7'
        result=_public_result(saved_run,value)
        result['answer_basis']=basis
        if status!='ANSWERED':result.update(status=status,answer='',claims=[],missing=['Synthetic gap.'])
        return result
    monkeypatch.setattr(ProjectEvidenceLoop,'ask',legacy_answer)
    evaluation=client.post(f'/api/projects/{project["id"]}/reference-evaluations',json={
        'run_id':run['id'],'name':'Frozen legacy model identity','questions':[question]}).json()
    db.execute('UPDATE reference_evaluations SET selector_version=? WHERE id=?',
               ('literal-page-selector-7',evaluation['evaluation_id']))
    item=evaluation['items'][0]
    response=client.post(f'/api/reference-evaluations/{evaluation["evaluation_id"]}/items/{item["item_id"]}/execute')
    assert response.status_code==200,response.text
    assert response.json()['result']['model']==expected_model
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0
