"""FR-INGEST-003/004, FR-REVIEW-001/002, FR-EXPORT-001, PRD-PROTOTYPE-001"""
import io,json
from pathlib import Path
from copy import deepcopy
import pytest
from openpyxl import load_workbook
from .conftest import upload,run_demo


def test_health_and_no_key_exposed(client):
    assert client.get('/').status_code==200
    s=client.get('/api/settings').json()
    assert s['provider']=='mock' and not s['live_ready']
    assert 'api_key' not in s and s['thinking']=='disabled'

@pytest.mark.parametrize('name',['','   '])
def test_invalid_name(client,name):assert client.post('/api/projects',json={'name':name}).status_code in (400,422)

def test_extra_fields_rejected(client):assert client.post('/api/projects',json={'name':'x','admin':True}).status_code==422

def test_cross_origin_blocked(client):
    assert client.post('/api/projects',json={'name':'x'},headers={'Origin':'https://evil.invalid'}).status_code==403
    assert client.get('/api/projects',headers={'Host':'evil.invalid'}).status_code==400

def test_header_required(client):
    assert client.post('/api/projects',json={'name':'x'},headers={'X-CIRP-Client':''}).status_code==403

def test_resumable_duplicate_and_corrupt_chunk(client,project):
    u=client.post(f'/api/projects/{project["id"]}/uploads',json={'name':'a.txt','size':6}).json()
    base=f'/api/uploads/{u["id"]}'
    assert client.put(base+'/chunk?offset=0',content=b'abc').json()['offset']==3
    assert client.put(base+'/chunk?offset=0',content=b'abc').json()['replayed']
    assert client.put(base+'/chunk?offset=0',content=b'xxx').status_code==409
    assert client.post(base+'/complete').status_code==409
    assert client.put(base+'/chunk?offset=4',content=b'de').status_code==409
    assert client.put(base+'/chunk?offset=3',content=b'def').status_code==200
    result=client.post(base+'/complete').json()
    assert result['state']=='COMPLETE'
    assert len(client.get(base).json()['chunks'])==2
    assert client.post(base+'/complete').json()['document_id']==result['document_id']
    duplicate=upload(client,project['id'],'another.txt',b'abcdef')
    assert duplicate['state']=='DUPLICATE' and duplicate['document_id']==result['document_id']

def test_upload_limit_and_basename(client,project):
    assert client.post(f'/api/projects/{project["id"]}/uploads',json={'name':'huge.txt','size':10000000001}).status_code==413
    u=upload(client,project['id'],'../../outside.txt',b'abc')
    assert u['name']=='outside.txt'

def test_chunk_limit(client,project):
    u=client.post(f'/api/projects/{project["id"]}/uploads',json={'name':'x.txt','size':6000000}).json()
    assert client.put(f'/api/uploads/{u["id"]}/chunk',content=b'x'*(4*1024*1024+1)).status_code==413

def test_abort(client,project):
    u=client.post(f'/api/projects/{project["id"]}/uploads',json={'name':'x.txt','size':100}).json()
    assert client.post(f'/api/uploads/{u["id"]}/abort').json()['state']=='ABORTED'
    assert client.put(f'/api/uploads/{u["id"]}/chunk',content=b'x').status_code==409

def test_empty_project_no_run(client,project):assert client.post(f'/api/projects/{project["id"]}/analysis-runs').status_code==409

def test_one_active_project_and_pause(client,project):
    upload(client,project['id'],'a.txt',b'hello')
    a=client.post(f'/api/projects/{project["id"]}/analysis-runs').json()
    assert client.post(f'/api/projects/{project["id"]}/analysis-runs').status_code==409
    assert client.post(f'/api/analysis-runs/{a["id"]}/pause').json()['status']=='PAUSED'
    assert client.post(f'/api/analysis-runs/{a["id"]}/resume').json()['status']=='QUEUED'
    assert client.post(f'/api/analysis-runs/{a["id"]}/cancel').json()['status']=='CANCELLED'

