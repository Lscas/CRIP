from dataclasses import replace
import json

import httpx

from app.db import Database
from app.gateway import InvalidModelOutput,ModelResult
from .test_evidence_loop import _answer
from .test_reference_results import _saved_run


def _create(client,project,run,questions=None,name='Fixed reference questions'):
    return client.post(f"/api/projects/{project['id']}/reference-evaluations",json={
        'run_id':run['id'],'name':name,'questions':questions or [
            'What approved color applies to Finish key PT9?',
            'Which section contains the finish requirement?',
        ]})


def test_evaluation_freezes_run_questions_and_profile_without_model_call(
        client,project,monkeypatch):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    monkeypatch.setattr(client.app.state.gateway,'evidence_decision_v3',lambda *_a,**_k: (_ for _ in ()).throw(
        AssertionError('Evaluation management called the model')))

    created=_create(client,project,run,questions=[
        'What  approved color applies to Finish key PT9?',
        'Which section contains the finish requirement?',
    ])

    assert created.status_code==201,created.text
    body=created.json();assert body['status']=='READY'
    assert body['project_id']==project['id'] and body['run_id']==run['id']
    assert body['snapshot_id']==run['snapshot_id']
    assert body['selector_version']=='literal-page-selector-8'
    assert body['profile']=={
        'provider':'mock','text_model':'mock-no-network','vision_enabled':False,
        'vision_model':None,'inference_mode':'disabled',
        'structured_output_mode':'json_object','api_protocol':'chat_completions'}
    assert [item['ordinal'] for item in body['items']]==[0,1]
    assert body['items'][0]['question']=='What approved color applies to Finish key PT9?'
    assert all(item['state']=='PENDING' and item['result'] is None and item['failure'] is None
               for item in body['items'])
    assert body['summary']=={
        'TOTAL':2,'PENDING':2,'FAILED':0,'ANSWERED':0,'CANNOT_ANSWER':0,'NEED_USER_INPUT':0,
        'MODEL_DISABLED':0,'REVIEW_PENDING':0,'ACCEPTED':0,'REJECTED':0,
        'NOT_APPLICABLE':0}
    assert db.one('SELECT 1 FROM schema_migrations WHERE version=12') is not None
    assert db.one('SELECT 1 FROM schema_migrations WHERE version=13') is not None
    assert db.one('SELECT 1 FROM schema_migrations WHERE version=16') is not None
    assert db.one('SELECT 1 FROM schema_migrations WHERE version=17') is not None
    assert db.one('SELECT 1 FROM schema_migrations WHERE version=18') is not None
    assert db.one('SELECT 1 FROM schema_migrations WHERE version=19') is not None
    assert db.one('SELECT 1 FROM schema_migrations WHERE version=20') is not None
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0
    reopened=Database(db.path)
    assert reopened.one('SELECT 1 FROM schema_migrations WHERE version=16') is not None
    assert reopened.one('SELECT 1 FROM schema_migrations WHERE version=17') is not None
    assert reopened.one('SELECT 1 FROM schema_migrations WHERE version=19') is not None
    assert reopened.one('SELECT 1 FROM schema_migrations WHERE version=20') is not None
    assert reopened.one("SELECT selector_version FROM reference_evaluations WHERE id=?",
                        (body['evaluation_id'],))['selector_version']=='literal-page-selector-8'

    legacy_profile=dict(body['profile']);legacy_profile.pop('structured_output_mode')
    legacy_profile.pop('api_protocol')
    db.execute('UPDATE reference_evaluations SET profile_json=? WHERE id=?',
               (json.dumps(legacy_profile),body['evaluation_id']))
    client.app.state.reference_evaluations.require_profile(
        body['evaluation_id'],client.app.state.settings)

    listing=client.get(f"/api/projects/{project['id']}/reference-evaluations")
    detail=client.get(f"/api/reference-evaluations/{body['evaluation_id']}")
    assert listing.status_code==detail.status_code==200
    assert listing.json()['total']==1
    assert listing.json()['items'][0]==detail.json()==body
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_evaluation_rejects_duplicate_legacy_active_and_cross_project_inputs(client,project):
    _,reference,_,_=_saved_run(client,project,'REFERENCE_QA')
    duplicate=_create(client,project,reference,questions=[
        'What color applies?','What  color applies?'])
    assert duplicate.status_code==400

    _,legacy,_,_=_saved_run(client,project,'LEGACY_ANALYSIS','EV-LEGACY-EVALUATION')
    wrong_mode=_create(client,project,legacy)
    assert wrong_mode.status_code==409

    active=client.app.state.runner.create(project['id'],analysis_mode='REFERENCE_QA')
    active_response=_create(client,project,active)
    assert active_response.status_code==409
    cancelled=client.post(f"/api/analysis-runs/{active['id']}/cancel")
    assert cancelled.status_code==200

    other=client.post('/api/projects',json={'name':'Other evaluation project'}).json()
    _,other_run,_,_=_saved_run(client,other,'REFERENCE_QA','EV-OTHER-EVALUATION')
    cross_project=_create(client,project,other_run)
    assert cross_project.status_code==404


