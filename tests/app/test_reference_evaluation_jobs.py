from dataclasses import replace

import pytest

from app.db import DomainError
from app.gateway import InvalidModelOutput,ModelResult
from .test_evidence_loop import _answer
from .test_reference_evaluations import _create,_profile_settings
from .test_reference_results import _saved_run


def _evaluation(client,project,questions=None):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    gateway.s=_profile_settings(
        gateway.s,'custom-3333333333333333','managed-reference-model')
    created=_create(client,project,run,questions=questions or [
        'What approved color applies to Finish key PT9?',
        'Which section contains the finish requirement?',
        'What source identifies Finish key PT9?',
    ])
    assert created.status_code==201,created.text
    return db,created.json()


def test_job_creation_is_confirmed_local_and_blocks_competing_dispatch(
        client,project,monkeypatch):
    db,evaluation=_evaluation(client,project)
    gateway=client.app.state.gateway
    monkeypatch.setattr(gateway,'evidence_decision_v3',lambda *_a,**_k:(_ for _ in ()).throw(
        AssertionError('Creating or listing a managed job called the model')))

    rejected=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs",
        json={'confirmed':False})
    assert rejected.status_code==422
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluation_jobs')['n']==0

    created=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs",
        json={'confirmed':True})
    assert created.status_code==202,created.text
    job=created.json()
    assert job['state']=='QUEUED' and job['reason_code']=='NONE'
    assert job['pending_at_start']==job['remaining_count']==3
    assert job['completed_count']==job['failed_count']==0
    assert job['maximum_model_decisions']==9
    assert job['profile']==evaluation['profile']
    assert job['selector_version']==evaluation['selector_version']=='literal-page-selector-8'
    assert job['execution_policy']=='EXPLICIT_CONFIRMATION_SEQUENTIAL_NO_AUTOMATIC_RETRY'
    assert db.one('SELECT 1 FROM schema_migrations WHERE version=14') is not None
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0
    listing=client.get(
        f"/api/projects/{project['id']}/reference-evaluation-jobs?limit=20")
    assert listing.status_code==200 and listing.json()['items']==[job]
    other=client.post('/api/projects',json={'name':'Other job project'}).json()
    other_listing=client.get(
        f"/api/projects/{other['id']}/reference-evaluation-jobs?limit=20")
    assert other_listing.status_code==200 and other_listing.json()['items']==[]

    duplicate=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs",
        json={'confirmed':True})
    assert duplicate.status_code==409
    pending=evaluation['items'][0]
    manual=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/items/{pending['item_id']}/execute")
    assert manual.status_code==409 and 'managed Reference evaluation job' in manual.text
    direct=client.post(f"/api/projects/{project['id']}/questions-v3",json={
        'run_id':evaluation['run_id'],'question':pending['question']})
    assert direct.status_code==409
    legacy=client.post(f"/api/projects/{project['id']}/questions",json={
        'run_id':evaluation['run_id'],'question':pending['question']})
    v2=client.post(f"/api/projects/{project['id']}/questions-v2",json={
        'run_id':evaluation['run_id'],'question':pending['question']})
    assert legacy.status_code==v2.status_code==409
    with pytest.raises(DomainError,match='managed Reference evaluation job'):
        client.app.state.runner.create(project['id'],analysis_mode='REFERENCE_QA')


