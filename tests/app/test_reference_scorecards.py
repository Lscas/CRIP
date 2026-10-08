import json

from .test_reference_evaluations import _create,_profile_settings
from .test_reference_results import _public_result,_saved_run


_WORDS=(
    'alpha','bravo','charlie','delta','echo','foxtrot','golf','hotel',
    'india','juliet','kilo','lima','mike','november','oscar',
)


def _benchmark(client,project,count=15,live=True):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    gateway=client.app.state.gateway
    if live:
        gateway.s=_profile_settings(
            gateway.s,'custom-5555555555555555','benchmark-reference-model')
    questions=[
        f'What approved color applies to Finish key PT9 for case {word}?'
        for word in _WORDS[:count]
    ]
    response=_create(
        client,project,run,questions=questions,name='Human benchmark scorecard')
    assert response.status_code==201,response.text
    return db,run,response.json()


def _attach_answered_results(client,evaluation,run):
    results=client.app.state.reference_results
    evaluations=client.app.state.reference_evaluations
    for item in evaluation['items']:
        saved=results.save(
            run,item['question'],_public_result(run,item['question']),
            evaluation['profile']['provider'],evaluation['profile']['text_model'])
        evaluation=evaluations.attach(
            evaluation['evaluation_id'],item['item_id'],saved['result_id'])
    return evaluation


def test_fixed_fifteen_scorecard_requires_terminal_human_adjudication_and_is_zero_call(
        client,project,monkeypatch):
    db,run,evaluation=_benchmark(client,project)
    gateway=client.app.state.gateway
    monkeypatch.setattr(
        gateway,'evidence_decision_v3',
        lambda *_a,**_k:(_ for _ in ()).throw(
            AssertionError('Scorecard management called the model')))
    url=f"/api/reference-evaluations/{evaluation['evaluation_id']}/scorecard"

    pending=client.get(url)

    assert pending.status_code==200,pending.text
    first=pending.json()
    assert first['status']=='INCOMPLETE_EXECUTION'
    assert first['selector_version']==evaluation['selector_version']=='literal-page-selector-8'
    assert first['gate_applicable'] is True and first['profile_eligible'] is True
    assert first['thresholds_met'] is False
    assert first['model_called'] is False and first['correctness_inferred'] is False
    assert first['criteria']=={
        'question_count_required':15,'contract_valid_percent_required':98,
        'contract_valid_count_required':15,'fully_usable_count_required':12,
        'unsupported_claim_count_allowed':0,
        'complete_human_adjudication_required':True,
    }
    assert first['summary']['PENDING']==15
    assert first['summary']['UNREVIEWED']==15
    assert client.get(url).json()==first

    evaluation=_attach_answered_results(client,evaluation,run)
    terminal=client.get(url).json()
    assert terminal['status']=='INCOMPLETE_ADJUDICATION'
    assert terminal['summary']['CONTRACT_VALID']==15
    assert terminal['summary']['ANSWERED']==15
    assert terminal['scorecard_id']!=first['scorecard_id']

    saved=None
    for index,item in enumerate(terminal['items']):
        saved=client.post(
            f"/api/reference-evaluations/{evaluation['evaluation_id']}"
            f"/items/{item['item_id']}/adjudication",json={
                'verdict':'FULLY_USABLE' if index<12 else 'UNUSABLE',
                'unsupported_claim':False,'expected_version':0,
                'note':'Compared against the independent source answer.',
            })
        assert saved.status_code==200,saved.text
    scorecard=saved.json()
    assert scorecard['status']=='THRESHOLDS_MET'
    assert scorecard['thresholds_met'] is True
    assert scorecard['summary']['ADJUDICATED']==15
    assert scorecard['summary']['FULLY_USABLE']==12
    assert scorecard['summary']['UNUSABLE']==3
    assert scorecard['summary']['UNSUPPORTED_CLAIM']==0
    serialized=json.dumps(scorecard)
    assert 'The approved color is blue.' not in serialized
    assert 'citations' not in serialized and 'result_json' not in serialized

    reviewed=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{scorecard['items'][0]['item_id']}/adjudication",json={
            'verdict':'PARTIAL','unsupported_claim':True,'expected_version':1,
            'note':'One factual claim is not supported by the cited source.',
        })
    assert reviewed.status_code==200,reviewed.text
    failed_gate=reviewed.json()
    assert failed_gate['status']=='THRESHOLDS_NOT_MET'
    assert failed_gate['thresholds_met'] is False
    assert failed_gate['summary']['FULLY_USABLE']==11
    assert failed_gate['summary']['PARTIAL']==1
    assert failed_gate['summary']['UNSUPPORTED_CLAIM']==1
    history=client.get(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{scorecard['items'][0]['item_id']}/adjudication-history")
    assert history.status_code==200,history.text
    assert history.json()['total']==2
    assert history.json()['items'][0]['after']['version']==2
    assert history.json()['items'][1]['before']['verdict']=='UNREVIEWED'
    stale=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{scorecard['items'][0]['item_id']}/adjudication",json={
            'verdict':'UNUSABLE','unsupported_claim':False,'expected_version':1})
    assert stale.status_code==409
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0
    assert db.one('SELECT 1 FROM schema_migrations WHERE version=15') is not None
    assert db.one('SELECT 1 FROM schema_migrations WHERE version=16') is not None


