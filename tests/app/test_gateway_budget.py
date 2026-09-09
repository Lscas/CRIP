"""FR-BUDGET-001/002, FR-ROUTE-003, FR-TOKEN-001/002。全部使用MockTransport。"""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json,time
import httpx,pytest
from app.db import BudgetError,DomainError
from app.gateway import Gateway,ProviderPaused,InvalidModelOutput
from app.settings import Settings
from .conftest import upload

@pytest.fixture
def context(client,project):
    upload(client,project['id'],'a.txt',b'text')
    run=client.app.state.runner.create(project['id']);rid=run['id']
    client.app.state.db.execute("UPDATE runs SET status='RUNNING' WHERE id=?",(rid,))
    run=client.app.state.runner.get(rid)
    ev=json.loads(Path('examples/evidence.json').read_text(encoding="utf-8"))[0]
    ev.update(tenant_id='local',project_id=project['id'],input_snapshot_id=run['snapshot_id'],raw_text='Provide concrete.')
    return client.app.state.db,run,ev

def settings(tmp_path,**kw):
    s=Settings(tmp_path,provider='deepseek',live_enabled=True,prices_confirmed=True,api_key='test-not-real',
               input_rate=Decimal('1'),output_rate=Decimal('2'),start_worker=False)
    return replace(s,**kw)

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
    assert requests[0]['response_format']=={'type':'json_object'} and requests[0]['max_tokens']==2000
    assert db.cost(run['project_id'])['spent_cny']=='0.000180'

@pytest.mark.parametrize('change',[{'api_key':''},{'live_enabled':False},{'prices_confirmed':False},
 {'api_base_url':'http://unsafe.test'},{'input_rate':Decimal('NaN')},{'output_rate':Decimal('-1')}])
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

@pytest.mark.parametrize('kind',['no_usage','truncated','bad_json','invented_evidence','extra_review_field'])
def test_invalid_response_not_published(context,tmp_path,kind):
    db,run,ev=context;b=body(ev)
    if kind=='no_usage':b.pop('usage')
    elif kind=='truncated':b['choices'][0]['finish_reason']='length'
    elif kind=='bad_json':b['choices'][0]['message']['content']='{bad'
    else:
        data=result(ev)
        if kind=='invented_evidence':data['requirements'][0]['evidence_ids']=['EV-not-provided']
        else:data['requirements'][0]['review_status']='ACCEPTED'
        b['choices'][0]['message']['content']=json.dumps(data)
    g=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=b))))
    with pytest.raises((ProviderPaused,InvalidModelOutput)):g.extract(run,ev)
    cost=db.cost(run['project_id'])
    assert cost['calls']==1
    assert (cost['unknown_calls']==1) if kind=='no_usage' else (Decimal(cost['spent_cny'])>0)

def test_persistent_budget_and_settlement_idempotency(context):
    db,run,ev=context
    aid=db.reserve(run['project_id'],run['id'],'x',Decimal('2'),'model','hash',Decimal('1'),Decimal('2'))
    db.settle(aid,Decimal('1'),{'prompt_tokens':1,'completion_tokens':2},None)
    db.settle(aid,Decimal('1'),{'prompt_tokens':1,'completion_tokens':2},None)
    with pytest.raises(DomainError):db.settle(aid,Decimal('2'),{},None)
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
    db,run,ev=context;b=body(ev);b['choices'][0]['message']['reasoning_content']='unexpected'
    g=Gateway(settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=b))))
    with pytest.raises(ProviderPaused):g.extract(run,ev)
    assert Decimal(db.cost(run['project_id'])['spent_cny'])>0