def test_mock_end_to_end_revision_and_export(client,project):
    rid=run_demo(client,project['id'])
    run=client.get(f'/api/analysis-runs/{rid}').json()
    assert run['status']=='PARTIAL' and run['coverage']['files_processed']==2
    rows=client.get(f'/api/analysis-runs/{rid}/records').json()
    mats=[x['record'] for x in rows if x['record']['kind']=='MATERIAL']
    assert len(mats)==2
    pad=next(m for m in mats if 'concrete' in m['candidate']['name'])
    assert pad['candidate']['design_properties'][0]['value']=='5000 psi'
    conflict=next(x['record'] for x in rows if x['record']['kind']=='CONFLICT')
    assert conflict['candidate']['resolution_status']=='LATEST_APPLIED'
    eid=pad['candidate']['evidence_ids'][0]
    source=client.get(f'/api/analysis-runs/{rid}/evidence/{eid}').json()
    assert source['evidence']['raw_text'] and source['file_name']
    assert client.get(f'/api/analysis-runs/{rid}/cost').json()['calls']==0
    data=client.get(f'/api/analysis-runs/{rid}/exports/json').json()
    assert len(data['records'])==5 and data['run']['status']=='PARTIAL'
    export=client.get(f'/api/analysis-runs/{rid}/exports/xlsx')
    wb=load_workbook(io.BytesIO(export.content))
    assert '材料清单' in wb.sheetnames and '证据' in wb.sheetnames

def test_review_and_optimistic_lock(client,project):
    rid=run_demo(client,project['id'])
    row=client.get(f'/api/analysis-runs/{rid}/records').json()[0];record_id=row['record']['meta']['record_id']
    uri=f'/api/records/{record_id}/review'
    assert client.post(uri,json={'action':'ACCEPTED','expected_version':0}).status_code==200
    assert client.post(uri,json={'action':'REJECTED','expected_version':0}).status_code==409
    assert len(client.get(f'/api/records/{record_id}/history').json())==1
    exported=client.get(f'/api/analysis-runs/{rid}/exports/json?reviewed_only=true').json()
    assert len(exported['records'])==1

def test_edit_validates_evidence_and_keeps_meta(client,project):
    rid=run_demo(client,project['id'])
    row=client.get(f'/api/analysis-runs/{rid}/records?kind=MATERIAL').json()[0];c=deepcopy(row['record']['candidate'])
    uri=f'/api/records/{row["record"]["meta"]["record_id"]}/review'
    c['evidence_ids']=['EV-foreign']
    r=client.post(uri,json={'action':'EDITED','expected_version':0,'note':'edit','candidate':c})
    assert r.status_code==400
    c=deepcopy(row['record']['candidate']);c['support_note']='人工说明'
    assert client.post(uri,json={'action':'EDITED','expected_version':0,'note':'说明更新','candidate':c}).status_code==200

def test_real_text_not_faked_by_mock(client,project):
    upload(client,project['id'],'real.txt','实际项目：请提供混凝土。'.encode())
    rid=client.post(f'/api/projects/{project["id"]}/analysis-runs').json()['id'];client.app.state.runner.process(rid)
    assert client.get(f'/api/analysis-runs/{rid}/records?kind=MATERIAL').json()==[]
    assert client.get(f'/api/analysis-runs/{rid}/records?kind=MISSING').json()

def test_unsupported_not_silently_dropped(client,project):
    upload(client,project['id'],'drawing.dwg',b'AC1032-not-real')
    upload(client,project['id'],'arbitrary.xlsx',b'not-supported')
    rid=client.post(f'/api/projects/{project["id"]}/analysis-runs').json()['id'];client.app.state.runner.process(rid)
    run=client.get(f'/api/analysis-runs/{rid}').json()
    assert run['status']=='PARTIAL' and run['coverage']['files_partial_or_failed']==2
    assert len(client.get(f'/api/analysis-runs/{rid}/records?kind=MISSING').json())==2

def test_export_formula_injection():
    from app.exporter import as_xlsx
    data={'run':{'project_id':'P','id':'R','status':'PARTIAL','coverage':{}},'notice':'=HYPERLINK("evil")','cost':{},'records':[],'evidence':[]}
    wb=load_workbook(io.BytesIO(as_xlsx(data)))
    cell=wb['运行说明']['D2'];assert cell.data_type=='s' and cell.value.startswith('=')

def test_snapshot_does_not_expand(client,project):
    upload(client,project['id'],'a.txt',b'first')
    rid=client.post(f'/api/projects/{project["id"]}/analysis-runs').json()['id']
    upload(client,project['id'],'b.txt',b'second')
    assert len(client.get(f'/api/analysis-runs/{rid}').json()['document_ids'])==1
