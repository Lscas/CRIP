import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.db import DomainError
from app.gateway import ModelResult
from app import reference_results as reference_results_module
from .conftest import upload
from .test_evidence_loop import _answer


def _saved_run(client,project,mode,evidence_id='EV-REFERENCE-1',
               text='The approved color is blue. Finish key PT9 applies.',raw=None):
    document=upload(client,project['id'],f'{mode.casefold()}-source.pdf',
                    raw if raw is not None else b'synthetic source')
    runner=client.app.state.runner;db=client.app.state.db
    run=runner.create(project['id'],analysis_mode=mode)
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?",(run['id'],))
    base=json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    evidence={
        **base,'evidence_id':evidence_id,'tenant_id':'local','project_id':project['id'],
        'input_snapshot_id':run['snapshot_id'],'document_id':document['document_id'],
        'raw_text':text,'internal_revision_date':'2025-01-01','revision_label':'1',
        'extraction_method':'TEXT_LAYER','content_basis':'SOURCE_TEXT',
        'parser_version':'synthetic-reference-result','locator':{
            **base['locator'],'page_number':1,'section':'Section 09 91 00',
            'paragraph':'2.2','bbox':[10.0,20.0,300.0,70.0],
            'coordinate_system':'pdf-points',
        },
    }
    with db.connect(True) as connection:
        connection.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',(
            run['id']+':'+evidence_id,run['id'],project['id'],document['document_id'],
            json.dumps(evidence),'PENDING',None,''))
        connection.execute('INSERT INTO document_results VALUES(?,?,?,?)',(
            run['id'],document['document_id'],'SUCCESS',json.dumps({
                'status':'SUCCESS','pages':[{'page':1,'status':'SUCCESS'}],
                'warnings':[],'parser_version':'synthetic-reference-result'})))
    return db,runner.get(run['id']),document,evidence


def _public_result(run,question,evidence_id='EV-REFERENCE-1',
                   answer='The approved color is blue.',quote='The approved color is blue.'):
    decision=_answer(evidence_id,quote)
    decision['answer']['answer']=answer
    decision['answer']['claims'][0]['text']=answer
    return {
        'qa_version':'3','feature':'BOUNDED_EVIDENCE_LOOP','run_id':run['id'],
        'question':question,'source_scope':{
            'policy':'RUN_SNAPSHOT_ALL_MATCHING_NO_PRECEDENCE',
            'snapshot_id':run['snapshot_id'],'conflicts':[]},
        **decision['answer'],'answer_basis':'MODEL_QA_V3_EVIDENCE_LOOP',
        'model_called':True,'model_call_count':1,'retrieved_count':1,
        'decision_trace':[],'page_selections':[],
    }


def test_source_evidence_accepts_a_saved_run_document(client,project):
    _,run,document,_=_saved_run(client,project,'REFERENCE_QA')

    sources=client.app.state.reference_results._source_evidence(run)

    assert sources['EV-REFERENCE-1']['document_id']==document['document_id']


def test_source_evidence_rejects_tampered_payload_document_id(client,project):
    db,run,document,evidence=_saved_run(client,project,'REFERENCE_QA')
    evidence['document_id']='DOC-tampered-not-the-evidence-row'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(
        json.dumps(evidence),run['id']+':EV-REFERENCE-1'))

    with pytest.raises(DomainError,match='does not match its snapshot'):
        client.app.state.reference_results._source_evidence(run)
    assert document['document_id'] in run['document_ids']