def test_job_processes_pending_items_one_at_a_time_and_saves_safe_failures(
        client,project,monkeypatch):
    db,evaluation=_evaluation(client,project)
    gateway=client.app.state.gateway;calls=[]
    def decision(*_args,**_kwargs):
        calls.append('decision')
        if len(calls)==2:
            raise InvalidModelOutput('synthetic rejected provider output must not be retained')
        return ModelResult(
            _answer('EV-REFERENCE-1','The approved color is blue.'),None,False,'custom')
    monkeypatch.setattr(gateway,'evidence_decision_v3',decision)
    job=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs",
        json={'confirmed':True}).json()

    client.app.state.reference_evaluation_jobs.process(job['job_id'])

    saved=client.get(f"/api/reference-evaluation-jobs/{job['job_id']}")
    assert saved.status_code==200,saved.text
    body=saved.json();assert body['state']=='COMPLETED'
    assert body['reason_code']=='COMPLETED' and body['remaining_count']==0
    assert body['completed_count']==3 and body['failed_count']==1
    assert body['current_item'] is None and body['finished_at']
    refreshed=client.get(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}").json()
    assert refreshed['status']=='COMPLETE'
    assert refreshed['summary']['ANSWERED']==2 and refreshed['summary']['FAILED']==1
    assert calls==['decision','decision','decision']
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0
    raw=db.one('SELECT * FROM reference_evaluation_jobs WHERE id=?',(job['job_id'],))
    assert 'synthetic rejected' not in str(raw)


def test_stop_after_current_is_durable_and_remaining_work_needs_new_confirmation(
        client,project,monkeypatch):
    db,evaluation=_evaluation(client,project)
    gateway=client.app.state.gateway;store=client.app.state.reference_evaluation_jobs
    first_job=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs",
        json={'confirmed':True}).json()
    calls=[]
    def stop_after_first(*_args,**_kwargs):
        calls.append('decision')
        store.stop(first_job['job_id'])
        return ModelResult(
            _answer('EV-REFERENCE-1','The approved color is blue.'),None,False,'custom')
    monkeypatch.setattr(gateway,'evidence_decision_v3',stop_after_first)

    store.process(first_job['job_id'])

    stopped=store.get(first_job['job_id'])
    assert stopped['state']=='STOPPED' and stopped['reason_code']=='USER_STOPPED'
    assert stopped['completed_count']==1 and stopped['remaining_count']==2
    assert calls==['decision']
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluation_jobs')['n']==1

    resumed=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs",
        json={'confirmed':True})
    assert resumed.status_code==202,resumed.text
    second_job=resumed.json();assert second_job['pending_at_start']==2
    monkeypatch.setattr(gateway,'evidence_decision_v3',lambda *_a,**_k:ModelResult(
        _answer('EV-REFERENCE-1','The approved color is blue.'),None,False,'custom'))
    store.process(second_job['job_id'])
    assert store.get(second_job['job_id'])['state']=='COMPLETED'
    assert store.get(second_job['job_id'])['completed_count']==2
    assert store.get(first_job['job_id'])['state']=='STOPPED'


def test_interruption_profile_change_and_internal_error_are_safe_and_never_resume(
        client,project,monkeypatch):
    db,evaluation=_evaluation(client,project)
    store=client.app.state.reference_evaluation_jobs
    first=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs",
        json={'confirmed':True}).json()
    store.interrupt_active()
    interrupted=store.get(first['job_id'])
    assert interrupted['state']=='INTERRUPTED'
    assert interrupted['reason_code']=='SERVICE_INTERRUPTED'
    store.process(first['job_id'])
    assert store.get(first['job_id'])==interrupted

    profile_job=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs",
        json={'confirmed':True}).json()
    gateway=client.app.state.gateway;original=gateway.s
    gateway.s=replace(gateway.s,structured_output_mode='json_schema')
    store.process(profile_job['job_id'])
    profile_halt=store.get(profile_job['job_id'])
    assert profile_halt['state']=='HALTED' and profile_halt['reason_code']=='PROFILE_CHANGED'
    gateway.s=original

    second=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/jobs",
        json={'confirmed':True}).json()
    monkeypatch.setattr(
        gateway,'evidence_decision_v3',
        lambda *_a,**_k:(_ for _ in ()).throw(
            RuntimeError('synthetic sensitive provider failure')))
    store.process(second['job_id'])
    halted=store.get(second['job_id'])
    assert halted['state']=='HALTED' and halted['reason_code']=='INTERNAL_ERROR'
    assert 'sensitive' not in str(halted)
    raw=db.one('SELECT * FROM reference_evaluation_jobs WHERE id=?',(second['job_id'],))
    assert 'sensitive' not in str(raw)
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0
