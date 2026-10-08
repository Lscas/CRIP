import httpx
import pytest
import json
import subprocess
import sys
from pathlib import Path
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from scripts.reference_live_smoke import run
from .test_page_selector import _raw_run
from .test_complete_context import _no_answer
from app.reference_text_profiles import profile


def test_live_smoke_never_dispatches_without_explicit_confirmation():
    with pytest.raises(ValueError,match='confirmation'):
        run(None,{},['Q1'])


def test_live_smoke_help_runs_from_an_arbitrary_working_directory(tmp_path):
    script=Path(__file__).resolve().parents[2]/'scripts'/'reference_live_smoke.py'
    completed=subprocess.run([sys.executable,str(script),'--help'],cwd=tmp_path,
                             capture_output=True,text=True,check=False)
    assert completed.returncode==0,completed.stderr
    assert '--profile-id' in completed.stdout and '--confirm-live' in completed.stdout


def test_live_smoke_stops_unresolved_before_evaluation_creation():
    paths=[]
    def handler(request):
        paths.append((request.method,request.url.path))
        value={'provider':'deepseek','model':'deepseek-flash','reasoning_effort':'none','vision_enabled':False}
        if request.url.path.endswith('unresolved-model-calls'):value=[{'id':'synthetic-unresolved'}]
        return httpx.Response(200,json=value)
    fixture={'frozen_context':{'project_id':'P1','run_id':'R1'},'questions':[{'id':'Q1','question':'What is the source?'}]}
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError,match='Unresolved'):
            run(client,fixture,['Q1'],confirmed=True,profile_id='FLASH_LOW')
    assert all(method=='GET' for method,_ in paths)


def test_controlled_runner_uses_saved_evaluation_and_never_reexecutes_terminal_items(tmp_path):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'id':'synthetic-smoke','choices':[
            {'finish_reason':'stop','message':{'content':json.dumps(_no_answer())}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50}})
    app=create_app(Settings(tmp_path,provider='deepseek',cheap_model='deepseek-flash',
                            api_key='offline-synthetic-key',live_enabled=True,start_worker=False))
    gateway=app.state.gateway
    gateway.client.close();gateway.client=httpx.Client(transport=httpx.MockTransport(handler))
    with TestClient(app,headers={'X-CIRP-Client':'browser'}) as client:
        project=client.post('/api/projects',json={'name':'Synthetic smoke project'}).json()
        db,saved_run,_=_raw_run(client,project,[{'text':'Approved source color is blue.'}])
        fixture={'frozen_context':{'project_id':project['id'],'run_id':saved_run['id'],
                                   'snapshot_id':saved_run['snapshot_id']},
                 'questions':[{'id':'Q1','question':'Which source color is approved?'}]}
        report=run(client,fixture,['Q1'],confirmed=True)
        row=report['items'][0]
        assert row['status']=='CANNOT_ANSWER' and row['receipt_count']==1
        assert row['supplement_rounds']==row['supplement_requests']==0
        assert row['all_receipts_cached'] is False and len(calls)==1
        assert db.one('SELECT id FROM reference_results WHERE id=?',(row['result_id'],))['id']==row['result_id']
        recovered=run(client,fixture,['Q1'],confirmed=True,evaluation_id=report['evaluation_id'])
        assert recovered['items'][0]['already_terminal'] is True
        assert len(calls)==1 and db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==1


@pytest.mark.parametrize(('profile_id','model','cap'),[
    ('FLASH_NONE','deepseek-flash',2600),('FLASH_LOW','deepseek-flash',8000),
    ('PRO','deepseek-v4-pro',2600),
])
def test_live_smoke_named_profile_uses_existing_channel_without_global_switch(
        tmp_path,profile_id,model,cap):
    calls=[]
    def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        assert payload['model']==model and payload['max_tokens']==cap
        return httpx.Response(200,json={'id':'synthetic-named','choices':[
            {'finish_reason':'stop','message':{'content':json.dumps(_no_answer())}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5}})
    app=create_app(Settings(tmp_path,provider='deepseek',cheap_model='deepseek-flash',
                            api_key='offline-synthetic-key',live_enabled=True,
                            vision_enabled=True,vision_model='deepseek-v4-flash-vision-exp',
                            start_worker=False))
    app.state.gateway.client.close();app.state.gateway.client=httpx.Client(transport=httpx.MockTransport(handler))
    with TestClient(app,headers={'X-CIRP-Client':'browser'}) as client:
        project=client.post('/api/projects',json={'name':'Named profile smoke'}).json()
        _,saved_run,_=_raw_run(client,project,[{'text':'Approved source color is blue.'}])
        fixture={'frozen_context':{'project_id':project['id'],'run_id':saved_run['id'],
                                   'snapshot_id':saved_run['snapshot_id']},
                 'questions':[{'id':'Q1','question':'Which source color is approved?'}]}
        before=client.app.state.gateway.s
        report=run(client,fixture,['Q1'],confirmed=True,profile_id=profile_id)
        assert report['profile']==profile(profile_id) and client.app.state.gateway.s==before
        assert len(calls)==1


def test_live_smoke_named_profile_refuses_wrong_existing_evaluation(tmp_path):
    app=create_app(Settings(tmp_path,provider='deepseek',cheap_model='deepseek-flash',
                            api_key='offline-synthetic-key',live_enabled=True,start_worker=False))
    with TestClient(app,headers={'X-CIRP-Client':'browser'}) as client:
        project=client.post('/api/projects',json={'name':'Wrong profile resume'}).json()
        _,saved_run,_=_raw_run(client,project,[{'text':'Approved source color is blue.'}])
        fixture={'frozen_context':{'project_id':project['id'],'run_id':saved_run['id'],
                                   'snapshot_id':saved_run['snapshot_id']},
                 'questions':[{'id':'Q1','question':'Which source color is approved?'}]}
        created=client.post(f"/api/projects/{project['id']}/reference-evaluations",json={
            'run_id':saved_run['id'],'name':'None profile','questions':[fixture['questions'][0]['question']],
            'profile_id':'FLASH_NONE'}).json()
        with pytest.raises(ValueError,match='named text profile'):
            run(client,fixture,['Q1'],confirmed=True,evaluation_id=created['evaluation_id'],profile_id='FLASH_LOW')
