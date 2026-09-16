"""FR-BUDGET-001/002, FR-ROUTE-003, FR-TOKEN-001/002。全部使用MockTransport。"""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,sqlite3,time
import httpx,pytest
from app.db import BudgetError,DomainError,dumps,units
from app.gateway import (Gateway,ProviderPaused,InvalidModelOutput,
                         normalize_verification_contract)
from app.parsers import MAX_FRAGMENT_BYTES,parse_file
from app.settings import Settings
from contracts.runtime_rules import quote_tokens
from .conftest import upload

@pytest.fixture
def context(client,project):
    upload(client,project['id'],'a.txt',b'text')
    run=client.app.state.runner.create(project['id']);rid=run['id']
    client.app.state.db.execute("UPDATE runs SET status='RUNNING',provider='deepseek' WHERE id=?",(rid,))
    run=client.app.state.runner.get(rid)
    ev=json.loads(Path('examples/evidence.json').read_text(encoding="utf-8"))[0]
    ev.update(tenant_id='local',project_id=project['id'],input_snapshot_id=run['snapshot_id'],raw_text='Provide concrete.')
    return client.app.state.db,run,ev

def settings(tmp_path,**kw):
    s=Settings(tmp_path,provider='deepseek',live_enabled=True,prices_confirmed=True,api_key='test-not-real',
               input_rate=Decimal('1'),output_rate=Decimal('2'),start_worker=False)
    return replace(s,**kw)

def gemini_settings(tmp_path,**kw):
    values={'provider':'gemini',
            'api_base_url':'https://generativelanguage.googleapis.com/v1beta/openai',
            'cheap_model':'gemini-3.6-flash'}
    values.update(kw)
    return settings(tmp_path, **values)

def test_verification_normalizer_downgrades_only_safe_format_repairs():
    data={'checks':[{'path':'/name','status':'SUPPORTED','citations':[
        {'evidence_id':'EV-1','quote':'Concrete','role':'SUPPORT'}],
        'reason':'r'*300,'claim':'Concrete','label':'Name','evidence_ids':['EV-1'],'context':'ctx'}]}

    normalized,flags=normalize_verification_contract(data)

    check=normalized['checks'][0]
    assert set(check)=={'path','status','citations','reason'}
    assert check['status']=='NEEDS_CONTEXT' and len(check['reason'])<=240
    assert flags==['INPUT_ECHO_FIELDS','REASON_LENGTH']

def test_verification_normalizer_rejects_unknown_nested_fields():
    data={'checks':[{'path':'/name','status':'SUPPORTED','citations':[],
                     'reason':'ok','confidence':0.9}]}
    with pytest.raises(InvalidModelOutput,match='未知契约外字段'):
        normalize_verification_contract(data)

def result(ev):
    return {'disposition':'CANDIDATES','reason':'literal', 'requirements':[{
      'candidate_key':'R1','category':'MATERIAL','subject':'PAD-01','action':'provide','object':'Concrete',
      'properties':[],'condition':None,'exception':None,'parent_requirement_key':None,'option_group_key':None,
      'option_relation':'NONE','evidence_ids':[ev['evidence_id']],'context_evidence_ids':[],'needs_context':False}]}

def body(ev):return {'id':'upstream-test','choices':[{'finish_reason':'stop','message':{'content':json.dumps(result(ev))}}],
                      'usage':{'prompt_tokens':100,'completion_tokens':40}}

def test_cheap_payload_and_cache(context,tmp_path):
    db,run,ev=context;requests=[]
    def handler(r):requests.append(json.loads(r.content));return httpx.Response(200,json=body(ev))
    gateway=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(handler)))
    assert not gateway.extract(run,ev).cached
    assert gateway.extract(run,ev).cached
    assert len(requests)==1 and requests[0]['thinking']=={'type':'disabled'}
    assert 'reasoning_effort' not in requests[0]
    assert requests[0]['response_format']=={'type':'json_object'} and requests[0]['max_tokens']==8000
    assert db.cost(run['project_id'])['spent_cny']=='0.000180'
    call=db.one('SELECT provider_request_id,reserved_units,request_hash FROM model_calls WHERE run_id=?',(run['id'],))
    upper=sum(len(message['content'].encode('utf-8')) for message in requests[0]['messages'])+256
    assert call['provider_request_id']=='upstream-test'
    assert call['reserved_units']==units(quote_tokens(upper,8000,Decimal('1'),Decimal('2')))
    assert call['request_hash']==hashlib.sha256(dumps(requests[0]).encode()).hexdigest()


def test_adjacent_evidence_uses_one_paid_request_and_one_recovery_family(context,tmp_path):
    db,run,first=context;second={**first,'evidence_id':'EV-second','raw_text':'Use 2 inch diameter.'}
    expected=result(first);requirement=expected['requirements'][0]
    requirement['evidence_ids']=[first['evidence_id'],second['evidence_id']]
    requirement['properties']=[{'name':'diameter','value':'2','unit':'inches',
                                'evidence_ids':[second['evidence_id']]}]
    response={'id':'batch-call','choices':[{'finish_reason':'stop','message':{'content':json.dumps(expected)}}],
              'usage':{'prompt_tokens':150,'completion_tokens':50}}
    requests=[]
    gateway=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(json.loads(request.content)),httpx.Response(200,json=response))[1])))

    value=gateway.extract_many(run,[first,second])
    recovered=gateway.extract_many(run,[first,second])

    assert value.data==expected and recovered.cached and len(requests)==1
    user=json.loads(requests[0]['messages'][1]['content'])
    assert [item['evidence_id'] for item in user['evidence_items']]==[first['evidence_id'],second['evidence_id']]
    call=db.one('SELECT task_key,state FROM model_calls WHERE run_id=?',(run['id'],))
    assert call['task_key'].startswith('extract-batch:'+first['evidence_id']+':') and call['state']=='SETTLED'


