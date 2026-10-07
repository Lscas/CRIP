"""Offline v9 named-route gateway coverage; all provider responses are synthetic."""
import json
import hashlib

import httpx
import pytest

from app.evidence_loop import ProjectEvidenceLoop
from app.gateway import Gateway,InvalidModelOutput
from app.page_selector import LAYOUT_BOUND_SELECTOR_VERSION
from app.reference_text_profiles import profile
from app.settings import Settings
from .test_page_selector import _raw_run,_spatial_text


def _need_evidence():
    return {'status':'NEED_EVIDENCE','reason_code':'MISSING_SOURCE_TEXT',
            'missing_facts':['Supplemental source is required.'],
            'requests':[{'tool':'SEARCH_TEXT','query':'Supplement'}],
            'answer':{'claims':[],'calculations':[],'coverage':[]}}


def _cannot_answer():
    return {'status':'CANNOT_ANSWER','reason_code':'NO_NEW_EVIDENCE',
            'missing_facts':['Synthetic terminal boundary.'],'requests':[],
            'answer':{'claims':[],'calculations':[],'coverage':[]}}


def test_v8_gateway_path_remains_receipt_v2_without_layout_navigation(tmp_path,client,project):
    db,run,_=_raw_run(client,project,[{'page':1,'text':'Frozen v8 source.'}])
    received=[]
    def handler(request):
        received.append(json.loads(request.content))
        return httpx.Response(200,json={'id':'synthetic-v8',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(_cannot_answer())}}],
            'usage':{'prompt_tokens':100,'completion_tokens':30}})
    gateway=Gateway(Settings(tmp_path/'v8',provider='deepseek',api_key='synthetic-key',
                             live_enabled=True),db,httpx.Client(transport=httpx.MockTransport(handler)))
    result=ProjectEvidenceLoop(db,gateway).ask(run,'What frozen information is available?')
    assert result['execution_receipts'][0]['receipt_version']=='reference-model-input-receipt-2'
    content=json.loads(received[0]['messages'][1]['content'])
    assert 'layout_navigation' not in content
    gateway.close()


