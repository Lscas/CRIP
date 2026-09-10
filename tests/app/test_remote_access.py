"""FR-REMOTE-001: 默认拒绝公开访问、来源鉴权、CSRF和付费关闭。"""
from dataclasses import replace
import base64
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.settings import Settings
from scripts.remote_preview import child_environment, tunnel_url
from scripts.build_cloudflare import build
from .conftest import run_demo

PASSWORD = 'synthetic-password-only-for-tests-0123456789'
ORIGIN = 'synthetic-origin-only-for-tests-0123456789'

def settings(tmp_path):
    return Settings(tmp_path, remote_enabled=True, preview_password=PASSWORD, origin_token=ORIGIN,
                    allowed_hosts=('testserver', 'localhost', 'preview.example.com'), start_worker=False)

def test_no_password_fails_closed(tmp_path):
    with pytest.raises(ValueError): Settings(tmp_path, remote_enabled=True)

def test_wildcard_fails_closed(tmp_path):
    with pytest.raises(ValueError): replace(settings(tmp_path), allowed_hosts=('*',))

@pytest.mark.parametrize('kwargs', [{'live_enabled':True}, {'provider':'deepseek'}, {'origin_token':'short'}])
def test_paid_or_weak_origin_rejected(tmp_path,kwargs):
    with pytest.raises(ValueError): replace(settings(tmp_path), **kwargs)

@pytest.mark.parametrize('path', ['/', '/assets/app.js', '/api/settings', '/api/projects', '/docs', '/openapi.json'])
def test_all_paths_need_auth(tmp_path,path):
    with TestClient(create_app(settings(tmp_path))) as client:
        r=client.get(path)
        assert r.status_code==401 and 'Basic' in r.headers['www-authenticate']
        assert r.headers['cache-control']=='no-store'

@pytest.mark.parametrize('value', ['Bearer anything','Basic $$$$', 'Basic /w==','Basic '+base64.b64encode(b'engineer:wrong').decode()])
def test_bad_auth(tmp_path,value):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get('/api/settings',headers={'Authorization':value}).status_code==401

def test_valid_auth_and_no_secret_leak(tmp_path):
    s=settings(tmp_path)
    assert PASSWORD not in repr(s) and ORIGIN not in repr(s)
    with TestClient(create_app(s)) as client:
        r=client.get('/api/settings',auth=('engineer',PASSWORD))
        assert r.status_code==200 and r.json()['remote_preview']
        assert PASSWORD not in r.text and ORIGIN not in r.text
        assert r.json()['provider']=='mock' and not r.json()['live_ready']
        assert client.get('/openapi.json',auth=('engineer',PASSWORD)).status_code==404