def test_extract_stop_response_above_old_4000_limit_is_accepted(context,tmp_path):
    db,run,ev=context;requests=[];response=body(ev)
    response['id']='extract-over-4000'
    response['usage']={'prompt_tokens':500,'completion_tokens':5001}
    def handler(request):requests.append(json.loads(request.content));return httpx.Response(200,json=response)
    gateway=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(handler)))
    value=gateway.extract(run,ev)
    assert value.data==result(ev) and len(requests)==1 and requests[0]['max_tokens']==8000
    assert db.one('SELECT state FROM model_calls WHERE run_id=?',(run['id'],))['state']=='SETTLED'


def test_extract_honors_lower_settings_output_limit_in_payload_hash_and_reservation(context,tmp_path):
    db,run,ev=context;requests=[];response=body(ev)
    response['usage']={'prompt_tokens':500,'completion_tokens':2201}
    def handler(request):requests.append(json.loads(request.content));return httpx.Response(200,json=response)
    gateway=Gateway(settings(tmp_path,output_limit=2300),db,
                    httpx.Client(transport=httpx.MockTransport(handler)))
    gateway.extract(run,ev)
    payload=requests[0]
    upper=sum(len(message['content'].encode('utf-8')) for message in payload['messages'])+256
    call=db.one('SELECT reserved_units,request_hash FROM model_calls WHERE run_id=?',(run['id'],))
    assert payload['max_tokens']==2300
    assert call['reserved_units']==units(quote_tokens(upper,2300,Decimal('1'),Decimal('2')))
    assert call['request_hash']==hashlib.sha256(dumps(payload).encode()).hexdigest()


def test_extract_length_at_new_limit_is_billed_once_and_fails_closed(context,tmp_path):
    db,run,ev=context;requests=[]
    response={'id':'extract-length-8000','choices':[{'finish_reason':'length','message':{'content':'{}'}}],
              'usage':{'prompt_tokens':500,'completion_tokens':8000}}
    def handler(request):requests.append(json.loads(request.content));return httpx.Response(200,json=response)
    gateway=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(InvalidModelOutput,match='不自动重复'):
        gateway.extract(run,ev)
    with pytest.raises(InvalidModelOutput,match='不会自动再次收费'):
        gateway.extract(run,ev)
    row=db.one('SELECT state,response,error FROM model_calls WHERE run_id=?',(run['id'],))
    assert len(requests)==1 and requests[0]['max_tokens']==8000
    assert row['state']=='SETTLED_ERROR' and row['response'] is None
    assert json.loads(row['error'])['kind']=='CONTRACT_ERROR'


@pytest.mark.parametrize(('wrapper','flag'),[
    ('\ufeff%s','UTF8_BOM'),
    ('```json\n%s\n```','MARKDOWN_FENCE'),
    ('Here is the requested JSON:\n%s\nEnd of JSON.','SURROUNDING_TEXT'),
])
def test_extract_conservatively_normalizes_json_presentation_wrappers(context,tmp_path,wrapper,flag):
    db,run,ev=context;expected=result(ev);response=body(ev)
    response['id']='extract-normalized-'+flag.lower()
    response['choices'][0]['message']['content']=wrapper % json.dumps(expected)
    gateway=Gateway(settings(tmp_path),db,httpx.Client(
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))))
    value=gateway.extract(run,ev)
    assert value.data==expected
    stored=json.loads(db.one('SELECT response FROM model_calls WHERE run_id=?',(run['id'],))['response'])
    assert stored==expected


@pytest.mark.parametrize('content',[
    lambda ev:'First:\n'+json.dumps(result(ev))+'\nSecond:\n{}',
    lambda ev:'I omitted two requirements:\n'+json.dumps(result(ev)),
])
def test_extract_normalizer_rejects_ambiguous_wrappers_without_guessing(context,tmp_path,content):
    db,run,ev=context;requests=[];response=body(ev)
    response['choices'][0]['message']['content']=content(ev)
    def handler(request):requests.append(request);return httpx.Response(200,json=response)
    gateway=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(InvalidModelOutput):gateway.extract(run,ev)
    with pytest.raises(InvalidModelOutput,match='不会自动再次收费'):gateway.extract(run,ev)
    assert len(requests)==1


def test_extract_contract_normalizer_salvages_only_review_visible_structure(context,tmp_path):
    db,run,ev=context;response=body(ev);data=result(ev)
    data['unexpected_root']='discard me'
    data['reason']='r'*450
    data['requirements'][0]['properties']=[{
        'name':'thickness','value':5,'unit':'mm','evidence_ids':[ev['evidence_id']],
    },{
        'name':None,'value':'x','unit':None,'evidence_ids':[ev['evidence_id']],
    }]
    invalid=dict(data['requirements'][0]);invalid.update(candidate_key='R2',subject=None,properties=[])
    data['requirements'].append(invalid)
    response['choices'][0]['message']['content']=json.dumps(data)
    gateway=Gateway(settings(tmp_path),db,httpx.Client(
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))))

    value=gateway.extract(run,ev)

    assert value.data['disposition']=='TRUNCATED'
    assert len(value.data['requirements'])==1
    assert value.data['requirements'][0]['properties']==[
        {'name':'thickness','value':'5','unit':'mm','evidence_ids':[ev['evidence_id']]}
    ]
    assert '保守结构修复' in value.data['reason'] and len(value.data['reason'])<=400
    assert 'unexpected_root' not in json.dumps(value.data,ensure_ascii=False)
    assert db.one('SELECT state FROM model_calls WHERE run_id=?',(run['id'],))['state']=='SETTLED'