def test_evaluation_preview_is_local_and_selected_execution_is_idempotent(
        client,project,monkeypatch):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=replace(
        gateway.s,provider='custom-0123456789abcdef',
        api_base_url='https://models.example/v1',api_key='synthetic-valid-key',
        cheap_model='gpt-5.6',vision_model='gpt-5.6',live_enabled=True)
    created=_create(client,project,run);assert created.status_code==201,created.text
    evaluation=created.json();first=evaluation['items'][0];second=evaluation['items'][1]
    calls=[]
    def decision(*_args,**_kwargs):
        calls.append('decision')
        return ModelResult(_answer('EV-REFERENCE-1','The approved color is blue.'),
                           None,False,'custom')
    monkeypatch.setattr(gateway,'evidence_decision_v3',decision)

    preview=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{first['item_id']}/preview")
    assert preview.status_code==200,preview.text
    assert preview.json()['question']==first['question']
    assert preview.json()['page_selection']['run_id']==run['id']
    assert calls==[] and db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0

    executed=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{first['item_id']}/execute")
    assert executed.status_code==200,executed.text
    assert executed.json()['replayed'] is False and len(calls)==1
    after=executed.json()['evaluation'];assert after['status']=='IN_PROGRESS'
    assert after['summary']['ANSWERED']==1 and after['summary']['PENDING']==1
    assert after['summary']['REVIEW_PENDING']==1
    assert after['items'][0]['state']=='COMPLETE'
    assert after['items'][1]['item_id']==second['item_id'] and after['items'][1]['state']=='PENDING'
    result_id=executed.json()['result']['result_id']
    assert after['items'][0]['result']['result_id']==result_id
    assert 'result' not in after['items'][0]['result']
    assert 'citations' not in after['items'][0]['result']

    replayed=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{first['item_id']}/execute")
    assert replayed.status_code==200 and replayed.json()['replayed'] is True
    assert replayed.json()['result']['result_id']==result_id and len(calls)==1

    reviewed=client.post(f'/api/reference-results/{result_id}/review',json={
        'action':'ACCEPTED','expected_version':0,'note':'Evaluation source checked.'})
    refreshed=client.get(f"/api/reference-evaluations/{evaluation['evaluation_id']}")
    assert reviewed.status_code==refreshed.status_code==200
    assert refreshed.json()['summary']['REVIEW_PENDING']==0
    assert refreshed.json()['summary']['ACCEPTED']==1
    assert db.one('SELECT COUNT(*) AS n FROM reference_results')['n']==1
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_evaluation_execution_fails_before_dispatch_after_profile_change(
        client,project,monkeypatch):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=replace(
        gateway.s,provider='custom-0123456789abcdef',
        api_base_url='https://models.example/v1',api_key='synthetic-valid-key',
        cheap_model='gpt-5.6',vision_model='gpt-5.6',live_enabled=True)
    evaluation=_create(client,project,run).json();item=evaluation['items'][0]
    gateway.s=replace(gateway.s,structured_output_mode='json_schema')
    calls=[]
    monkeypatch.setattr(gateway,'evidence_decision_v3',lambda *_a,**_k:calls.append('called'))

    response=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item['item_id']}/execute")

    assert response.status_code==409
    assert 'differs' in response.json()['detail']
    assert calls==[]
    assert client.get(f"/api/reference-evaluations/{evaluation['evaluation_id']}").json()['status']=='READY'
    assert db.one('SELECT COUNT(*) AS n FROM reference_results')['n']==0
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_evaluation_saves_safe_terminal_contract_failure_without_retry(
        client,project,monkeypatch):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=replace(
        gateway.s,provider='custom-0123456789abcdef',
        api_base_url='https://models.example/v1',api_key='synthetic-valid-key',
        cheap_model='gpt-5.6',vision_model='gpt-5.6',live_enabled=True)
    evaluation=_create(client,project,run,questions=[
        'What approved color applies to Finish key PT9?']).json()
    item=evaluation['items'][0];calls=[]

    def rejected(*_args,**_kwargs):
        calls.append('decision')
        raise InvalidModelOutput('synthetic provider text must not be retained')

    monkeypatch.setattr(gateway,'evidence_decision_v3',rejected)
    response=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item['item_id']}/execute")

    assert response.status_code==200,response.text
    body=response.json();failure=body['failure'];saved=body['evaluation']
    assert body['result'] is None and body['replayed'] is False
    assert failure['code']=='MODEL_OUTPUT_REJECTED' and 'synthetic' not in failure['detail']
    assert failure['execution_receipt'] is None
    assert failure['validator_category'] is None
    assert saved['status']=='COMPLETE'
    assert saved['summary']['FAILED']==1 and saved['summary']['PENDING']==0
    assert saved['items'][0]['state']=='FAILED'
    assert saved['items'][0]['failure']==failure and saved['items'][0]['result'] is None
    assert db.one('SELECT COUNT(*) AS n FROM reference_results')['n']==0
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluation_failures')['n']==1

    replayed=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item['item_id']}/execute")
    assert replayed.status_code==200 and replayed.json()['replayed'] is True
    assert replayed.json()['failure']==failure and calls==['decision']


