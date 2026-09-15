"""FR-RENDER-001: 免费隔离入口；不调用平台或付费模型。"""
from dataclasses import replace
import base64
from pathlib import Path
import pytest
import yaml
from fastapi.testclient import TestClient
from app.main import create_app
from app.settings import Settings, ROOT
from app.render_preview import preview_settings, listen_port, PREVIEW_BYTES
from .conftest import run_demo

HOST = 'cirp-synthetic-test.onrender.com'
PASSWORD = 'synthetic-preview-password-0123456789012345'

def settings(tmp_path):
    return replace(preview_settings({'RENDER_EXTERNAL_HOSTNAME':HOST,'CIRP_PREVIEW_PASSWORD':PASSWORD},data_dir=tmp_path),start_worker=False)

@pytest.mark.parametrize('host', ['', '*.onrender.com', 'example.com','x.onrender.com.evil.test','https://x.onrender.com','x.onrender.com:443','x.onrender.com/path'])
def test_invalid_host_rejected(tmp_path,host):
    with pytest.raises(ValueError):
        preview_settings({'RENDER_EXTERNAL_HOSTNAME':host,'CIRP_PREVIEW_PASSWORD':PASSWORD},data_dir=tmp_path)

@pytest.mark.parametrize('password',['','short'])
def test_missing_or_weak_password_rejected(tmp_path,password):
    with pytest.raises(ValueError):
        preview_settings({'RENDER_EXTERNAL_HOSTNAME':HOST,'CIRP_PREVIEW_PASSWORD':password},data_dir=tmp_path)

def test_no_production_settings_inherited(tmp_path,monkeypatch):
    import app.settings as module
    monkeypatch.setattr(module,'load_dotenv',lambda *a,**k: (_ for _ in ()).throw(AssertionError('dotenv forbidden')))
    s=preview_settings({'RENDER_EXTERNAL_HOSTNAME':HOST,'CIRP_PREVIEW_PASSWORD':PASSWORD,
                        'CIRP_API_KEY':'synthetic-secret','CIRP_PROVIDER':'deepseek',
                        'CIRP_LIVE_API_ENABLED':'true','CIRP_ORIGIN_TOKEN':'synthetic-origin'*4,
                        'CIRP_DATA_DIR':'/production-data'},data_dir=tmp_path)
    assert s.data_dir==tmp_path and s.provider=='mock' and not s.api_key and not s.live_enabled
    assert not s.origin_token and s.render_free_preview and s.project_bytes==PREVIEW_BYTES
    assert Settings(tmp_path/'local').project_bytes==10_000_000_000

@pytest.mark.parametrize('change',[{'remote_enabled':False},{'project_bytes':10_000_000_000},{'live_enabled':True},{'provider':'deepseek'},{'origin_token':'x'*32}])
def test_profile_invariants(tmp_path,change):
    with pytest.raises(ValueError): replace(settings(tmp_path),**change)

def test_public_health_only_and_host(tmp_path):
    with TestClient(create_app(settings(tmp_path)),base_url='https://'+HOST) as c:
        assert c.get('/_health').json()=={'status':'ok'}
        assert c.head('/_health').status_code==200 and not c.head('/_health').content
        assert c.get('/_health',headers={'Host':'attacker.invalid'}).status_code==400
        for path in ['/','/api/settings','/api/projects','/assets/app.js','/_health/','/api/demo-files/01_original.txt']:
            assert c.get(path).status_code==401
        assert c.post('/_health').status_code==401
        assert c.get('/api/settings',headers={'X-CIRP-Origin-Token':'anything'}).status_code==401

def test_local_does_not_gain_public_health(tmp_path):
    with TestClient(create_app(Settings(tmp_path,start_worker=False))) as c:
        assert c.get('/_health').status_code==404

def test_authenticated_demo_upload_limit_and_secret_safety(tmp_path):
    with TestClient(create_app(settings(tmp_path)),base_url='https://'+HOST,
                    headers={'Authorization':'Basic '+base64.b64encode(('engineer:'+PASSWORD).encode()).decode(), 'X-CIRP-Client':'browser','Origin':'https://'+HOST}) as c:
        s=c.get('/api/settings');assert s.status_code==200 and PASSWORD not in s.text
        assert s.json()['render_free_preview'] and 'Data is lost' in s.json()['storage_warning']
        assert not s.json()['live_ready'] and not s.json()['local_single_user']
        assert c.get('/api/demo-files/01_original.txt').status_code==200
        assert c.get('/api/demo-files/secret.txt').status_code==404
        p=c.post('/api/projects',json={'name':'isolated test'}).json()['id']
        assert c.post(f'/api/projects/{p}/uploads',json={'name':'too-big.txt','size':PREVIEW_BYTES+1}).status_code==413
        run=run_demo(c,p)
        assert len(c.get(f'/api/analysis-runs/{run}/records').json())==4
        assert c.get(f'/api/analysis-runs/{run}/cost').json()['calls']==0
        assert c.get(f'/api/analysis-runs/{run}/exports/json').status_code==200
        assert c.post('/api/projects',json={'name':'bad'},headers={'Origin':'https://evil.invalid'}).status_code==403

@pytest.mark.parametrize('value',['x','-1','0','80','65536','18012','18013','19099'])
def test_invalid_port(value):
    with pytest.raises(ValueError): listen_port({'PORT':value})

def test_port_defaults_and_boundaries():
    assert listen_port({})==10000 and listen_port({'PORT':'12345'})==12345

def test_blueprint_only_explicit_free_service():
    config=yaml.safe_load((ROOT/'render.yaml').read_text(encoding='utf-8'))
    assert set(config)=={'services'} and len(config['services'])==1
    service=config['services'][0]
    assert service['type']=='web' and service['runtime']=='python' and service['plan']=='free'
    assert 'disk' not in service and 'scaling' not in service and service['autoDeployTrigger']=='off'
    assert service['startCommand']=='python -m app.render_preview' and service['healthCheckPath']=='/_health'
    env={e['key']:e for e in service['envVars']}
    assert env['CIRP_PREVIEW_PASSWORD']=={'key':'CIRP_PREVIEW_PASSWORD','generateValue':True}
    assert not any('API_KEY' in k for k in env)

def test_ui_discloses_ephemeral_and_has_demo_links():
    assert 'environment-warning' in (ROOT/'web/index.html').read_text(encoding='utf-8')
    assert 'storage_warning' in (ROOT/'web/app.js').read_text(encoding='utf-8')
    assert '/api/demo-files/01_original.txt' in (ROOT/'web/index.html').read_text(encoding='utf-8')