def test_extract_contract_normalizer_repairs_identity_and_missing_parent_without_inventing_facts(context,tmp_path):
    db,run,ev=context;response=body(ev);data=result(ev)
    second=json.loads(json.dumps(data['requirements'][0]))
    second.update(subject='PAD-02',candidate_key='R1',parent_requirement_key='missing-parent',needs_context=False)
    data['requirements'].append(second)
    response['choices'][0]['message']['content']=json.dumps(data)
    gateway=Gateway(settings(tmp_path),db,httpx.Client(
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))))

    value=gateway.extract(run,ev)

    assert value.data['disposition']=='TRUNCATED'
    assert [row['candidate_key'] for row in value.data['requirements']]==['R1','R1~2']
    assert value.data['requirements'][1]['parent_requirement_key'] is None
    assert value.data['requirements'][1]['needs_context'] is True
    assert [row['subject'] for row in value.data['requirements']]==['PAD-01','PAD-02']


def test_manual_extraction_generation_keeps_billing_suffix_out_of_evidence_scope(context,tmp_path):
    db,run,ev=context;requests=[]
    responses=[
        {'id':'failed-generation','choices':[{'finish_reason':'stop','message':{'content':'not-json'}}],
         'usage':{'prompt_tokens':500,'completion_tokens':100}},
        {'id':'manual-generation','choices':[{'finish_reason':'stop','message':{'content':json.dumps(result(ev))}}],
         'usage':{'prompt_tokens':500,'completion_tokens':100}},
    ]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=responses[len(requests)-1])
    gateway=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(InvalidModelOutput):
        gateway.extract(run,ev)
    source=db.one("SELECT id,task_key,state FROM model_calls WHERE run_id=?",(run['id'],))
    family=ev['evidence_id'];next_key=family+':manual-requeue:1'
    event={
        'event_type':'RECOVERY_GENERATION_AUTHORIZED','policy':'EXPLICIT_ONLY_MAX_3',
        'task_family':family,'generation':1,'attempt_number':2,'source_call_id':source['id'],
        'source_task_key':source['task_key'],'source_state':source['state'],'trigger':'RUN_RESUME',
        'context_id':'offline-contract-requeue','next_task_key':next_key,
    }
    db.execute('INSERT INTO call_reconciliation_events VALUES(?,?,?,?,?,?,?)',
               ('RECOVERY-test',source['id'],run['project_id'],run['id'],'local-user',dumps(event),
                '2026-09-13T00:00:00+00:00'))

    value=gateway.extract(run,ev)
    sent=json.loads(requests[1]['messages'][1]['content'])
    calls=db.all('SELECT task_key,state FROM model_calls WHERE run_id=? ORDER BY created_at,id',(run['id'],))
    assert value.data==result(ev)
    assert sent['evidence_id']==family and ':manual-requeue:' not in sent['evidence_id']
    assert calls==[{'task_key':family,'state':'SETTLED_ERROR'},
                   {'task_key':next_key,'state':'SETTLED'}]


def test_deepseek_vision_uses_image_model_same_budget_and_cache(context,tmp_path):
    db,run,ev=context;requests=[]
    vision={'page_type':'DRAWING','sheet_id':'A-101','important_visible_text':'SCALE: 1/4" = 1\'-0"',
            'observations':['Floor plan with door tags.'],'explicit_quantity_texts':[],
            'scale_text':'1/4" = 1\'-0"','limitations':['Small notes need review.'],'needs_review':True}
    response={'id':'vision-upstream','choices':[{'finish_reason':'stop','message':{'content':json.dumps(vision)}}],
              'usage':{'prompt_tokens':500,'completion_tokens':100}}
    def handler(request):requests.append(json.loads(request.content));return httpx.Response(200,json=response)
    gateway=Gateway(settings(tmp_path,vision_enabled=True),db,httpx.Client(transport=httpx.MockTransport(handler)))
    metadata={'document_id':'D-1','page':1,'coordinate_system':'image-pixels-top-left','width':800,'height':600}
    first=gateway.vision(run,'D-1:1',b'not-a-real-png-needed-by-mock',metadata)
    second=gateway.vision(run,'D-1:1',b'not-a-real-png-needed-by-mock',metadata)
    assert not first.cached and second.cached and len(requests)==1
    payload=requests[0]
    assert payload['model']=='deepseek-v4-flash-vision-exp'
    assert payload['thinking']=={'type':'disabled'} and isinstance(payload['messages'][1]['content'],list)
    assert payload['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    call=db.one("SELECT model,task_key FROM model_calls WHERE run_id=? AND task_key LIKE 'vision:%'",(run['id'],))
    assert call['model']=='deepseek-v4-flash-vision-exp' and call['task_key']=='vision:D-1:1'
    assert db.cost(run['project_id'])['spent_cny']=='0.000700'


def test_vision_stop_response_above_old_1200_limit_is_accepted(context,tmp_path):
    db,run,ev=context;requests=[]
    vision={'page_type':'DRAWING','sheet_id':'A-901','important_visible_text':'x'*3000,
            'observations':['Dense construction sheet.'],'explicit_quantity_texts':[],
            'scale_text':'1:100','limitations':['Review small notes.'],'needs_review':True}
    response={'id':'vision-over-1200','choices':[{'finish_reason':'stop','message':{'content':json.dumps(vision)}}],
              'usage':{'prompt_tokens':500,'completion_tokens':1301}}
    def handler(request):requests.append(json.loads(request.content));return httpx.Response(200,json=response)
    gateway=Gateway(settings(tmp_path,vision_enabled=True),db,
                    httpx.Client(transport=httpx.MockTransport(handler)))
    value=gateway.vision(run,'D-1:long-valid',b'fake-png',{
        'document_id':'D-1','page':1,'coordinate_system':'image-pixels-top-left','width':80,'height':60})
    assert value.data['sheet_id']=='A-901' and len(value.data['important_visible_text'])==3000
    assert len(requests)==1 and requests[0]['max_tokens']==2000
    assert db.one("SELECT state FROM model_calls WHERE task_key='vision:D-1:long-valid'")['state']=='SETTLED'


def test_vision_length_at_new_limit_is_billed_once_and_fails_closed(context,tmp_path):
    db,run,ev=context;requests=[]
    response={'id':'vision-length-2000','choices':[{'finish_reason':'length','message':{'content':'{}'}}],
              'usage':{'prompt_tokens':500,'completion_tokens':2000}}
    def handler(request):requests.append(json.loads(request.content));return httpx.Response(200,json=response)
    gateway=Gateway(settings(tmp_path,vision_enabled=True),db,
                    httpx.Client(transport=httpx.MockTransport(handler)))
    metadata={'document_id':'D-1','page':2,'coordinate_system':'image-pixels-top-left','width':80,'height':60}
    with pytest.raises(InvalidModelOutput,match='不自动重复'):
        gateway.vision(run,'D-1:length',b'fake-png',metadata)
    with pytest.raises(InvalidModelOutput,match='不会自动再次收费'):
        gateway.vision(run,'D-1:length',b'fake-png',metadata)
    row=db.one("SELECT state,response,error FROM model_calls WHERE task_key='vision:D-1:length'")
    assert len(requests)==1 and row['state']=='SETTLED_ERROR' and row['response'] is None
    assert json.loads(row['error'])['kind']=='CONTRACT_ERROR'


def test_vision_conservatively_normalizes_non_semantic_json_deviations(context,tmp_path):
    db,run,ev=context
    raw={'page_type':'COVER_SHEET','sheet_id':'','important_visible_text':'x'*4100,
         'observations':['visible']*14,'explicit_quantity_texts':[],
         'scale_text':'','limitations':[],'needs_review':False,'extra':'discard me'}
    response={'id':'vision-normalized','choices':[{'finish_reason':'stop','message':{
              'content':'```json\n'+json.dumps(raw)+'\n```'}}],
              'usage':{'prompt_tokens':500,'completion_tokens':100}}
    gateway=Gateway(settings(tmp_path,vision_enabled=True),db,httpx.Client(
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))))
    result_value=gateway.vision(run,'D-1:normalize',b'fake-png',{
        'document_id':'D-1','page':1,'coordinate_system':'image-pixels-top-left','width':80,'height':60})
    assert result_value.data['page_type']=='UNKNOWN' and result_value.data['sheet_id'] is None
    assert result_value.data['needs_review'] is True and len(result_value.data['important_visible_text'])==4000
    assert len(result_value.data['observations'])==12 and 'extra' not in result_value.data
    assert any('保守规范化' in value for value in result_value.data['limitations'])