def test_evaluation_persists_safe_input_receipt_for_contract_rejection(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=replace(
        gateway.s,provider='custom-0123456789abcdef',
        api_base_url='https://models.example/v1',api_key='synthetic-valid-key',
        cheap_model='gpt-5.6',vision_model='gpt-5.6',live_enabled=True)
    evaluation=_create(client,project,run,questions=[
        'What approved color applies to Finish key PT9?']).json()
    item=evaluation['items'][0]
    rejected_marker='untrusted-provider-marker-must-not-persist'

    def handler(_request):
        invalid={
            'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE','missing_facts':[],
            'requests':[],'answer':{
                'status':'ANSWERED','answer':rejected_marker,
                'claims':[],'missing':[],'calculations':[],'coverage':[],
            },
        }
        return httpx.Response(200,json={
            'id':'receipt-rejection-1',
            'choices':[{'finish_reason':'stop','message':{
                'content':json.dumps(invalid)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':20},
        })

    gateway.client.close()
    gateway.client=httpx.Client(transport=httpx.MockTransport(handler))
    response=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item['item_id']}/execute")

    assert response.status_code==200,response.text
    failure=response.json()['failure'];receipt=failure['execution_receipt']
    assert response.json()['result'] is None and receipt['cached'] is False
    assert failure['validator_category']=='additionalProperties'
    assert receipt['round']==1 and receipt['provider']=='custom-0123456789abcdef'
    assert receipt['model']=='gpt-5.6' and receipt['evidence_count']>=1
    assert receipt['source_text_included'] is False
    assert rejected_marker not in json.dumps(failure)
    call=db.one('SELECT id,state,request_hash,response,error FROM model_calls')
    assert call['id']==receipt['model_call_id'] and call['state']=='SETTLED_ERROR'
    assert call['request_hash']==receipt['request_hash'] and call['response'] is None
    assert rejected_marker not in (call['error'] or '')
    raw=db.one('SELECT execution_receipt_json FROM reference_evaluation_failures')
    assert json.loads(raw['execution_receipt_json'])==receipt


def test_evaluation_items_save_one_at_a_time_and_resume_from_pending(
        client,project,monkeypatch):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=replace(
        gateway.s,provider='custom-0123456789abcdef',
        api_base_url='https://models.example/v1',api_key='synthetic-valid-key',
        cheap_model='gpt-5.6',vision_model='gpt-5.6',live_enabled=True)
    evaluation=_create(client,project,run,questions=[
        'What approved color applies to Finish key PT9?',
        'Which color is approved for Finish key PT9?',
        'For Finish key PT9, what color is approved?']).json()
    calls=[]

    def decision(*_args,**_kwargs):
        number=len(calls)+1;calls.append(number)
        return ModelResult(
            _answer('EV-REFERENCE-1','The approved color is blue.'),None,False,'custom')

    monkeypatch.setattr(gateway,'evidence_decision_v3',decision)
    pending=evaluation['items']
    for completed,item in enumerate(pending,start=1):
        response=client.post(
            f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item['item_id']}/execute")
        assert response.status_code==200,response.text
        saved=response.json()['evaluation']
        assert saved['summary']['ANSWERED']==completed
        assert saved['summary']['PENDING']==3-completed
        assert [entry['state'] for entry in saved['items'][:completed]]==['COMPLETE']*completed
        assert [entry['state'] for entry in saved['items'][completed:]]==['PENDING']*(3-completed)
        assert db.one('SELECT COUNT(*) AS n FROM reference_results')['n']==completed

    assert calls==[1,2,3]
    assert saved['status']=='COMPLETE'
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def _profile_settings(settings,identity,model):
    return replace(
        settings,provider=identity,api_base_url='https://models.example/v1',
        api_key='synthetic-valid-key',cheap_model=model,vision_model=model,
        live_enabled=True,structured_output_mode='json_object',
        api_protocol='chat_completions')


def test_evaluation_clone_freezes_same_snapshot_and_questions_under_new_profile(
        client,project,monkeypatch):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=_profile_settings(
        gateway.s,'custom-1111111111111111','reference-model-a')
    source=_create(client,project,run).json()
    monkeypatch.setattr(gateway,'evidence_decision_v3',lambda *_a,**_k:(_ for _ in ()).throw(
        AssertionError('Cloning called the model')))

    duplicate=client.post(f"/api/reference-evaluations/{source['evaluation_id']}/clone")
    assert duplicate.status_code==409 and 'different model profile' in duplicate.json()['detail']

    gateway.s=_profile_settings(
        gateway.s,'custom-2222222222222222','reference-model-b')
    response=client.post(f"/api/reference-evaluations/{source['evaluation_id']}/clone")

    assert response.status_code==201,response.text
    body=response.json();cloned=body['evaluation']
    assert body['source_evaluation_id']==source['evaluation_id'] and body['model_called'] is False
    assert cloned['evaluation_id']!=source['evaluation_id']
    assert cloned['run_id']==source['run_id'] and cloned['snapshot_id']==source['snapshot_id']
    assert cloned['question_set_hash']==source['question_set_hash']
    assert cloned['selector_version']==source['selector_version']=='literal-page-selector-8'
    assert [item['question'] for item in cloned['items']]==[
        item['question'] for item in source['items']]
    assert cloned['profile']['text_model']=='reference-model-b'
    assert cloned['status']=='READY' and cloned['summary']['PENDING']==2
    pending_query=(f"baseline_evaluation_id={source['evaluation_id']}"
                   f"&candidate_evaluation_id={cloned['evaluation_id']}")
    pending_comparison=client.get(
        f"/api/projects/{project['id']}/reference-evaluations/compare?{pending_query}")
    assert pending_comparison.status_code==200,pending_comparison.text
    assert pending_comparison.json()['summary']=={
        'PENDING':2,'CHANGED':0,'UNCHANGED':0,'TOTAL':2}
    assert all(item['change']=='PENDING' for item in pending_comparison.json()['items'])
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluations')['n']==2
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_evaluation_compare_is_same_snapshot_local_and_outcome_only(
        client,project,monkeypatch):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=_profile_settings(
        gateway.s,'custom-1111111111111111','reference-model-a')
    baseline=_create(client,project,run).json()
    monkeypatch.setattr(
        gateway,'evidence_decision_v3',
        lambda *_a,**_k:ModelResult(
            _answer('EV-REFERENCE-1','The approved color is blue.'),None,False,'custom'))
    for item in baseline['items']:
        response=client.post(
            f"/api/reference-evaluations/{baseline['evaluation_id']}/items/{item['item_id']}/execute")
        assert response.status_code==200,response.text
    baseline=response.json()['evaluation']

    gateway.s=_profile_settings(
        gateway.s,'custom-2222222222222222','reference-model-b')
    candidate=client.post(
        f"/api/reference-evaluations/{baseline['evaluation_id']}/clone").json()['evaluation']
    candidate_calls=[]
    def candidate_decision(*_args,**_kwargs):
        candidate_calls.append('decision')
        if len(candidate_calls)==2:
            raise InvalidModelOutput('synthetic rejected output must not be retained')
        return ModelResult(
            _answer('EV-REFERENCE-1','The approved color is blue.'),None,False,'custom')
    monkeypatch.setattr(gateway,'evidence_decision_v3',candidate_decision)
    for item in candidate['items']:
        response=client.post(
            f"/api/reference-evaluations/{candidate['evaluation_id']}/items/{item['item_id']}/execute")
        assert response.status_code==200,response.text
    candidate=response.json()['evaluation']

    query=(f"baseline_evaluation_id={baseline['evaluation_id']}"
           f"&candidate_evaluation_id={candidate['evaluation_id']}")
    first=client.get(
        f"/api/projects/{project['id']}/reference-evaluations/compare?{query}")
    second=client.get(
        f"/api/projects/{project['id']}/reference-evaluations/compare?{query}")

    assert first.status_code==second.status_code==200,first.text
    comparison=first.json();assert comparison==second.json()
    assert comparison['model_called'] is False and comparison['profile_changed'] is True
    assert comparison['run_id']==run['id'] and comparison['snapshot_id']==run['snapshot_id']
    assert comparison['question_set_hash']==baseline['question_set_hash']
    assert comparison['selector_version']==baseline['selector_version']=='literal-page-selector-8'
    assert comparison['summary']=={'PENDING':0,'CHANGED':1,'UNCHANGED':1,'TOTAL':2}
    unchanged,changed=comparison['items']
    assert unchanged['change']=='UNCHANGED'
    assert unchanged['baseline']['kind']==unchanged['candidate']['kind']=='RESULT'
    assert unchanged['baseline']['answer']=='The approved color is blue.'
    assert changed['change']=='CHANGED'
    assert changed['baseline']['kind']=='RESULT' and changed['candidate']['kind']=='FAILURE'
    assert 'synthetic' not in changed['candidate']['detail']
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0

    db.execute("UPDATE reference_evaluations SET selector_version='literal-page-selector-3' "
               "WHERE id=?",(candidate['evaluation_id'],))
    selector_mismatch=client.get(
        f"/api/projects/{project['id']}/reference-evaluations/compare?{query}")
    assert selector_mismatch.status_code==409
    assert 'page-selector version' in selector_mismatch.json()['detail']

    incompatible=_create(
        client,project,run,questions=['A different frozen question?'],
        name='Different question set').json()
    rejected=client.get(
        f"/api/projects/{project['id']}/reference-evaluations/compare?"
        f"baseline_evaluation_id={baseline['evaluation_id']}"
        f"&candidate_evaluation_id={incompatible['evaluation_id']}")
    assert rejected.status_code==409 and 'same run, snapshot, question set' in rejected.text

    other=client.post('/api/projects',json={'name':'Other comparison project'}).json()
    _,other_run,_,_=_saved_run(client,other,'REFERENCE_QA','EV-OTHER-COMPARISON')
    other_evaluation=_create(client,other,other_run).json()
    monkeypatch.setattr(
        client.app.state.reference_evaluations,'_public',
        lambda *_a,**_k:(_ for _ in ()).throw(
            AssertionError('Cross-project comparison expanded saved model output')))
    cross_project=client.get(
        f"/api/projects/{project['id']}/reference-evaluations/compare?"
        f"baseline_evaluation_id={baseline['evaluation_id']}"
        f"&candidate_evaluation_id={other_evaluation['evaluation_id']}")
    assert cross_project.status_code==404
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0