def test_scorecard_rejects_pending_cross_task_and_invalid_outcome_verdicts(
        client,project,monkeypatch):
    db,run,evaluation=_benchmark(client,project,count=2)
    monkeypatch.setattr(
        client.app.state.gateway,'evidence_decision_v3',
        lambda *_a,**_k:(_ for _ in ()).throw(
            AssertionError('Scorecard management called the model')))
    scorecard=client.get(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/scorecard").json()
    assert scorecard['status']=='NOT_APPLICABLE'
    assert scorecard['gate_applicable'] is False
    first,second=evaluation['items']
    pending=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{first['item_id']}/adjudication",json={
            'verdict':'UNUSABLE','unsupported_claim':False,'expected_version':0})
    assert pending.status_code==409

    client.app.state.reference_evaluations.fail(
        evaluation['evaluation_id'],first['item_id'],'MODEL_OUTPUT_REJECTED')
    invalid=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{first['item_id']}/adjudication",json={
            'verdict':'FULLY_USABLE','unsupported_claim':False,'expected_version':0})
    assert invalid.status_code==409
    valid=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{first['item_id']}/adjudication",json={
            'verdict':'UNUSABLE','unsupported_claim':False,'expected_version':0,
            'note':'No contract-valid result was saved.'})
    assert valid.status_code==200,valid.text
    assert valid.json()['summary']['CONTRACT_INVALID']==1
    assert valid.json()['summary']['UNUSABLE']==1

    other=client.post('/api/projects',json={'name':'Other benchmark project'}).json()
    _,_,other_evaluation=_benchmark(client,other,count=1)
    cross=client.get(
        f"/api/reference-evaluations/{other_evaluation['evaluation_id']}"
        f"/items/{first['item_id']}/adjudication-history")
    assert cross.status_code==404
    impossible=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{first['item_id']}/adjudication",json={
            'verdict':'FULLY_USABLE','unsupported_claim':True,'expected_version':1})
    assert impossible.status_code==400
    assert second['state']=='PENDING'
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0


def test_fixed_gate_refuses_mock_profile_without_inferring_quality(client,project):
    db,_,evaluation=_benchmark(client,project,live=False)
    executed=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{evaluation['items'][0]['item_id']}/execute")
    assert executed.status_code==200,executed.text
    assert executed.json()['result']['status']=='MODEL_DISABLED'

    response=client.get(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}/scorecard")

    assert response.status_code==200,response.text
    body=response.json()
    assert body['status']=='MODEL_DISABLED_PROFILE'
    assert body['gate_applicable'] is True and body['profile_eligible'] is False
    assert body['thresholds_met'] is False and body['correctness_inferred'] is False
    assert body['summary']['MODEL_DISABLED']==1
    assert body['summary']['CONTRACT_VALID']==0
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n']==0