def test_irrecoverable_vision_contract_error_records_only_safe_diagnostic(context,tmp_path):
    db,run,ev=context
    requests=[]
    response={'id':'vision-invalid','choices':[{'finish_reason':'stop','message':{'content':'not-json'}}],
              'usage':{'prompt_tokens':500,'completion_tokens':100}}
    gateway=Gateway(settings(tmp_path,vision_enabled=True),db,httpx.Client(
        transport=httpx.MockTransport(lambda request:(requests.append(request),httpx.Response(200,json=response))[1])))
    with pytest.raises(InvalidModelOutput):
        gateway.vision(run,'D-1:invalid',b'fake-png',{
            'document_id':'D-1','page':1,'coordinate_system':'image-pixels-top-left','width':80,'height':60})
    with pytest.raises(InvalidModelOutput,match='不会自动再次收费'):
        gateway.vision(run,'D-1:invalid',b'fake-png',{
            'document_id':'D-1','page':1,'coordinate_system':'image-pixels-top-left','width':80,'height':60})
    call=db.one("SELECT state,actual_units,error,response FROM model_calls WHERE task_key='vision:D-1:invalid'")
    diagnostic=json.loads(call['error'])
    assert len(requests)==1 and call['state']=='SETTLED_ERROR'
    assert call['actual_units'] is not None and call['response'] is None
    assert diagnostic=={'kind':'CONTRACT_ERROR','class':'VISION_RESULT','exception':'JSONDecodeError'}
    assert 'not-json' not in call['error']


def test_atomic_finalize_rolls_back_accounting_response_and_cache_together(context):
    db,run,ev=context
    attempt=db.reserve(run['project_id'],run['id'],'atomic-failure',Decimal('2'),
                       'model','hash',Decimal('1'),Decimal('2'))
    with db.connect(True) as connection:
        connection.execute('''CREATE TRIGGER reject_atomic_cache BEFORE INSERT ON cache
                              BEGIN SELECT RAISE(ABORT,'synthetic cache failure'); END''')
    with pytest.raises(sqlite3.IntegrityError,match='synthetic cache failure'):
        db.finalize_model_call(attempt,Decimal('1'),{'prompt_tokens':1,'completion_tokens':2},
                               'provider-safe',response=result(ev),cache_key='atomic-key')
    row=db.one('SELECT state,actual_units,usage,provider_request_id,response,error FROM model_calls WHERE id=?',
               (attempt,))
    assert row=={'state':'RESERVED','actual_units':None,'usage':None,'provider_request_id':None,
                 'response':None,'error':None}
    assert db.one('SELECT spent_units FROM budget_accounts WHERE project_id=?',(run['project_id'],))['spent_units']==0
    assert db.one('SELECT key FROM cache WHERE key=?',('atomic-key',),False) is None


def test_atomic_finalize_rejects_raw_failure_text_before_settlement(context):
    db,run,ev=context
    attempt=db.reserve(run['project_id'],run['id'],'unsafe-diagnostic',Decimal('1'),
                       'model','hash',Decimal('1'),Decimal('2'))
    with pytest.raises(DomainError,match='安全终态格式'):
        db.finalize_model_call(attempt,Decimal('0.1'),{'prompt_tokens':1,'completion_tokens':1},None,
                               diagnostic={'kind':'CONTRACT_ERROR','class':'EXTRACTION_RESULT',
                                           'message':'raw provider response must not persist'})
    row=db.one('SELECT state,actual_units,error FROM model_calls WHERE id=?',(attempt,))
    assert row=={'state':'RESERVED','actual_units':None,'error':None}