@pytest.mark.parametrize(('profile_id','expected_model'),[
    ('FLASH_NONE','deepseek-flash'),('FLASH_LOW','deepseek-flash'),('PRO','deepseek-v4-pro'),
])
def test_v9_named_route_rebuilds_navigation_each_round_and_caches(
        tmp_path,client,project,profile_id,expected_model):
    initial,initial_map=_spatial_text([(10,[('Initial',10,48),('511',52,72)]),
                                        (30,[('NOTE',10,44)])])
    supplement,supplement_map=_spatial_text([(10,[('Supplement',10,70),('593',74,94)]),
                                              (30,[('NOTE',10,44)])])
    # A right column on each page makes the layout eligible without asserting
    # any semantic relationship from the geometry.
    initial+=' RIGHT';initial_map.append({'start':len(initial)-5,'end':len(initial),'bbox':[300,10,340,20]})
    supplement+=' RIGHT';supplement_map.append({'start':len(supplement)-5,'end':len(supplement),'bbox':[300,10,340,20]})
    db,run,_=_raw_run(client,project,[
        {'page':1,'sheet':'A5.01','text':initial,'text_map':initial_map},
        {'page':2,'sheet':'A5.02','text':supplement,'text_map':supplement_map},
    ])
    received=[]
    def handler(request):
        payload=json.loads(request.content);content=json.loads(payload['messages'][1]['content'])
        received.append((payload,content))
        result=_need_evidence() if len(received)==1 else _cannot_answer()
        return httpx.Response(200,json={'id':'synthetic-v9',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':30}})
    gateway=Gateway(Settings(tmp_path/'v9',provider='deepseek',api_key='synthetic-key',
                             live_enabled=True,cheap_model='deepseek-flash'),db,
                    httpx.Client(transport=httpx.MockTransport(handler)))
    service=ProjectEvidenceLoop(db,gateway)
    route=profile(profile_id)
    question='On Sheet A5.01, what initial information is available?'
    result=service.ask(run,question,selector_version=LAYOUT_BOUND_SELECTOR_VERSION,route=route)
    replay=service.ask(run,question,selector_version=LAYOUT_BOUND_SELECTOR_VERSION,route=route)
    assert result['model_call_count']==2 and replay['model_call_count']==0 and len(received)==2
    assert [receipt['receipt_version'] for receipt in result['execution_receipts']]==[
        'reference-model-input-receipt-3','reference-model-input-receipt-3']
    for payload,content in received:
        assert payload['model']==expected_model
        assert content['layout_navigation']['navigation_version']=='source-layout-navigation-1'
        assert all('layout_lines' not in item for item in content['evidence'])
        assert content['layout_navigation']['limitations']==[
            'Navigation is non-citable and does not establish semantic support.']
    first_receipt,second_receipt=result['execution_receipts']
    assert first_receipt['visual_inputs']==[] and first_receipt['image_bytes']==0
    assert all('layout_sha256' not in item and 'layout_bytes' not in item
               for item in first_receipt['evidence_inputs'])
    assert first_receipt['layout_navigation_sha256']!=second_receipt['layout_navigation_sha256']
    assert first_receipt['initial_evidence_manifest_sha256']==second_receipt['initial_evidence_manifest_sha256']
    assert first_receipt['prompt_contract_hash']==hashlib.sha256(
        received[0][0]['messages'][0]['content'].encode('utf-8')).hexdigest()
    assert first_receipt['system_text_bytes']==len(
        received[0][0]['messages'][0]['content'].encode('utf-8'))
    gateway.close()


def test_v9_rejects_legacy_layout_carrier_and_non_named_or_visual_route(tmp_path,client,project):
    db,run,_=_raw_run(client,project,[{'page':1,'text':'Initial source.'}])
    gateway=Gateway(Settings(tmp_path/'v9-reject',provider='deepseek',api_key='synthetic-key',
                             live_enabled=True),db,httpx.Client(transport=httpx.MockTransport(
                                 lambda request: pytest.fail('v9 preflight must not call HTTP'))))
    rows=[{'evidence_id':'EV-1','document_id':'D','raw_text':'Initial source.',
           'prompt_text':'Initial source.','extraction_method':'TEXT_LAYER','text_map':[],
           'locator':{'page_number':1},'layout_lines':''}]
    with pytest.raises(InvalidModelOutput,match='legacy layout'):
        gateway.evidence_decision_v3(run,'What is available?',rows,[],[],0,
                                     selector_version=LAYOUT_BOUND_SELECTOR_VERSION,
                                     route=profile('FLASH_NONE'),
                                     initial_evidence_manifest_sha256='a'*64)
    with pytest.raises(InvalidModelOutput,match='canonical named'):
        gateway.evidence_decision_v3(run,'What is available?',[{key:value for key,value in rows[0].items() if key!='layout_lines'}],
                                     [],[],0,selector_version=LAYOUT_BOUND_SELECTOR_VERSION,
                                     initial_evidence_manifest_sha256='a'*64)
    gateway.close()


def test_v9_named_contract_failure_reuses_one_settled_call_with_v3_receipt(tmp_path,client,project):
    db,run,_=_raw_run(client,project,[{'page':1,'text':'Initial source.'}])
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'id':'synthetic-v9-invalid',
            'choices':[{'finish_reason':'stop','message':{'content':'{}'}}],
            'usage':{'prompt_tokens':100,'completion_tokens':30}})
    gateway=Gateway(Settings(tmp_path/'v9-failure',provider='deepseek',api_key='synthetic-key',
                             live_enabled=True),db,httpx.Client(transport=httpx.MockTransport(handler)))
    rows=[{'evidence_id':'EV-1','document_id':'D','raw_text':'LEFT RIGHT',
           'prompt_text':'LEFT RIGHT','extraction_method':'TEXT_LAYER',
           'text_map':[{'start':0,'end':4,'bbox':[10,10,40,20]},
                       {'start':5,'end':10,'bbox':[300,10,340,20]}],
           'locator':{'page_number':1}}]
    kwargs={'selector_version':LAYOUT_BOUND_SELECTOR_VERSION,'route':profile('FLASH_NONE'),
            'initial_evidence_manifest_sha256':'a'*64}
    with pytest.raises(InvalidModelOutput) as first:
        gateway.evidence_decision_v3(run,'What is available?',rows,[],[],0,**kwargs)
    with pytest.raises(InvalidModelOutput) as replay:
        gateway.evidence_decision_v3(run,'What is available?',rows,[],[],0,**kwargs)
    assert len(calls)==1
    assert first.value.execution_receipt['receipt_version']=='reference-model-input-receipt-3'
    assert replay.value.execution_receipt['receipt_version']=='reference-model-input-receipt-3'
    assert replay.value.execution_receipt['cached'] is True
    gateway.close()


def test_v9_whitespace_equivalent_questions_keep_actual_request_identity(tmp_path,client,project):
    db,run,_=_raw_run(client,project,[{'page':1,'text':'Finish PT9 is blue. RIGHT'}])
    received=[]
    def handler(request):
        payload=json.loads(request.content);received.append(payload)
        return httpx.Response(200,json={'id':f'synthetic-space-{len(received)}',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(_cannot_answer())}}],
            'usage':{'prompt_tokens':100,'completion_tokens':30}})
    gateway=Gateway(Settings(tmp_path/'v9-spacing',provider='deepseek',api_key='synthetic-key',
                             live_enabled=True),db,httpx.Client(transport=httpx.MockTransport(handler)))
    service=ProjectEvidenceLoop(db,gateway);route=profile('FLASH_NONE')
    original='What approved color applies to Finish PT9?'
    equivalent=' What  approved\ncolor applies to  Finish PT9? '
    first=service.ask(run,original,selector_version=LAYOUT_BOUND_SELECTOR_VERSION,route=route)
    second=service.ask(run,equivalent,selector_version=LAYOUT_BOUND_SELECTOR_VERSION,route=route)
    replay=service.ask(run,original,selector_version=LAYOUT_BOUND_SELECTOR_VERSION,route=route)
    first_receipt=first['execution_receipts'][0];second_receipt=second['execution_receipts'][0]
    assert first['model_call_count']==second['model_call_count']==1
    assert replay['model_call_count']==0 and len(received)==2
    assert first_receipt['question_hash']==second_receipt['question_hash']
    assert first_receipt['request_hash']!=second_receipt['request_hash']
    assert first_receipt['profile_neutral_input_sha256']!=second_receipt['profile_neutral_input_sha256']
    assert [json.loads(payload['messages'][1]['content'])['question'] for payload in received]==[
        original,equivalent]
    assert db.one("SELECT COUNT(*) AS n FROM model_calls WHERE state!='SETTLED'")['n']==0
    gateway.close()


