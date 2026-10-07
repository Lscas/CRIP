import json
import pytest
from dataclasses import replace

from .test_reference_evaluations import _create,_profile_settings
from .test_reference_results import _saved_run


def _readiness_evaluation(client,project):
    secret='PRIVATE SOURCE SENTENCE MUST NEVER APPEAR IN THE READINESS MANIFEST.'
    db,run,_,_=_saved_run(
        client,project,'REFERENCE_QA',
        text=f'{secret} Finish key PT9 applies to the approved blue color.')
    gateway=client.app.state.gateway
    gateway.s=_profile_settings(
        gateway.s,'custom-4444444444444444','readiness-reference-model')
    response=_create(client,project,run,questions=[
        'What applies to Finish key PT9?',
        'ZXQWVV',
    ],name='Readiness input plan')
    assert response.status_code==201,response.text
    return db,response.json(),secret


def test_readiness_is_deterministic_zero_call_metadata_only_manifest(
        client,project,monkeypatch):
    db,evaluation,secret=_readiness_evaluation(client,project)
    gateway=client.app.state.gateway
    monkeypatch.setattr(
        gateway,'evidence_decision_v3',
        lambda *_a,**_k:(_ for _ in ()).throw(
            AssertionError('Readiness inspection called the model')))
    url=f"/api/reference-evaluations/{evaluation['evaluation_id']}/readiness"

    first=client.get(url);second=client.get(url)

    assert first.status_code==second.status_code==200,first.text
    body=first.json();assert second.json()==body
    assert body['status']=='READY' and body['ready_to_confirm'] is True
    assert body['model_called'] is False
    assert body['source_text_included'] is False
    assert body['semantic_graph_used'] is False and body['embeddings_used'] is False
    assert body['policy']=='LOCAL_LITERAL_SELECTION_MANIFEST_NO_MODEL_CALL'
    assert body['selector_version']=='literal-page-selector-8'
    assert body['summary']['question_count']==2
    assert body['summary']['pending_question_count']==2
    assert body['summary']['maximum_remaining_model_decisions']==6
    assert body['summary']['maximum_page_images_per_decision']==0
    assert body['summary']['maximum_remaining_page_image_attachments']==0
    assert body['summary']['selected_question_count']==1
    assert body['summary']['no_selected_source_count']==1
    assert body['summary']['pending_page_references']==1
    assert body['summary']['sum_pending_selected_text_bytes']>0
    assert body['questions'][0]['selected_page_count']==1
    assert body['questions'][1]['warnings']==['NO_SELECTED_SOURCE']
    assert body['questions'][1]['pages']==[]
    serialized=json.dumps(body,ensure_ascii=False)
    assert secret not in serialized
    assert 'raw_text' not in serialized and 'preview' not in serialized
    assert 'source_evidence_ids' not in serialized
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0

    db.execute("UPDATE reference_evaluations SET selector_version='literal-page-selector-3' "
               "WHERE id=?",(evaluation['evaluation_id'],))
    legacy=client.get(url).json()
    assert legacy['selector_version']=='literal-page-selector-3'
    assert legacy['manifest_id']!=body['manifest_id']
    assert legacy['model_called'] is False and db.one(
        'SELECT COUNT(*) AS n FROM model_calls')['n']==0


@pytest.mark.parametrize('selector_version',['literal-page-selector-7','literal-page-selector-8'])
def test_readiness_accepts_question_entity_reason_code_without_model_call(
        client,project,monkeypatch,selector_version):
    db,run,_,_=_saved_run(
        client,project,'REFERENCE_QA',
        text='Building I full-height wall attachment requirement.')
    gateway=client.app.state.gateway
    gateway.s=_profile_settings(
        gateway.s,'custom-4444444444444444','readiness-reference-model')
    response=_create(
        client,project,run,
        questions=['What is the wall attachment requirement for Building I?'],
        name='V7 entity-scoped readiness')
    assert response.status_code==201,response.text
    evaluation=response.json()
    db.execute('UPDATE reference_evaluations SET selector_version=? WHERE id=?',
               (selector_version,evaluation['evaluation_id']))
    monkeypatch.setattr(
        gateway,'evidence_decision_v3',
        lambda *_a,**_k:(_ for _ in ()).throw(
            AssertionError('Readiness inspection called the model')))

    readiness=client.get(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/readiness")

    assert readiness.status_code==200,readiness.text
    body=readiness.json()
    assert body['selector_version']==selector_version
    assert body['model_called'] is False
    assert 'QUESTION_ENTITY_MATCH' in body['questions'][0]['pages'][0]['reason_codes']
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_readiness_separates_stable_input_manifest_from_operational_status(
        client,project,monkeypatch):
    db,evaluation,_=_readiness_evaluation(client,project)
    gateway=client.app.state.gateway
    monkeypatch.setattr(
        gateway,'evidence_decision_v3',
        lambda *_a,**_k:(_ for _ in ()).throw(
            AssertionError('Readiness or job creation called the model')))
    url=f"/api/reference-evaluations/{evaluation['evaluation_id']}/readiness"
    ready=client.get(url).json()

    gateway.s=replace(gateway.s,structured_output_mode='json_schema')
    mismatched=client.get(url)

    assert mismatched.status_code==200,mismatched.text
    mismatch=mismatched.json()
    assert mismatch['manifest_id']==ready['manifest_id']
    assert mismatch['status_id']!=ready['status_id']
    assert mismatch['status']=='BLOCKED' and mismatch['ready_to_confirm'] is False
    assert mismatch['blockers']==['ACTIVE_PROFILE_MISMATCH']
    assert mismatch['checks']['profile_match'] is False

    gateway.s=replace(gateway.s,structured_output_mode='json_object')
    created=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs",
        json={'confirmed':True})
    assert created.status_code==202,created.text
    active=client.get(url).json()
    assert active['manifest_id']==ready['manifest_id']
    assert active['status_id']!=ready['status_id']
    assert active['status']=='BLOCKED'
    assert active['blockers']==['ACTIVE_MANAGED_JOB']
    assert active['checks']['active_managed_job'] is True
    stopped=client.post(
        f"/api/reference-evaluation-jobs/{created.json()['job_id']}/stop")
    assert stopped.status_code==200 and stopped.json()['state']=='STOPPED'
    assert client.get(url).json()==ready
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_readiness_reports_no_pending_without_authorizing_more_work(
        client,project,monkeypatch):
    db,evaluation,_=_readiness_evaluation(client,project)
    monkeypatch.setattr(
        client.app.state.gateway,'evidence_decision_v3',
        lambda *_a,**_k:(_ for _ in ()).throw(
            AssertionError('Readiness called the model')))
    before=client.get(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/readiness").json()
    store=client.app.state.reference_evaluations
    for item in evaluation['items']:
        store.fail(evaluation['evaluation_id'],item['item_id'],'MODEL_OUTPUT_REJECTED')

    response=client.get(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/readiness")

    assert response.status_code==200,response.text
    body=response.json()
    assert body['manifest_id']==before['manifest_id']
    assert body['status_id']!=before['status_id']
    assert body['status']=='NO_PENDING' and body['ready_to_confirm'] is False
    assert body['blockers']==['NO_PENDING_QUESTIONS']
    assert body['summary']['pending_question_count']==0
    assert body['summary']['maximum_remaining_model_decisions']==0
    assert body['summary']['pending_page_references']==0
    assert body['summary']['sum_pending_selected_text_bytes']==0
    assert all(question['would_dispatch'] is False for question in body['questions'])
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0