def test_extract_crash_after_atomic_commit_recovers_without_second_http(context,tmp_path,monkeypatch):
    db,run,ev=context;requests=[]
    gateway=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(200,json=body(ev)))[1])))
    committed=db.finalize_model_call
    def commit_then_crash(*args,**kwargs):
        committed(*args,**kwargs)
        raise SystemExit('synthetic crash after commit')
    monkeypatch.setattr(db,'finalize_model_call',commit_then_crash)
    with pytest.raises(SystemExit,match='after commit'):
        gateway.extract(run,ev)
    monkeypatch.setattr(db,'finalize_model_call',committed)

    recovered=gateway.extract(run,ev)
    assert recovered.cached and recovered.request_id and recovered.data==result(ev)
    assert len(requests)==1 and db.cost(run['project_id'])['calls']==1


def test_vision_crash_after_atomic_commit_recovers_without_second_http(context,tmp_path,monkeypatch):
    db,run,ev=context;requests=[]
    data={'page_type':'DRAWING','sheet_id':'A-101','important_visible_text':'DOOR D-1',
          'observations':['One tag.'],'explicit_quantity_texts':[],'scale_text':None,
          'limitations':['Review source page.'],'needs_review':True}
    response={'id':'vision-crash','choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}],
              'usage':{'prompt_tokens':500,'completion_tokens':100}}
    gateway=Gateway(settings(tmp_path,vision_enabled=True),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(200,json=response))[1])))
    committed=db.finalize_model_call
    def commit_then_crash(*args,**kwargs):
        committed(*args,**kwargs)
        raise SystemExit('synthetic crash after vision commit')
    monkeypatch.setattr(db,'finalize_model_call',commit_then_crash)
    metadata={'document_id':'D-1','page':4,'coordinate_system':'image-pixels-top-left','width':80,'height':60}
    with pytest.raises(SystemExit,match='vision commit'):
        gateway.vision(run,'D-1:4',b'fake-png',metadata)
    monkeypatch.setattr(db,'finalize_model_call',committed)

    recovered=gateway.vision(run,'D-1:4',b'fake-png',metadata)
    assert recovered.cached and recovered.data==data
    assert len(requests)==1 and db.cost(run['project_id'])['calls']==1


def test_explicit_vision_generation_is_a_new_call_and_keeps_failed_history(context,tmp_path):
    db,run,ev=context;requests=[]
    valid={'page_type':'DRAWING','sheet_id':'A-1','important_visible_text':'Scale 1:100',
           'observations':[],'explicit_quantity_texts':[],'scale_text':'1:100',
           'limitations':['Review required.'],'needs_review':True}
    responses=[
        {'id':'failed-generation','choices':[{'finish_reason':'stop','message':{'content':'not-json'}}],
         'usage':{'prompt_tokens':500,'completion_tokens':100}},
        {'id':'manual-generation','choices':[{'finish_reason':'stop','message':{'content':json.dumps(valid)}}],
         'usage':{'prompt_tokens':500,'completion_tokens':100}},
    ]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=responses[len(requests)-1])
    gateway=Gateway(settings(tmp_path,vision_enabled=True),db,
                    httpx.Client(transport=httpx.MockTransport(handler)))
    metadata={'document_id':'D-1','page':8,'coordinate_system':'image-pixels-top-left','width':80,'height':60}
    with pytest.raises(InvalidModelOutput):
        gateway.vision(run,'D-1:8',b'fake-png',metadata)
    retried=gateway.vision(run,'D-1:8',b'fake-png',metadata,billing_generation=1)
    recovered=gateway.vision(run,'D-1:8',b'fake-png',metadata,billing_generation=1)

    rows=db.all("SELECT task_key,state,error,response FROM model_calls WHERE task_key LIKE 'vision:D-1:8%' ORDER BY created_at")
    assert len(requests)==2 and retried.data==valid and recovered.cached
    assert [row['task_key'] for row in rows]==[
        'vision:D-1:8','vision:D-1:8:manual-requeue:1']
    assert rows[0]['state']=='SETTLED_ERROR' and rows[0]['error'] and rows[0]['response'] is None
    assert rows[1]['state']=='SETTLED' and rows[1]['error'] is None and rows[1]['response']

def test_gemini_payload_uses_minimal_reasoning_and_bills_hidden_output(context,tmp_path):
    db,run,ev=context;run={**run,'provider':'gemini'};requests=[];response=body(ev)
    response['choices'][0]['message']['reasoning_content']='expected internal reasoning marker'
    response['usage']['total_tokens']=150
    def handler(r):requests.append(json.loads(r.content));return httpx.Response(200,json=response)
    gateway=Gateway(gemini_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(handler)))
    result_value=gateway.extract(run,ev)
    assert result_value.mode=='gemini' and not result_value.cached
    assert len(requests)==1 and requests[0]['reasoning_effort']=='minimal'
    assert 'thinking' not in requests[0]
    cost=db.cost(run['project_id'])
    assert cost['output_tokens']==50 and cost['spent_cny']=='0.000200'


def test_loopback_openai_compatible_model_uses_no_key_or_provider_specific_payload(context,tmp_path):
    db,run,ev=context;provider='custom-0123456789abcdef';run={**run,'provider':provider};requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=body(ev))
    local=Settings(tmp_path,provider=provider,live_enabled=True,prices_confirmed=True,
                   api_base_url='http://127.0.0.1:11434/v1',cheap_model='qwen3:8b',
                   input_rate=Decimal('0'),output_rate=Decimal('0'),start_worker=False)
    value=Gateway(local,db,httpx.Client(transport=httpx.MockTransport(handler))).extract(run,ev)
    payload=json.loads(requests[0].content)
    assert value.mode==provider and 'authorization' not in requests[0].headers
    assert 'thinking' not in payload and 'reasoning_effort' not in payload
    assert db.cost(run['project_id'])['spent_cny']=='0.000000'
    assert db.one('SELECT state,actual_units FROM model_calls WHERE run_id=?',(run['id'],))=={
        'state':'SETTLED','actual_units':0}


