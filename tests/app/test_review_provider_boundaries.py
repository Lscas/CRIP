from decimal import Decimal
from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path
import json

import httpx
import pytest

from app.gateway import Gateway, InvalidModelOutput
from app.settings import Settings
from .conftest import upload


class _Calls:
    def __init__(self): self.calls=[]
    def finalize_model_call(self, *args, **kwargs): self.calls.append((args,kwargs))


def _gateway():
    gateway=object.__new__(Gateway)
    gateway.s=SimpleNamespace(provider='deepseek')
    gateway.db=_Calls()
    return gateway


@pytest.mark.parametrize('body',[
    {'choices': 'not-a-list'},
    {'choices': [{}]},
    {'choices': [{'message': 'not-an-object'}]},
])
def test_credible_usage_with_malformed_response_envelope_is_settled_error(body):
    gateway=_gateway();usage={'prompt_tokens':3,'completion_tokens':2}
    with pytest.raises(InvalidModelOutput,match='malformed'):
        gateway._terminal_response_policy('CALL-1',Decimal('0'),usage,'provider-1',body)
    args,kwargs=gateway.db.calls[0]
    assert args[:4] == ('CALL-1',Decimal('0'),usage,'provider-1')
    assert kwargs['diagnostic']['class'] == 'RESPONSE_ENVELOPE'
    assert 'response' not in kwargs and 'cache_key' not in kwargs


def test_malformed_completion_details_is_settled_error_not_usage_unknown():
    gateway=_gateway();usage={'prompt_tokens':3,'completion_tokens':2,'completion_tokens_details':'bad'}
    with pytest.raises(InvalidModelOutput,match='malformed'):
        gateway._terminal_response_policy(
            'CALL-2',Decimal('0'),usage,'provider-2',
            {'choices':[{'message':{},'finish_reason':'stop'}]})
    assert gateway.db.calls[0][1]['diagnostic']['class'] == 'RESPONSE_ENVELOPE'


def test_valid_envelope_preserves_reasoning_policy_result():
    gateway=_gateway();usage={'prompt_tokens':3,'completion_tokens':2}
    assert gateway._terminal_response_policy(
        'CALL-3',Decimal('0'),usage,'provider-3',
        {'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]}) is False
    assert gateway.db.calls == []


@pytest.mark.parametrize('choices,details', [
    ([None], None), ([{'message': None}], None),
    ([{'finish_reason':'stop','message':{'content':'{}'}}], []),
])
def test_extract_credible_usage_malformed_envelope_settles_real_call(
        client, project, tmp_path, choices, details):
    upload(client,project['id'],'provider-boundary.txt',b'synthetic')
    run=client.app.state.runner.create(project['id'])
    client.app.state.db.execute("UPDATE runs SET status='RUNNING',provider='deepseek' WHERE id=?",(run['id'],))
    run=client.app.state.runner.get(run['id'])
    evidence=json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    evidence.update(tenant_id='local',project_id=project['id'],input_snapshot_id=run['snapshot_id'],raw_text='Synthetic.')
    usage={'prompt_tokens':7,'completion_tokens':3}
    if details is not None: usage['completion_tokens_details']=details
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'id':'provider-synthetic','choices':choices,'usage':usage})
    settings=replace(Settings(tmp_path,provider='deepseek',live_enabled=True,api_key='test-not-real',start_worker=False))
    gateway=Gateway(settings,client.app.state.db,httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(InvalidModelOutput,match='malformed'):
            gateway.extract(run,evidence)
    finally:
        gateway.close()
    row=client.app.state.db.one('SELECT state,actual_units,usage,response,error FROM model_calls WHERE run_id=?',(run['id'],))
    assert len(requests)==1 and row['state']=='SETTLED_ERROR' and row['actual_units'] is not None
    assert json.loads(row['usage']) == usage
    assert row['response'] is None and client.app.state.db.all('SELECT * FROM cache') == []
    assert 'Synthetic' not in (row['error'] or '')


def test_extract_missing_usage_remains_unknown_in_real_ledger(client, project, tmp_path):
    upload(client,project['id'],'missing-usage.txt',b'synthetic')
    run=client.app.state.runner.create(project['id'])
    client.app.state.db.execute("UPDATE runs SET status='RUNNING',provider='deepseek' WHERE id=?",(run['id'],))
    run=client.app.state.runner.get(run['id'])
    evidence=json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    evidence.update(tenant_id='local',project_id=project['id'],input_snapshot_id=run['snapshot_id'],raw_text='Synthetic.')
    gateway=Gateway(replace(Settings(tmp_path,provider='deepseek',live_enabled=True,api_key='test-not-real',start_worker=False)),
                    client.app.state.db,httpx.Client(transport=httpx.MockTransport(
                        lambda request:httpx.Response(200,json={'choices':[]}))))
    try:
        with pytest.raises(Exception,match='usage'):
            gateway.extract(run,evidence)
    finally:
        gateway.close()
    row=client.app.state.db.one('SELECT state,actual_units,response FROM model_calls WHERE run_id=?',(run['id'],))
    assert row['state']=='UNKNOWN' and row['actual_units'] is None and row['response'] is None


def test_verify_credible_usage_null_choice_settles_real_call(client, project, tmp_path):
    upload(client,project['id'],'verify-boundary.txt',b'synthetic')
    run=client.app.state.runner.create(project['id'])
    client.app.state.db.execute("UPDATE runs SET status='RUNNING',provider='deepseek' WHERE id=?",(run['id'],))
    run=client.app.state.runner.get(run['id'])
    evidence=json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    evidence.update(tenant_id='local',project_id=project['id'],input_snapshot_id=run['snapshot_id'],
                    raw_text='Valve body shall be 316 stainless steel.',locator={})
    field={'path':'/grade','claim':'316','label':'grade','evidence_ids':[evidence['evidence_id']],
           'context':{'name':'valve'},'check_key':'synthetic-check','status':'NEEDS_SEMANTIC',
           'method':'LITERAL_LOCATION_ONLY','citations':[],'issues':[],'request_id':None,'basis':'DIRECT'}
    requests=[]
    gateway=Gateway(replace(Settings(tmp_path,provider='deepseek',live_enabled=True,api_key='test-not-real',start_worker=False)),
                    client.app.state.db,httpx.Client(transport=httpx.MockTransport(
                        lambda request:(requests.append(request),httpx.Response(200,json={
                            'id':'verify-provider','choices':[None],
                            'usage':{'prompt_tokens':7,'completion_tokens':3}}))[1])))
    try:
        with pytest.raises(InvalidModelOutput,match='malformed'):
            gateway.verify_claims(run,[field],{evidence['evidence_id']:evidence})
    finally:
        gateway.close()
    row=client.app.state.db.one('SELECT state,actual_units,usage,response,error FROM model_calls WHERE run_id=?',(run['id'],))
    assert len(requests)==1 and row['state']=='SETTLED_ERROR' and row['actual_units'] is not None
    assert json.loads(row['usage']) == {'prompt_tokens':7,'completion_tokens':3}
    assert row['response'] is None and 'Valve body' not in (row['error'] or '')
