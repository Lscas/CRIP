from dataclasses import replace
import hashlib
import json

import httpx
import pytest

from app.evidence_loop import ProjectEvidenceLoop
from app.db import DomainError
from app.gateway import Gateway
from app.reference_evaluations import ReferenceEvaluationStore
from app.reference_text_profiles import PROFILE_VERSION,profile,require_channel
from app.settings import DEEPSEEK_BASE_URL,Settings
from .test_evidence_loop import _provider_answer
from .test_project_qa_v2 import _qa_fixture
from .test_reference_results import _saved_run


@pytest.mark.parametrize(('profile_id','model','mode','limit'),[
    ('FLASH_NONE','deepseek-flash','disabled',2600),
    ('FLASH_LOW','deepseek-flash','thinking-low',8000),
    ('PRO','deepseek-v4-pro','disabled',2600),
])
def test_closed_reference_text_profiles_are_explicit(profile_id,model,mode,limit):
    value=profile(profile_id)
    assert value['profile_version']==PROFILE_VERSION
    assert value['profile_id']==profile_id and value['text_model']==model
    assert value['inference_mode']==mode and value['max_output_tokens']==limit
    assert value['vision_enabled'] is False and value['vision_model'] is None


def test_profile_route_is_closed_and_does_not_change_active_settings(client):
    settings=client.app.state.gateway.s
    client.app.state.gateway.s=replace(settings,provider='deepseek',api_base_url=DEEPSEEK_BASE_URL,
                                       cheap_model='deepseek-flash',api_key='synthetic-key',live_enabled=True,
                                       vision_enabled=False,structured_output_mode='json_object',
                                       api_protocol='chat_completions')
    before=client.app.state.gateway.s
    assert client.app.state.reference_evaluations.profile_for_id('FLASH_LOW',before)==profile('FLASH_LOW')
    assert client.app.state.gateway.s==before


def test_named_profile_requires_loaded_official_live_channel(client):
    with pytest.raises(Exception):
        require_channel(client.app.state.gateway.s,profile('FLASH_NONE'))


@pytest.mark.parametrize(('profile_id','expected_limit'),[
    ('FLASH_NONE',2600),('FLASH_LOW',8000),('PRO',2600),
])
def test_named_profiles_use_fixed_text_payload_and_cache_identity(
        client,project,tmp_path,profile_id,expected_limit):
    db,run,_=_qa_fixture(client,project,[{'name':'reference.pdf','fragments':[
        {'text':'The approved color is blue.'}]}])
    calls=[]
    def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        assert payload['model']==profile(profile_id)['text_model']
        assert payload['max_tokens']==expected_limit
        assert isinstance(payload['messages'][1]['content'],str)
        assert 'image_url' not in request.content.decode('utf-8')
        if profile_id=='FLASH_LOW':
            assert payload['thinking']=={'type':'enabled'} and payload['reasoning_effort']=='low'
        else:assert payload['thinking']=={'type':'disabled'}
        return httpx.Response(200,json={'id':'synthetic-'+profile_id,'choices':[{
            'finish_reason':'stop','message':{'content':json.dumps(_provider_answer())}}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    settings=Settings(tmp_path/'named-route',provider='deepseek',api_key='synthetic-key',
                      live_enabled=True,cheap_model='deepseek-flash',vision_enabled=True,
                      vision_model='deepseek-v4-flash-vision-exp',output_limit=1)
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))
    loop=ProjectEvidenceLoop(db,gateway)
    route=profile(profile_id)
    first=loop.ask(run,'What approved color applies?',route=route)
    second=loop.ask(run,'What approved color applies?',route=route)
    assert len(calls)==1 and second['execution_receipts'][0]['cached'] is True
    receipt=first['execution_receipts'][0]
    assert receipt['max_output_tokens']==expected_limit and receipt['visual_inputs']==[]
    assert receipt['initial_evidence_manifest_sha256']==second['execution_receipts'][0]['initial_evidence_manifest_sha256']
    assert first['execution_profile']['profile_id']==profile_id
    gateway.close()


def test_named_profiles_do_not_cross_recover_requests(client,project,tmp_path):
    db,run,_=_qa_fixture(client,project,[{'name':'reference.pdf','fragments':[
        {'text':'The approved color is blue.'}]}])
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'synthetic-route','choices':[{
            'finish_reason':'stop','message':{'content':json.dumps(_provider_answer())}}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    settings=Settings(tmp_path/'route-separation',provider='deepseek',api_key='synthetic-key',live_enabled=True)
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))
    loop=ProjectEvidenceLoop(db,gateway)
    none=loop.ask(run,'What approved color applies?',route=profile('FLASH_NONE'))
    low=loop.ask(run,'What approved color applies?',route=profile('FLASH_LOW'))
    assert len(requests)==2
    assert none['execution_receipts'][0]['request_hash']!=low['execution_receipts'][0]['request_hash']
    gateway.close()