def test_live_calls_observe_configured_minimum_start_interval(context,tmp_path):
    db,run,ev=context;starts=[]
    def handler(request):
        starts.append(time.monotonic())
        user=json.loads(json.loads(request.content)['messages'][1]['content'])
        current={**ev,'evidence_id':user['evidence_id']}
        return httpx.Response(200,json=body(current))
    g=Gateway(settings(tmp_path,min_request_interval_seconds=.02),db,
              httpx.Client(transport=httpx.MockTransport(handler)))
    second={**ev,'evidence_id':'EV-second','raw_text':'Provide steel.'}
    g.extract(run,ev);g.extract(run,second)
    assert len(starts)==2 and starts[1]-starts[0]>=.015

def test_long_chinese_parser_fragments_fit_default_gateway_envelope(context,tmp_path):
    db,run,ev=context;text='施工材料与检查要求。'*156
    source=tmp_path/'long-zh.txt';source.write_text(text,encoding='utf-8')
    fragments=parse_file(source,source.name)['fragments'];requests=[]
    def handler(request):
        payload=json.loads(request.content);requests.append(payload)
        user=json.loads(payload['messages'][1]['content']);current={**ev,'evidence_id':user['evidence_id']}
        return httpx.Response(200,json=body(current))
    gateway=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(handler)))
    for index,fragment in enumerate(fragments):
        current={**ev,'evidence_id':f'EV-zh-{index}','raw_text':fragment['text'],'locator':fragment['locator']}
        gateway.extract(run,current)
    assert ''.join(f['text'] for f in fragments)==text and len(fragments)>1
    assert all(len(f['text'].encode('utf-8'))<=MAX_FRAGMENT_BYTES for f in fragments)
    envelope_sizes=[sum(len(m['content'].encode('utf-8')) for m in p['messages'])+256 for p in requests]
    assert any(size>6000 for size in envelope_sizes)
    assert all(size<=32000 for size in envelope_sizes)


def test_extraction_input_byte_envelope_remains_bounded_before_http(context,tmp_path):
    db,run,ev=context;calls=[]
    oversized={**ev,'raw_text':'X'*32000}
    gateway=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(calls.append(request),httpx.Response(200,json=body(oversized)))[1])))
    with pytest.raises(InvalidModelOutput,match='输入预算'):
        gateway.extract(run,oversized)
    assert calls==[] and db.cost(run['project_id'])['calls']==0

@pytest.mark.parametrize('change',[
    {'api_base_url':'https://example.invalid/v1beta/openai'},
    {'cheap_model':'gemini-3.8-flash'},
])
def test_gemini_identity_gate_blocks_before_http(context,tmp_path,change):
    db,run,ev=context
    g=Gateway(gemini_settings(tmp_path,**change),db,
              httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError()))))
    with pytest.raises(ProviderPaused):g.extract(run,ev)
    assert db.cost(run['project_id'])['calls']==0

@pytest.mark.parametrize('change',[
    {'api_base_url':'https://example.invalid'},
    {'cheap_model':'deepseek-v4-pro'},
])
def test_deepseek_identity_gate_blocks_before_http(context,tmp_path,change):
    db,run,ev=context
    g=Gateway(settings(tmp_path,**change),db,
              httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError()))))
    with pytest.raises(ProviderPaused):g.extract(run,ev)
    assert db.cost(run['project_id'])['calls']==0

def test_provider_mismatch_blocks_before_http(context,tmp_path):
    db,run,ev=context
    g=Gateway(gemini_settings(tmp_path),db,
              httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError()))))
    with pytest.raises(ProviderPaused,match='Provider'):
        g.extract(run,ev)
    assert db.cost(run['project_id'])['calls']==0

def test_gemini_missing_total_tokens_keeps_reservation_unknown(context,tmp_path):
    db,run,ev=context;run={**run,'provider':'gemini'}
    g=Gateway(gemini_settings(tmp_path),db,
              httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body(ev)))))
    with pytest.raises(ProviderPaused,match='usage'):
        g.extract(run,ev)
    assert db.cost(run['project_id'])['unknown_calls']==1
    assert json.loads(db.one('SELECT error FROM model_calls WHERE run_id=?',(run['id'],))['error'])=={
        'kind':'INVALID_RESPONSE','class':'MISSING_USAGE'}

@pytest.mark.parametrize('change',[{'api_key':''},{'live_enabled':False},{'prices_confirmed':False},
 {'api_base_url':'http://unsafe.test'},{'input_rate':Decimal('NaN')},{'output_rate':Decimal('-1')},
 {'input_limit':32001},{'output_limit':8001}])
def test_live_gate_no_http(context,tmp_path,change):
    db,run,ev=context
    def handler(r):raise AssertionError('不应发出HTTP')
    g=Gateway(settings(tmp_path,**change),db,httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderPaused):g.extract(run,ev)
    assert db.cost(run['project_id'])['calls']==0

def test_budget_blocks_before_http(context,tmp_path):
    db,run,ev=context;db.execute('UPDATE budget_accounts SET spent_units=300000000 WHERE project_id=?',(run['project_id'],))
    g=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError()))))
    with pytest.raises(BudgetError):g.extract(run,ev)
    assert db.cost(run['project_id'])['calls']==0