def test_v9_profiles_share_neutral_input_and_manifest_but_not_request_key(tmp_path,client,project):
    db,run,_=_raw_run(client,project,[{'page':1,'text':'Finish PT9 is blue. RIGHT'}])
    received=[]
    def handler(request):
        payload=json.loads(request.content);received.append(payload)
        return httpx.Response(200,json={'id':f'synthetic-profile-{len(received)}',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(_cannot_answer())}}],
            'usage':{'prompt_tokens':100,'completion_tokens':30}})
    gateway=Gateway(Settings(tmp_path/'v9-profiles',provider='deepseek',api_key='synthetic-key',
                             live_enabled=True),db,httpx.Client(transport=httpx.MockTransport(handler)))
    service=ProjectEvidenceLoop(db,gateway);question='What approved color applies to Finish PT9?'
    results=[service.ask(run,question,selector_version=LAYOUT_BOUND_SELECTOR_VERSION,
                         route=profile(profile_id))
             for profile_id in ('FLASH_NONE','FLASH_LOW','PRO')]
    receipts=[result['execution_receipts'][0] for result in results]
    assert len(received)==3
    assert len({receipt['profile_neutral_input_sha256'] for receipt in receipts})==1
    assert len({receipt['initial_evidence_manifest_sha256'] for receipt in receipts})==1
    assert len({receipt['request_hash'] for receipt in receipts})==3
    assert [payload['model'] for payload in received]==[
        'deepseek-flash','deepseek-flash','deepseek-v4-pro']
    gateway.close()


def test_v9_large_complete_rows_and_unicode_supplement_are_never_truncated(tmp_path,client,project):
    initial=[{'page':1,'text':(
        f'InitialToken {index:03d} ' + '多字节证据'*120 +
        (f' TAIL-INITIAL-{index:03d}' if index==299 else ''))}
        for index in range(300)]
    supplement=[{'page':2,'text':(
        f'SupplementToken {index:02d} ' + '补充原文'*120 +
        (f' TAIL-SUPPLEMENT-{index:02d}' if index==9 else ''))}
        for index in range(10)]
    db,run,_=_raw_run(client,project,initial+supplement)
    received=[]
    def handler(request):
        payload=json.loads(request.content);content=json.loads(payload['messages'][1]['content'])
        received.append(content)
        decision=({'status':'NEED_EVIDENCE','reason_code':'MISSING_SOURCE_TEXT',
                   'missing_facts':['Supplemental source is required.'],
                   'requests':[{'tool':'SEARCH_TEXT','query':'SupplementToken'}],
                   'answer':{'claims':[],'calculations':[],'coverage':[]}}
                  if len(received)==1 else _cannot_answer())
        return httpx.Response(200,json={'id':f'synthetic-large-{len(received)}',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(decision)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':30}})
    gateway=Gateway(Settings(tmp_path/'v9-large',provider='deepseek',api_key='synthetic-key',
                             live_enabled=True),db,httpx.Client(transport=httpx.MockTransport(handler)))
    result=ProjectEvidenceLoop(db,gateway).ask(
        run,'What InitialToken is recorded?',selector_version=LAYOUT_BOUND_SELECTOR_VERSION,
        route=profile('FLASH_NONE'))
    first,second=received;first_receipt,second_receipt=result['execution_receipts']
    assert len(first['evidence'])==300 and len(second['evidence'])==310
    assert first['evidence'][-1]['text'].endswith('TAIL-INITIAL-299')
    assert any(item['text'].endswith('TAIL-SUPPLEMENT-09') for item in second['evidence'])
    assert first_receipt['evidence_count']==300 and second_receipt['evidence_count']==310
    assert first_receipt['user_text_bytes']>64*1024 and second_receipt['user_text_bytes']>64*1024
    assert first_receipt['source_text_bytes']==sum(
        len(item['text'].encode('utf-8')) for item in first['evidence'])
    assert second_receipt['source_text_bytes']==sum(
        len(item['text'].encode('utf-8')) for item in second['evidence'])
    assert first_receipt['ordered_evidence_manifest_sha256']!=second_receipt['ordered_evidence_manifest_sha256']
    gateway.close()