def test_named_execution_replay_reauthenticates_settled_commitment(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=replace(gateway.s,provider='deepseek',api_base_url=DEEPSEEK_BASE_URL,
                      api_key='synthetic-key',live_enabled=True,
                      cheap_model='deepseek-flash',vision_enabled=False,
                      structured_output_mode='json_object',api_protocol='chat_completions')
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'named-replay','choices':[{
            'finish_reason':'stop','message':{'content':json.dumps(_provider_answer())}}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    gateway.client.close();gateway.client=httpx.Client(transport=httpx.MockTransport(handler))
    created=client.post(f"/api/projects/{project['id']}/reference-evaluations",json={
        'run_id':run['id'],'name':'Named replay integrity',
        'questions':['What approved color applies?'],'profile_id':'FLASH_NONE'})
    assert created.status_code==201,created.text
    evaluation=created.json();item=evaluation['items'][0]
    executed=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item['item_id']}/execute")
    assert executed.status_code==200,executed.text
    call=db.one('SELECT id FROM model_calls')
    db.execute('''UPDATE model_calls SET reference_input_commitment_sha256=? WHERE id=?''',
               ('0'*64,call['id']))
    replay=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item['item_id']}/execute")
    assert 400<=replay.status_code<500 and len(calls)==1


@pytest.mark.parametrize('field',[
    'initial_evidence_manifest_sha256','max_output_tokens','inference_mode',
    'layout_sha256','layout_bytes','ordered_evidence_manifest_sha256',
    'profile_neutral_input_sha256','profile_id',
])
def test_named_saved_result_tampering_cannot_bypass_receipt_commitment(client,project,field):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=replace(gateway.s,provider='deepseek',api_base_url=DEEPSEEK_BASE_URL,
                      api_key='synthetic-key',live_enabled=True,
                      cheap_model='deepseek-flash',vision_enabled=False,
                      structured_output_mode='json_object',api_protocol='chat_completions')
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'named-tamper','choices':[{
            'finish_reason':'stop','message':{'content':json.dumps(_provider_answer())}}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    gateway.client.close();gateway.client=httpx.Client(transport=httpx.MockTransport(handler))
    created=client.post(f"/api/projects/{project['id']}/reference-evaluations",json={
        'run_id':run['id'],'name':'Named saved-result integrity',
        'questions':['What approved color applies?'],'profile_id':'FLASH_NONE'})
    assert created.status_code==201,created.text
    evaluation=created.json();item=evaluation['items'][0]
    executed=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item['item_id']}/execute")
    assert executed.status_code==200,executed.text
    saved=executed.json()['result'];raw=db.one(
        'SELECT result_json FROM reference_results WHERE id=?',(saved['result_id'],))
    result=json.loads(raw['result_json']);receipt=result['execution_receipts'][0]
    if field=='profile_id':
        result['execution_profile']['profile_id']='PRO'
    elif field in ('layout_sha256','layout_bytes'):
        receipt['evidence_inputs'][0][field]=('0'*64 if field.endswith('sha256') else 1)
    elif field=='max_output_tokens':receipt[field]=1
    elif field=='inference_mode':receipt[field]='thinking-low'
    else:receipt[field]='0'*64
    altered=json.dumps(result,ensure_ascii=False,separators=(',',':'))
    db.execute('UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?',(
        altered,hashlib.sha256(altered.encode('utf-8')).hexdigest(),saved['result_id']))
    replay=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{item['item_id']}/execute")
    assert 400<=replay.status_code<500 and len(calls)==1


@pytest.mark.parametrize('field,value',[
    ('model','deepseek-v4-pro'),('inference_mode','thinking-low'),
    ('api_protocol','responses'),('structured_output_mode','json_schema'),
    ('max_output_tokens',1),('visual_inputs',[{'region_id':'VR-x'}]),
    ('initial_evidence_manifest_sha256',None),
    ('profile_neutral_input_sha256',None),
])
def test_named_profile_rejects_bad_receipt_before_attach(field,value):
    receipt={'provider':'deepseek','model':'deepseek-flash','inference_mode':'disabled',
             'api_protocol':'chat_completions','structured_output_mode':'json_object',
             'max_output_tokens':2600,'visual_inputs':[],
             'initial_evidence_manifest_sha256':'0'*64,
             'profile_neutral_input_sha256':'1'*64}
    ReferenceEvaluationStore._require_route_receipts(profile('FLASH_NONE'),[receipt])
    receipt[field]=value
    with pytest.raises(DomainError,match='does not match'):
        ReferenceEvaluationStore._require_route_receipts(profile('FLASH_NONE'),[receipt])