def test_source_evidence_rejects_document_excluded_from_run(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    other=upload(client,project['id'],'other-source.pdf',b'other synthetic source')
    db.execute('UPDATE runs SET document_ids=? WHERE id=?',(
        json.dumps([other['document_id']]),run['id']))
    excluded=client.app.state.runner.get(run['id'])

    with pytest.raises(DomainError,match='does not match its snapshot'):
        client.app.state.reference_results._source_evidence(excluded)


def test_source_evidence_rejects_foreign_document_even_when_run_and_payload_agree(client,project):
    db,run,_,evidence=_saved_run(client,project,'REFERENCE_QA')
    other=client.post('/api/projects',json={'name':'Foreign document project'}).json()
    document=upload(client,other['id'],'foreign.pdf',b'foreign synthetic source')
    evidence['document_id']=document['document_id']
    db.execute('UPDATE evidence SET document_id=?,payload=? WHERE id=?',(
        document['document_id'],json.dumps(evidence),run['id']+':EV-REFERENCE-1'))
    mismatched={**run,'document_ids':[document['document_id']]}
    with pytest.raises(DomainError,match='does not match its snapshot'):
        client.app.state.reference_results._source_evidence(mismatched)


@pytest.mark.parametrize(('field','value'),[
    ('project_id','P-other-project'),
    ('input_snapshot_id','SN-other-snapshot'),
])
def test_source_evidence_rejects_payload_cross_project_or_snapshot(client,project,field,value):
    db,run,_,evidence=_saved_run(client,project,'REFERENCE_QA')
    evidence[field]=value
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(
        json.dumps(evidence),run['id']+':EV-REFERENCE-1'))

    with pytest.raises(DomainError,match='does not match its snapshot'):
        client.app.state.reference_results._source_evidence(run)


@pytest.mark.parametrize('document_ids',[None,[],['DOC-one','DOC-one'],['DOC-one',7]])
def test_source_evidence_rejects_malformed_run_document_set(client,project,document_ids):
    _,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    malformed={**run,'document_ids':document_ids}

    with pytest.raises(DomainError,match='run documents are malformed'):
        client.app.state.reference_results._source_evidence(malformed)


def test_legacy_and_reference_knowledge_are_separate(client,project):
    _,legacy,_,_=_saved_run(client,project,'LEGACY_ANALYSIS','EV-LEGACY-1')
    _,reference,_,_=_saved_run(client,project,'REFERENCE_QA','EV-REFERENCE-1')

    legacy_knowledge=client.get(f"/api/projects/{project['id']}/knowledge")
    reference_knowledge=client.get(f"/api/projects/{project['id']}/reference-knowledge")
    preview=client.post(f"/api/projects/{project['id']}/questions-v3/preview",json={
        'question':'What approved color applies to Finish key PT9?'})
    legacy_answer=client.post(f"/api/projects/{project['id']}/questions",json={
        'question':'What approved color applies to Finish key PT9?'})

    assert (legacy_knowledge.status_code==reference_knowledge.status_code==
            preview.status_code==legacy_answer.status_code==200)
    assert legacy_knowledge.json()['run_id']==legacy['id']
    assert reference_knowledge.json()['run_id']==reference['id']
    assert reference_knowledge.json()['active_run_id'] is None
    assert preview.json()['page_selection']['run_id']==reference['id']
    assert legacy_answer.json()['run_id']==legacy['id']


def test_answer_is_immutable_deduplicated_and_optimistically_reviewed(
        client,project,monkeypatch):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=replace(gateway.s,provider='custom-0123456789abcdef',
                      api_base_url='https://models.example/v1',
                      api_key='synthetic-valid-key',cheap_model='gpt-5.6',
                      vision_model='gpt-5.6',live_enabled=True)
    calls=[]
    def decision(*_args,**_kwargs):
        calls.append('decision')
        return ModelResult(_answer('EV-REFERENCE-1','The approved color is blue.'),
                           None,False,'custom')
    monkeypatch.setattr(gateway,'evidence_decision_v3',decision)

    first=client.post(f"/api/projects/{project['id']}/questions-v3",json={
        'question':'What is the approved color for Finish key PT9?'})
    equivalent=client.post(f"/api/projects/{project['id']}/questions-v3",json={
        'question':'What  is the approved color for Finish key PT9?'})

    assert first.status_code==equivalent.status_code==200
    assert first.json()['result_id']==equivalent.json()['result_id']
    result_id=first.json()['result_id']
    before=db.one('SELECT result_hash,result_json FROM reference_results WHERE id=?',(result_id,))
    citations=db.all('SELECT * FROM reference_result_citations WHERE result_id=?',(result_id,))
    assert len(calls)==2 and len(citations)==1
    assert citations[0]['citation_type']=='TEXT' and citations[0]['evidence_id']=='EV-REFERENCE-1'
    assert json.loads(citations[0]['citation_json'])['quote']=='The approved color is blue.'

    listing=client.get(f"/api/projects/{project['id']}/reference-results")
    detail=client.get(f'/api/reference-results/{result_id}')
    accepted=client.post(f'/api/reference-results/{result_id}/review',json={
        'action':'ACCEPTED','expected_version':0,'note':'Checked against source.'})
    stale=client.post(f'/api/reference-results/{result_id}/review',json={
        'action':'REJECTED','expected_version':0,'note':'Stale tab.'})
    history=client.get(f'/api/reference-results/{result_id}/history')
    after=db.one('SELECT result_hash,result_json FROM reference_results WHERE id=?',(result_id,))

    assert listing.status_code==detail.status_code==accepted.status_code==history.status_code==200
    assert listing.json()['total']==1 and detail.json()['review']['status']=='PENDING'
    assert accepted.json()['review']=={
        'status':'ACCEPTED','version':1,'event_id':history.json()[0]['id']}
    assert history.json()[0]['actor']=='local-engineer'
    assert history.json()[0]['before']['status']=='PENDING'
    assert history.json()[0]['after']['status']=='ACCEPTED'
    assert stale.status_code==409 and before==after and len(calls)==2
    assert db.one('SELECT 1 FROM model_calls',(),False) is None


