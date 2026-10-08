"""Offline end-to-end supplemental-document Reference case flow."""
import copy

import pytest

from .conftest import upload
from .test_evidence_loop import _empty_answer,_provider_answer
from .test_reference_profile_comparisons import _channel


QUESTION='What approved color applies?'


def _reference_run(client,project_id):
    created=client.post(f'/api/projects/{project_id}/analysis-runs',json={
        'analysis_mode':'REFERENCE_QA'})
    assert created.status_code==202,created.text
    run=created.json()
    client.app.state.runner.process(run['id'])
    ready=client.get(f"/api/analysis-runs/{run['id']}")
    assert ready.status_code==200,ready.text
    assert ready.json()['status']=='PARTIAL'
    return ready.json()


def _ask(client,project_id,run_id,profile_id=None):
    body={'run_id':run_id,'question':QUESTION}
    if profile_id is not None:body['profile_id']=profile_id
    response=client.post(f'/api/projects/{project_id}/questions-v3',json=body)
    assert response.status_code==200,response.text
    return response.json()


@pytest.mark.parametrize('profile_id',[None,'FLASH_NONE'])
def test_reference_case_real_upload_followup_flow_is_proven_and_human_owned(client,project,profile_id):
    """Exercise parser, gateway, immutable result, case, and receipt links together."""
    initial_document=upload(client,project['id'],'initial-note.txt',
                            b'No approved color is supplied in this initial note.')
    initial_run=_reference_run(client,project['id'])

    def decision(_payload,content):
        if len(calls)==1:
            return {'status':'NEED_USER_INPUT','reason_code':'MISSING_PROJECT_FILE',
                    'missing_facts':['A field confirmation document is required.'],
                    'requests':[],'answer':_empty_answer()}
        supplied=next(item for item in content['evidence']
                      if 'The approved color is blue.' in item['text'])
        return _provider_answer(supplied['evidence_ref'])

    calls=_channel(client,decision)
    initial=_ask(client,project['id'],initial_run['id'],profile_id)
    assert initial['status']=='NEED_USER_INPUT' and initial['model_called'] is True
    assert len(calls)==1
    original=client.get(f"/api/reference-results/{initial['result_id']}")
    assert original.status_code==200,original.text
    original_result=copy.deepcopy(original.json())

    created=client.post(f'/api/projects/{project["id"]}/reference-cases',json={
        'run_id':initial_run['id'],'question':QUESTION,'result_id':initial['result_id'],
        'note':'Awaiting a field confirmation document.'})
    assert created.status_code==201,created.text
    case=created.json()
    assert case['source']['status']=='NEED_USER_INPUT' and case['status']=='OPEN'

    supplemental=upload(client,project['id'],'field-confirmation.txt',
                        b'The approved color is blue.')
    attached=client.post(f"/api/reference-cases/{case['case_id']}/update",json={
        'expected_version':case['version'],'attachments':[supplemental['document_id']],
        'note':'Attached the field confirmation.'})
    assert attached.status_code==200,attached.text
    case=attached.json()
    assert [item['document_id'] for item in case['attachments']]==[supplemental['document_id']]

    followup_run=_reference_run(client,project['id'])
    assert followup_run['snapshot_id']!=initial_run['snapshot_id']
    assert set(followup_run['document_ids'])=={
        initial_document['document_id'],supplemental['document_id']}
    answered=_ask(client,project['id'],followup_run['id'],profile_id)
    assert answered['status']=='ANSWERED' and answered['model_called'] is True
    assert len(calls)==2
    for result in (initial,answered):
        assert result['execution_profile'].get('profile_id')==profile_id
        assert len(result['execution_receipts'])==1
        receipt=result['execution_receipts'][0]
        call=client.app.state.db.one('''SELECT reference_input_commitment_version,
            reference_input_commitment_sha256 FROM model_calls WHERE id=?''',
            (receipt['model_call_id'],))
        if profile_id:
            assert call['reference_input_commitment_version']
            assert call['reference_input_commitment_sha256']
        else:
            assert call['reference_input_commitment_version'] is None
            assert call['reference_input_commitment_sha256'] is None

    link_body={
        'expected_version':case['version'],'run_id':followup_run['id'],
        'result_id':answered['result_id'],'supplemental_document_ids':[supplemental['document_id']],
        'note':'Linked the saved answer using the field confirmation.'}
    case_before_stale=copy.deepcopy(case)
    stale=client.post(f"/api/reference-cases/{case['case_id']}/follow-up-results",json={
        **link_body,'expected_version':created.json()['version']})
    assert stale.status_code==409,stale.text
    after_stale=client.get(f"/api/reference-cases/{case['case_id']}").json()
    assert after_stale==case_before_stale
    assert len(calls)==2

    linked=client.post(f"/api/reference-cases/{case['case_id']}/follow-up-results",json=link_body)
    assert linked.status_code==200,linked.text
    case=linked.json()
    assert case['status']=='IN_REVIEW' and case['resolution']=='' and len(case['followups'])==1
    proof=case['followups'][0]['proof']['documents'][0]
    assert proof['document_id']==supplemental['document_id']
    assert proof['text_input_rounds']==[1] and proof['text_input_count']>=1
    assert proof['citation_count']==1

    replay=client.post(f"/api/reference-cases/{case['case_id']}/follow-up-results",json=link_body)
    assert replay.status_code==200,replay.text
    assert replay.json()['case_id']==case['case_id'] and len(calls)==2

    unchanged=client.get(f"/api/reference-results/{initial['result_id']}")
    assert unchanged.status_code==200 and unchanged.json()==original_result
    resolved=client.post(f"/api/reference-cases/{case['case_id']}/update",json={
        'expected_version':case['version'],'status':'RESOLVED',
        'resolution':'Human reviewer confirmed the field document.'})
    assert resolved.status_code==200, resolved.text
    reopened=client.post(f"/api/reference-cases/{case['case_id']}/update",json={
        'expected_version':resolved.json()['version'],'status':'OPEN',
        'note':'Human reviewer reopened the case for a later check.'})
    assert reopened.status_code==200,reopened.text
    assert reopened.json()['status']=='OPEN' and reopened.json()['resolution']==''
    assert all(event['actor']=='local-user' for event in reopened.json()['history'])
    assert len(calls)==2