def test_timeout_no_retry_retains_reservation(context,tmp_path):
    db,run,ev=context;calls=[]
    def handler(r):calls.append(r);raise httpx.ReadTimeout('timeout')
    g=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderPaused):g.extract(run,ev)
    with pytest.raises(ProviderPaused):g.extract(run,ev)
    assert len(calls)==1 and db.cost(run['project_id'])['unknown_calls']==1
    assert Decimal(db.cost(run['project_id'])['reserved_cny'])>0
    row=db.one('SELECT error,provider_request_id FROM model_calls WHERE run_id=?',(run['id'],))
    assert json.loads(row['error'])=={'kind':'NETWORK_ERROR','class':'TIMEOUT','exception':'ReadTimeout'}
    assert row['provider_request_id'] is None


@pytest.mark.parametrize(('status','provider_code','expected_class','message'),[
    (400,'INVALID_ARGUMENT','REQUEST_REJECTED','参数'),
    (401,'UNAUTHENTICATED','AUTHENTICATION','鉴权'),
    (403,'PERMISSION_DENIED','PERMISSION','权限'),
    (404,'NOT_FOUND','NOT_FOUND','不存在'),
    (408,'DEADLINE_EXCEEDED','TRANSIENT_TIMEOUT','超时'),
    (429,'RESOURCE_EXHAUSTED','RATE_LIMIT_OR_QUOTA','配额'),
    (503,'UNAVAILABLE','PROVIDER_TRANSIENT','暂时不可用'),
])
def test_http_status_is_safely_classified_without_body_or_retry(context,tmp_path,status,provider_code,
                                                                 expected_class,message):
    db,run,ev=context;requests=[]
    headers={'x-goog-request-id':'safe-request-123'}
    if status==429:headers['Retry-After']='60'
    def handler(request):
        requests.append(request)
        return httpx.Response(status,headers=headers,json={'error':{
            'status':provider_code,'message':'secret-in-body Provide concrete.'}})
    g=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderPaused,match=message) as caught:g.extract(run,ev)
    assert f'HTTP {status}' in str(caught.value)
    if status==429:assert 'Retry-After=60' in str(caught.value)
    with pytest.raises(ProviderPaused):g.extract(run,ev)
    assert len(requests)==1
    row=db.one('SELECT state,error,provider_request_id,response FROM model_calls WHERE run_id=?',(run['id'],))
    diagnostic=json.loads(row['error'])
    assert diagnostic['kind']=='HTTP_STATUS' and diagnostic['status']==status
    assert diagnostic['class']==expected_class and diagnostic['provider_code']==provider_code
    assert diagnostic['provider_request_id']=='safe-request-123'
    assert ('retry_after' in diagnostic)==(status==429)
    assert row['state']=='UNKNOWN' and row['provider_request_id']=='safe-request-123' and row['response'] is None
    assert 'secret-in-body' not in row['error'] and 'Provide concrete.' not in row['error']


def test_success_status_with_invalid_json_keeps_sanitized_diagnostic(context,tmp_path):
    db,run,ev=context
    response=httpx.Response(200,headers={'x-request-id':'json-request-1'},content=b'not-json-secret')
    g=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(lambda request:response)))
    with pytest.raises(ProviderPaused,match='JSON'):g.extract(run,ev)
    row=db.one('SELECT error,provider_request_id FROM model_calls WHERE run_id=?',(run['id'],))
    assert json.loads(row['error'])=={'kind':'INVALID_RESPONSE','status':200,'class':'INVALID_JSON',
                                      'provider_request_id':'json-request-1'}
    assert row['provider_request_id']=='json-request-1' and 'not-json-secret' not in row['error']


def test_untrusted_diagnostic_headers_are_not_persisted_or_displayed(context,tmp_path):
    db,run,ev=context
    response=httpx.Response(429,headers={'Retry-After':'soon; forged','x-request-id':'bad request id'},
                            json={'error':{'status':'RESOURCE_EXHAUSTED','message':'private'}})
    g=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(lambda request:response)))
    with pytest.raises(ProviderPaused) as caught:g.extract(run,ev)
    assert 'Retry-After' not in str(caught.value)
    row=db.one('SELECT error,provider_request_id FROM model_calls WHERE run_id=?',(run['id'],))
    diagnostic=json.loads(row['error'])
    assert 'retry_after' not in diagnostic and 'provider_request_id' not in diagnostic
    assert row['provider_request_id'] is None and 'forged' not in row['error']


@pytest.mark.parametrize('api_key',['test-not-real','abc123'])
def test_api_key_shaped_provider_fields_are_never_persisted(context,tmp_path,api_key):
    db,run,ev=context
    response=httpx.Response(403,headers={'x-request-id':api_key},
                            json={'error':{'status':api_key,'code':api_key,'message':api_key}})
    g=Gateway(settings(tmp_path,api_key=api_key),db,
              httpx.Client(transport=httpx.MockTransport(lambda request:response)))
    with pytest.raises(ProviderPaused):g.extract(run,ev)
    row=db.one('SELECT error,provider_request_id FROM model_calls WHERE run_id=?',(run['id'],))
    diagnostic=json.loads(row['error'])
    assert diagnostic=={'kind':'HTTP_STATUS','status':403,'class':'PERMISSION'}
    assert row['provider_request_id'] is None and api_key not in row['error']


def test_contract_diagnostic_never_persists_key_shaped_validation_path(context,tmp_path):
    db,run,ev=context;g=Gateway(settings(tmp_path,api_key='test-not-real'),db)
    class SyntheticValidationError(ValueError):
        absolute_path=['test-not-real']
        validator='required'
    diagnostic=g._terminal_diagnostic('EXTRACTION_RESULT',SyntheticValidationError())
    assert diagnostic=={'kind':'CONTRACT_ERROR','class':'EXTRACTION_RESULT',
                        'exception':'SyntheticValidationError','validator':'required'}
    assert 'test-not-real' not in dumps(diagnostic)
    g.close()