def test_non_answer_is_saved_but_cannot_be_reviewed(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')

    response=client.post(f"/api/projects/{project['id']}/questions-v3",json={
        'question':'What is the approved color for Finish key PT9?'})

    assert response.status_code==200,response.text
    assert response.json()['status']=='MODEL_DISABLED'
    assert response.json()['review']['status']=='NOT_APPLICABLE'
    rejected=client.post(f"/api/reference-results/{response.json()['result_id']}/review",json={
        'action':'ACCEPTED','expected_version':0})
    assert rejected.status_code==409
    assert db.one('SELECT 1 FROM model_calls',(),False) is None


def test_image_region_is_normalized_and_private_reasoning_is_rejected(client,project):
    db,run,document,_=_saved_run(client,project,'REFERENCE_QA')
    store=client.app.state.reference_results;question='Where is Pump P-101 shown?'
    region={
        'region_id':'VR-'+('1'*24),'document_id':document['document_id'],
        'file_name':document['name'],'page_number':1,'bbox':[0.0,0.0,800.0,600.0],
        'coordinate_system':'pixel-top-left','source_evidence_ids':['EV-REFERENCE-1'],
        'image_sha256':hashlib.sha256(b'synthetic image').hexdigest(),
    }
    result={
        'qa_version':'3','feature':'BOUNDED_EVIDENCE_LOOP','run_id':run['id'],
        'question':question,'source_scope':{
            'policy':'RUN_SNAPSHOT_ALL_MATCHING_NO_PRECEDENCE',
            'snapshot_id':run['snapshot_id'],'conflicts':[]},
        'status':'ANSWERED','answer':'Pump P-101 is near Grid A-2.',
        'claims':[{'text':'Pump P-101 is near Grid A-2.','citations':[{
            'type':'IMAGE_REGION','region_id':region['region_id'],
            'document_id':region['document_id'],'page_number':1,'bbox':region['bbox'],
            'observation':'The page shows Pump P-101 near Grid A-2.','needs_review':True,
        }]}],
        'missing':[],'calculations':[],'answer_basis':'MODEL_QA_V3_MULTIMODAL_EVIDENCE_LOOP',
        'model_called':True,'model_call_count':1,'retrieved_count':1,
        'decision_trace':[],'page_selections':[],'visual_regions':[region],
    }

    saved=store.save(run,question,result,'custom-0123456789abcdef','gpt-5.6')
    citation=saved['citations'][0]['citation']
    assert citation['type']=='IMAGE_REGION'
    assert citation['image_sha256']==region['image_sha256']
    assert citation['coordinate_system']=='pixel-top-left'
    assert 'png' not in json.dumps(saved)

    leaked={**_public_result(run,'What is the approved color?'),
            'reasoning':'private chain of thought'}
    with pytest.raises(DomainError,match='private model reasoning'):
        store.save(run,'What is the approved color?',leaked,
                   'custom-0123456789abcdef','gpt-5.6')
    assert db.one('SELECT COUNT(*) AS n FROM reference_results')['n']==1


def test_reference_export_contains_complete_audit_and_is_zero_call(
        client,project,monkeypatch):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    store=client.app.state.reference_results;question='What is the approved color?'
    saved=store.save(run,question,_public_result(run,question),
                     'custom-0123456789abcdef','gpt-5.6')
    store.review(saved['result_id'],'ACCEPTED',0,'Verified against the cited source.')
    monkeypatch.setattr(client.app.state.gateway,'evidence_decision_v3',lambda *_a,**_k: (_ for _ in ()).throw(
        AssertionError('Reference export called the model')))

    response=client.get(f"/api/projects/{project['id']}/reference-results/export.json",params={
        'run_id':run['id'],'review_status':'ACCEPTED'})

    assert response.status_code==200,response.text
    assert response.headers['content-disposition'].endswith(f'{run["id"]}.json"')
    body=response.json();assert body['export_version']=='reference-results-json-1'
    assert body['project']=={'id':project['id'],'name':project['name']}
    assert body['filters']=={'run_id':run['id'],'review_status':'ACCEPTED'}
    assert body['result_count']==1
    exported=body['results'][0]
    assert exported['result_id']==saved['result_id']
    assert exported['result']['decision_trace']==[]
    assert exported['citations'][0]['citation']['quote']=='The approved color is blue.'
    assert exported['review']['status']=='ACCEPTED'
    assert exported['review_history'][0]['note']=='Verified against the cited source.'
    assert db.one('SELECT 1 FROM model_calls',(),False) is None

    _,legacy,_,_=_saved_run(client,project,'LEGACY_ANALYSIS','EV-LEGACY-EXPORT')
    rejected=client.get(f"/api/projects/{project['id']}/reference-results/export.json",
                        params={'run_id':legacy['id']})
    assert rejected.status_code==409
    monkeypatch.setattr(reference_results_module,'MAX_EXPORT_RESULTS',0)
    too_large=client.get(f"/api/projects/{project['id']}/reference-results/export.json")
    assert too_large.status_code==409


def test_reference_export_review_and_history_share_one_read_snapshot(client,project,monkeypatch):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    store=client.app.state.reference_results;question='What is the approved color?'
    saved=store.save(run,question,_public_result(run,question),
                     'custom-0123456789abcdef','gpt-5.6')
    original_public=store._public;changed=False

    def review_during_export(row,detail,connection=None):
        nonlocal changed
        if connection is not None and not changed:
            changed=True
            store.review(saved['result_id'],'ACCEPTED',0,'Concurrent review.')
        return original_public(row,detail,connection)
    monkeypatch.setattr(store,'_public',review_during_export)

    exported=store.export(project['id'],run['id'])['results'][0]
    # The export began before the concurrent write, so both surfaces must be old.
    assert changed is True
    assert exported['review']=={'status':'PENDING','version':0,'event_id':None}
    assert exported['review_history']==[]
    current=db.one('SELECT review_status,review_version FROM reference_results WHERE id=?',
                   (saved['result_id'],))
    assert current=={'review_status':'ACCEPTED','review_version':1}


def test_reference_comparison_reports_saved_outcome_review_and_processing_deltas(
        client,project,monkeypatch):
    text=('The approved color is blue. The approved color is green. '
          'Door type A is wood. Warranty period is five years.')
    db,baseline,_,_=_saved_run(client,project,'REFERENCE_QA','EV-REFERENCE-1',text,
                               b'synthetic baseline source')
    db,candidate,_,_=_saved_run(client,project,'REFERENCE_QA','EV-REFERENCE-2',text,
                                b'synthetic candidate source')
    store=client.app.state.reference_results

    def save(run,question,answer,quote,evidence_id,calculation=False,
             provider='custom-0123456789abcdef',model='gpt-5.6'):
        result=_public_result(run,question,evidence_id=evidence_id,answer=answer,quote=quote)
        if calculation:
            result['calculations']=[{'operator':'ADD','operands':['2','3'],'result':'5',
                                     'citations':[{'type':'TEXT','evidence_id':evidence_id,
                                                   'quote':quote}]}]
        return store.save(run,question,result,provider,model)

    unchanged_before=save(baseline,'What is the unchanged finish color?',
                          'The approved color is blue.','The approved color is blue.',
                          'EV-REFERENCE-1',True)
    save(candidate,'What  is the unchanged finish color?',
         'The approved color is blue.','The approved color is blue.','EV-REFERENCE-2',True)
    store.review(unchanged_before['result_id'],'ACCEPTED',0,'Baseline reviewed.')
    save(baseline,'What finish color changed?',
         'The approved color is blue.','The approved color is blue.','EV-REFERENCE-1')
    save(candidate,'What finish color changed?',
         'The approved color is green.','The approved color is green.',
         'EV-REFERENCE-2',provider='custom-fedcba9876543210',
         model='gpt-6-reference')
    save(baseline,'What door requirement was removed?',
         'Door type A is wood.','Door type A is wood.','EV-REFERENCE-1')
    save(candidate,'What warranty requirement was added?',
         'Warranty period is five years.','Warranty period is five years.',
         'EV-REFERENCE-2')
    monkeypatch.setattr(client.app.state.gateway,'evidence_decision_v3',lambda *_a,**_k: (_ for _ in ()).throw(
        AssertionError('Reference comparison called the model')))

    params={'baseline_run_id':baseline['id'],'candidate_run_id':candidate['id']}
    first=client.get(f"/api/projects/{project['id']}/reference-results/compare",params=params)
    second=client.get(f"/api/projects/{project['id']}/reference-results/compare",params=params)

    assert first.status_code==second.status_code==200,first.text
    body=first.json();assert body['comparison_id']==second.json()['comparison_id']
    assert body['model_called'] is False
    assert body['policy']=='SAVED_PUBLIC_OUTCOME_ONLY_NO_PRECEDENCE'
    assert body['summary']=={
        'ADDED':1,'REMOVED':1,'CHANGED':1,'UNCHANGED':1,'TOTAL':4}
    by_question={item['question']:item for item in body['items']}
    unchanged=by_question['What is the unchanged finish color?']
    assert unchanged['change']=='UNCHANGED' and unchanged['review_changed'] is True
    assert unchanged['processing_changed'] is False
    assert (unchanged['baseline_versions'][0]['outcome_hash']==
            unchanged['candidate_versions'][0]['outcome_hash'])
    assert (unchanged['baseline_versions'][0]['result_hash']!=
            unchanged['candidate_versions'][0]['result_hash'])
    assert unchanged['candidate_versions'][0]['calculations'][0]['result']=='5'
    assert 'evidence_id' not in unchanged['candidate_versions'][0]['calculations'][0]['citations'][0]
    changed=by_question['What finish color changed?']
    assert changed['change']=='CHANGED' and changed['processing_changed'] is True
    assert by_question['What warranty requirement was added?']['processing_changed'] is False
    assert by_question['What door requirement was removed?']['review_changed'] is False
    assert body['baseline']['result_count']==3 and body['candidate']['result_count']==3
    assert db.one('SELECT 1 FROM model_calls',(),False) is None

    same=client.get(f"/api/projects/{project['id']}/reference-results/compare",params={
        'baseline_run_id':baseline['id'],'candidate_run_id':baseline['id']})
    assert same.status_code==400
    _,legacy,_,_=_saved_run(client,project,'LEGACY_ANALYSIS','EV-LEGACY-COMPARE',text)
    wrong_mode=client.get(f"/api/projects/{project['id']}/reference-results/compare",params={
        'baseline_run_id':legacy['id'],'candidate_run_id':candidate['id']})
    assert wrong_mode.status_code==409
    other=client.post('/api/projects',json={'name':'Other project'}).json()
    _,other_run,_,_=_saved_run(client,other,'REFERENCE_QA','EV-OTHER-COMPARE',text)
    cross_project=client.get(f"/api/projects/{project['id']}/reference-results/compare",params={
        'baseline_run_id':baseline['id'],'candidate_run_id':other_run['id']})
    assert cross_project.status_code==404
    active=client.app.state.runner.create(project['id'],analysis_mode='REFERENCE_QA')
    active_run=client.get(f"/api/projects/{project['id']}/reference-results/compare",params={
        'baseline_run_id':baseline['id'],'candidate_run_id':active['id']})
    assert active_run.status_code==409