def test_origin_key_only_for_api(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        h={'X-CIRP-Origin-Token':ORIGIN}
        assert client.get('/api/settings',headers=h).status_code==200
        assert client.get('/',headers=h).status_code==401
        assert client.get('/api/settings',headers={'X-CIRP-Origin-Token':'wrong'}).status_code==401

def test_remote_csrf_and_host(tmp_path):
    with TestClient(create_app(settings(tmp_path)),headers={'Authorization':'Basic '+base64.b64encode(('engineer:'+PASSWORD).encode()).decode()}) as c:
        assert c.post('/api/projects',json={'name':'x'}).status_code==403
        assert c.post('/api/projects',json={'name':'x'},headers={'X-CIRP-Client':'browser','Origin':'https://evil.example'}).status_code==403
        assert c.get('/api/settings',headers={'Host':'attacker.example'}).status_code==400
        assert c.post('/api/projects',json={'name':'x'},headers={'X-CIRP-Client':'browser','Origin':'https://testserver'}).status_code==201

def test_invalid_auth_rate_limit_but_owner_can_enter(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as c:
        for _ in range(30): assert c.get('/').status_code==401
        assert c.get('/').status_code==429
        assert c.get('/',auth=('engineer',PASSWORD)).status_code==200

def test_remote_demo_workflow(tmp_path):
    with TestClient(create_app(settings(tmp_path)),headers={'X-CIRP-Client':'browser','Authorization':'Basic '+base64.b64encode(('engineer:'+PASSWORD).encode()).decode()}) as c:
        project=c.post('/api/projects',json={'name':'remote mock'}).json()
        rid=run_demo(c,project['id'])
        assert len(c.get(f'/api/analysis-runs/{rid}/records').json())==5
        assert c.get(f'/api/analysis-runs/{rid}/cost').json()['calls']==0

def test_launcher_forces_mock(monkeypatch):
    monkeypatch.setenv('CIRP_PROVIDER','deepseek');monkeypatch.setenv('CIRP_API_KEY','not-a-real-key')
    env=child_environment('example.trycloudflare.com',PASSWORD,ORIGIN)
    assert env['CIRP_PROVIDER']=='mock' and env['CIRP_API_KEY']=='' and env['CIRP_LIVE_API_ENABLED']=='false'
    assert 'example.trycloudflare.com' in env['CIRP_ALLOWED_HOSTS']
    assert 'preview-data' in env['CIRP_DATA_DIR']

def test_tunnel_url_validation():
    assert tunnel_url('Your tunnel https://one-two.trycloudflare.com |')=='https://one-two.trycloudflare.com'
    assert tunnel_url('https://one.trycloudflare.com.attacker.example') is None
    assert tunnel_url('https://elsewhere.example') is None

def test_pages_build_contains_only_allowlist(tmp_path):
    folder=build(tmp_path/'assets')
    names={str(x.relative_to(folder)) for x in folder.rglob('*') if x.is_file()}
    assert names=={'index.html','assets/app.js','assets/i18n.js','assets/style.css','_worker.js','_routes.json','robots.txt'}
    assert 'exclude' in (folder/'_routes.json').read_text()
    with pytest.raises(ValueError): build(folder)

def test_fail_closed_api_configures_only_named_preview(tmp_path, monkeypatch):
    import httpx
    from scripts.remote_preview import configure_fail_closed
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN','synthetic-cloudflare-token')
    monkeypatch.setenv('CLOUDFLARE_ACCOUNT_ID','a'*32)
    seen=[]; original=httpx.Client
    def handler(request):
        seen.append((request.method,str(request.url)))
        assert request.headers['Authorization']=='Bearer synthetic-cloudflare-token'
        if request.method=='PATCH':
            import json
            assert json.loads(request.content)['deployment_configs']['production']['fail_open'] is False
            return httpx.Response(200,json={'success':True})
        return httpx.Response(200,json={'result':{'deployment_configs':{'production':{'fail_open':False},'preview':{'fail_open':False}}}})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs: original(transport=httpx.MockTransport(handler),**kwargs))
    configure_fail_closed('cirp-preview-test')
    assert len(seen)==2 and all(url.endswith('/pages/projects/cirp-preview-test') for _,url in seen)

def test_fail_closed_noninteractive_without_credentials_stops(monkeypatch):
    from scripts.remote_preview import configure_fail_closed
    import sys
    monkeypatch.delenv('CLOUDFLARE_API_TOKEN',raising=False)
    monkeypatch.delenv('CLOUDFLARE_ACCOUNT_ID',raising=False)
    monkeypatch.setattr(sys.stdin,'isatty',lambda:False)
    with pytest.raises(RuntimeError): configure_fail_closed('cirp-preview-test')

def test_pages_sequence_and_no_secrets_in_assets(tmp_path, monkeypatch):
    import scripts.remote_preview as remote
    calls=[]
    def fake(command,args,log,secret_input=None):
        calls.append((args,secret_input))
        return 'Published: https://abc.cirp-preview-test.pages.dev\n'
    monkeypatch.setattr(remote,'wrangler',fake)
    monkeypatch.setattr(remote,'configure_fail_closed',lambda project:calls.append((['fail-closed'],None)))
    result=remote.publish_pages('npx',tmp_path,'https://test.trycloudflare.com',PASSWORD,ORIGIN)
    assert result=='https://abc.cirp-preview-test.pages.dev'
    kinds=[args[0:3] for args,_ in calls]
    assert kinds[3]==['fail-closed'] and kinds[4]==['pages','secret','bulk']
    assert calls[4][1]['ORIGIN_TOKEN']==ORIGIN
    for path in (tmp_path/'pages-assets').rglob('*'):
        if path.is_file():
            text=path.read_text()
            assert PASSWORD not in text and ORIGIN not in text


def test_tunnel_host_rewrite_accepts_only_explicit_public_origin(tmp_path):
    with TestClient(create_app(settings(tmp_path)), headers={
        'Authorization':'Basic '+base64.b64encode(('engineer:'+PASSWORD).encode()).decode(),
        'X-CIRP-Client':'browser'}) as c:
        assert c.post('/api/projects',json={'name':'allowed'},headers={'Origin':'https://preview.example.com'}).status_code==201
        assert c.post('/api/projects',json={'name':'forbidden'},headers={'Origin':'https://preview.example.com.attacker.invalid'}).status_code==403