@pytest.mark.parametrize('upstream_id',[
    'test-not-real',
    'prefix-test-not-real-suffix',
    'x' * 161,
    'contains spaces',
])
def test_success_body_provider_id_cannot_persist_key_or_unbounded_text(context,tmp_path,upstream_id):
    db,run,ev=context
    response=body(ev);response['id']=upstream_id
    gateway=Gateway(settings(tmp_path),db,httpx.Client(
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response))))
    gateway.extract(run,ev)
    call=db.one('SELECT provider_request_id FROM model_calls WHERE run_id=?',(run['id'],))
    assert call['provider_request_id'] is None

@pytest.mark.parametrize('kind',['no_usage','truncated','bad_json','invented_evidence','extra_review_field'])
def test_invalid_response_not_published(context,tmp_path,kind):
    db,run,ev=context;b=body(ev);requests=[]
    if kind=='no_usage':b.pop('usage')
    elif kind=='truncated':b['choices'][0]['finish_reason']='length'
    elif kind=='bad_json':b['choices'][0]['message']['content']='{bad'
    else:
        data=result(ev)
        if kind=='invented_evidence':data['requirements'][0]['evidence_ids']=['EV-not-provided']
        else:data['requirements'][0]['review_status']='ACCEPTED'
        b['choices'][0]['message']['content']=json.dumps(data)
    g=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(200,json=b))[1])))
    with pytest.raises((ProviderPaused,InvalidModelOutput)):g.extract(run,ev)
    cost=db.cost(run['project_id'])
    assert cost['calls']==1
    if kind=='no_usage':
        assert cost['unknown_calls']==1
    else:
        with pytest.raises(InvalidModelOutput,match='不会自动再次收费'):
            g.extract(run,ev)
        row=db.one('SELECT state,error,response FROM model_calls WHERE run_id=?',(run['id'],))
        assert Decimal(cost['spent_cny'])>0 and len(requests)==1
        assert row['state']=='SETTLED_ERROR' and row['response'] is None
        diagnostic=json.loads(row['error'])
        assert diagnostic['kind']=='CONTRACT_ERROR' and diagnostic['class']=='EXTRACTION_RESULT'
        assert '{bad' not in row['error'] and 'EV-not-provided' not in row['error']

def test_persistent_budget_and_settlement_idempotency(context):
    db,run,ev=context
    aid=db.reserve(run['project_id'],run['id'],'x',Decimal('2'),'model','hash',Decimal('1'),Decimal('2'))
    db.settle(aid,Decimal('1'),{'prompt_tokens':1,'completion_tokens':2},None)
    db.settle(aid,Decimal('1'),{'completion_tokens':2,'prompt_tokens':1},None)
    with pytest.raises(DomainError):db.settle(aid,Decimal('2'),{},None)
    with pytest.raises(BudgetError,match='禁止自动重复收费'):
        db.reserve(run['project_id'],run['id'],'x',Decimal('1'),'model','new-hash',Decimal('1'),Decimal('2'))
    from app.db import Database
    assert Database(db.path).cost(run['project_id'])['spent_cny']=='1.000000'

def test_budget_race_only_one_large_reservation(context):
    db,run,ev=context
    def reserve(i):
        try:return db.reserve(run['project_id'],run['id'],str(i),Decimal('200'),'m','h',Decimal('1'),Decimal('2'))
        except BudgetError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(reserve,[1,2]))
    assert sum(v is not None for v in values)==1
    assert db.cost(run['project_id'])['reserved_cny']=='200.000000'


def test_same_task_reservation_is_idempotent_across_concurrent_writers(context):
    db,run,ev=context
    def reserve(_):
        try:
            return db.reserve(run['project_id'],run['id'],'same-paid-task',Decimal('1'),
                              'm','h',Decimal('1'),Decimal('2'))
        except BudgetError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=list(pool.map(reserve,[1,2]))
    assert sum(value is not None for value in values)==1
    assert db.one("SELECT COUNT(*) AS n FROM model_calls WHERE task_key='same-paid-task'")['n']==1

def test_overage_freezes_account(context):
    db,run,ev=context
    aid=db.reserve(run['project_id'],run['id'],'x',Decimal('1'),'m','h',Decimal('1'),Decimal('2'))
    db.settle(aid,Decimal('2'),{},None)
    assert db.cost(run['project_id'])['frozen']
    with pytest.raises(BudgetError):db.reserve(run['project_id'],run['id'],'y',Decimal('1'),'m','h',Decimal('1'),Decimal('2'))

def test_deadline_stops_new_billable_work(context):
    db,run,ev=context;db.execute('UPDATE runs SET deadline_epoch=? WHERE id=?',(time.time()-1,run['id']))
    with pytest.raises(BudgetError):db.reserve(run['project_id'],run['id'],'x',Decimal('1'),'m','h',Decimal('1'),Decimal('2'))


def test_property_without_evidence_is_rejected(context,tmp_path):
    db,run,ev=context;b=body(ev);data=result(ev)
    data['requirements'][0]['properties']=[{'name':'grade','value':'A','unit':None,'evidence_ids':[]}]
    b['choices'][0]['message']['content']=json.dumps(data)
    g=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=b))))
    with pytest.raises(InvalidModelOutput):g.extract(run,ev)
    assert Decimal(db.cost(run['project_id'])['spent_cny'])>0


def test_unexpected_reasoning_pauses_after_accounting(context,tmp_path):
    db,run,ev=context;b=body(ev);requests=[];b['choices'][0]['message']['reasoning_content']='unexpected'
    g=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(200,json=b))[1])))
    with pytest.raises(ProviderPaused):g.extract(run,ev)
    with pytest.raises(ProviderPaused,match='禁止自动重复收费'):g.extract(run,ev)
    row=db.one('SELECT state,error,response FROM model_calls WHERE run_id=?',(run['id'],))
    assert len(requests)==1 and Decimal(db.cost(run['project_id'])['spent_cny'])>0
    assert row['state']=='SETTLED_ERROR' and row['response'] is None
    assert json.loads(row['error'])=={'kind':'POLICY_ERROR','class':'REASONING_NOT_DISABLED'}
