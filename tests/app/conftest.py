from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.settings import Settings

@pytest.fixture
def client(tmp_path):
    app=create_app(Settings(tmp_path,start_worker=False))
    with TestClient(app,headers={'X-CIRP-Client':'browser'}) as c:yield c

@pytest.fixture
def project(client):return client.post('/api/projects',json={'name':'Test project'}).json()

def upload(client,pid,name,raw):
    u=client.post(f'/api/projects/{pid}/uploads',json={'name':name,'size':len(raw)})
    assert u.status_code==201,u.text
    u=u.json()
    if raw:
        r=client.put(f'/api/uploads/{u["id"]}/chunk?offset=0',content=raw)
        assert r.status_code==200,r.text
    r=client.post(f'/api/uploads/{u["id"]}/complete')
    assert r.status_code==200,r.text
    return r.json()

def run_demo(client,pid):
    for path in sorted(Path('examples/demo').glob('*.txt')):upload(client,pid,path.name,path.read_bytes())
    r=client.post(f'/api/projects/{pid}/analysis-runs')
    assert r.status_code==202,r.text
    rid=r.json()['id'];client.app.state.runner.process(rid)
    return rid
