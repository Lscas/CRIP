"""Read-only Autodesk and Procore imports use synthetic MockTransport only."""
import hashlib
import httpx


def replace_transport(client,handler):
    connectors=client.app.state.connectors;connectors.client.close()
    connectors.client=httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=False)
    return connectors


def configure(client,provider,**values):
    payload={'access_token':'synthetic-secret-token','project_id':'b.project' if provider=='autodesk' else '22',
             'hub_id':'b.hub' if provider=='autodesk' else '',
             'company_id':'11' if provider=='procore' else '',
             'folder_id':'','remember':False,**values}
    response=client.post(f'/api/connectors/{provider}',json=payload)
    assert response.status_code==200,response.text
    assert response.json()['configured'] and 'token' not in response.text.lower()


def test_autodesk_browse_and_import_reuses_upload_hash_pipeline(client,project):
    configure(client,'autodesk')
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.headers['authorization']=='Bearer synthetic-secret-token'
        if request.url.path.endswith('/topFolders'):
            return httpx.Response(200,json={'data':[{'type':'folders','id':'folder-1',
                'attributes':{'displayName':'Project Files'}}]})
        if request.url.path.endswith('/items/item-1/tip'):
            return httpx.Response(200,json={'data':{'attributes':{'displayName':'drawing.pdf','storageSize':4},
                'relationships':{'storage':{'data':{'id':'urn:adsk.objects:os.object:wip.dm.prod/object.pdf'}}}}})
        if request.url.path.endswith('/oss/v2/buckets/wip.dm.prod/objects/object.pdf'):
            return httpx.Response(200,content=b'%PDF',headers={'content-length':'4'})
        return httpx.Response(404)
    replace_transport(client,handler)

    items=client.get('/api/connectors/autodesk/items').json()
    imported=client.post(f'/api/projects/{project["id"]}/connector-imports',
                         json={'provider':'autodesk','remote_id':'item-1'})

    assert items==[{'remote_id':'folder-1','kind':'FOLDER','name':'Project Files','size':None}]
    assert imported.status_code==201 and imported.json()['state']=='COMPLETE'
    manifest=client.get(f'/api/projects/{project["id"]}/manifest').json()
    assert manifest['documents'][0]['name']=='drawing.pdf'
    assert manifest['documents'][0]['sha256']==hashlib.sha256(b'%PDF').hexdigest()
    assert len(calls)==3


def test_procore_browse_and_import_never_forwards_token_to_signed_storage(client,project):
    configure(client,'procore')
    storage_headers=[]
    def handler(request):
        if request.url.host=='storage.procore.com':
            storage_headers.append(dict(request.headers));return httpx.Response(200,content=b'DATA',headers={'content-length':'4'})
        assert request.headers['authorization']=='Bearer synthetic-secret-token'
        assert request.headers['procore-company-id']=='11'
        if request.url.path=='/rest/v1.0/folders':
            return httpx.Response(200,json={'folders':[],'files':[{'id':7,'name':'spec.pdf','size':4}]})
        if request.url.path=='/rest/v1.0/files/7':
            return httpx.Response(200,json={'id':7,'name':'spec.pdf','size':4,'file_versions':[{
                'id':8,'number':2,'size':4,'prostore_file':{'url':'https://storage.procore.com/signed/spec.pdf'}}]})
        return httpx.Response(404)
    replace_transport(client,handler)

    items=client.get('/api/connectors/procore/items').json()
    imported=client.post(f'/api/projects/{project["id"]}/connector-imports',
                         json={'provider':'procore','remote_id':'7'})

    assert items[0]=={'remote_id':'7','kind':'FILE','name':'spec.pdf','size':4}
    assert imported.status_code==201 and imported.json()['state']=='COMPLETE'
    assert storage_headers and 'authorization' not in storage_headers[0]


def test_connector_rejects_untrusted_download_location_and_never_exposes_token(client,project):
    configure(client,'procore')
    def handler(request):
        return httpx.Response(200,json={'name':'unsafe.pdf','file_versions':[{
            'number':1,'size':4,'url':'https://evil.invalid/file.pdf'}]})
    replace_transport(client,handler)

    response=client.post(f'/api/projects/{project["id"]}/connector-imports',
                         json={'provider':'procore','remote_id':'7'})
    status=client.get('/api/connectors').text

    assert response.status_code==502 and 'untrusted' in response.json()['detail'].lower()
    assert 'synthetic-secret-token' not in response.text+status
    assert client.get(f'/api/projects/{project["id"]}/manifest').json()['documents']==[]


def test_disconnect_removes_in_memory_connection(client):
    configure(client,'autodesk')
    response=client.delete('/api/connectors/autodesk')
    assert response.status_code==200 and not response.json()['configured']
    assert client.get('/api/connectors/autodesk/items').status_code==409


def test_reconfigure_replaces_saved_connection_without_retaining_stale_token(client,monkeypatch):
    saved={}
    monkeypatch.setattr('app.connectors.save_api_key',lambda _data,name,value:saved.__setitem__(name,value))
    monkeypatch.setattr('app.connectors.load_api_key',lambda _data,name:saved.get(name))
    monkeypatch.setattr('app.connectors.forget_api_key',lambda _data,name:saved.pop(name,None))
    first={'access_token':'first-synthetic-token','project_id':'b.project','hub_id':'b.hub',
           'company_id':'','folder_id':'','remember':True}
    second={**first,'access_token':'second-synthetic-token','remember':False}

    remembered=client.post('/api/connectors/autodesk',json=first).json()
    replaced=client.post('/api/connectors/autodesk',json=second).json()

    assert remembered['saved_for_current_windows_user'] is True
    assert replaced['saved_for_current_windows_user'] is False
    assert saved=={}
    assert client.app.state.connectors._bundle('autodesk')['access_token']=='second-synthetic-token'


def test_connector_validation_never_echoes_rejected_token(client):
    token='private-synthetic-token-'+('x'*17000)
    response=client.post('/api/connectors/autodesk',json={
        'access_token':token,'project_id':'b.project','hub_id':'b.hub','remember':False})
    assert response.status_code==422
    assert token not in response.text and 'private-synthetic-token' not in response.text
