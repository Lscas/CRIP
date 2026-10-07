"""Frozen-evaluation/result ownership guards, using synthetic HTTP only."""
import hashlib
import json

import pytest

from app.db import DomainError
from .test_evidence_loop import _provider_answer
from .test_reference_profile_comparisons import _channel,_compare,_create,_execute
from .test_reference_results import _saved_run


def test_legacy_evaluation_rejects_attaching_named_result(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    calls=_channel(client,lambda *_:_provider_answer())
    named=_create(client,project,run,'FLASH_NONE')
    named_result=_execute(client,named)['result']
    legacy=_create(client,project,run,None)
    item=legacy['items'][0]
    with pytest.raises(DomainError) as rejected:
        client.app.state.reference_evaluations.attach(
            legacy['evaluation_id'],item['item_id'],named_result['result_id'])
    assert rejected.value.code==409 and len(calls)==1
    assert db.one('SELECT result_id FROM reference_evaluation_items WHERE id=?',
                  (item['item_id'],))['result_id'] is None


@pytest.mark.parametrize('corruption', [
    'wrong_profile', 'wrong_question', 'strip_result_profile',
    'strip_evaluation_profile', 'legacy_target',
    'missing_receipts', 'missing_call', 'malformed_receipt',
])
def test_all_terminal_consumers_reject_wrong_result_ownership_without_side_effects(
        client, project, corruption):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    calls=_channel(client,lambda *_:_provider_answer())
    target=_create(client,project,run,None if corruption=='legacy_target' else 'FLASH_NONE')
    own=_execute(client,target)['result']
    question=('What approved color applies to Finish key PT9?'
              if corruption=='wrong_question' else target['items'][0]['question'])
    other=_create(client,project,run,'FLASH_NONE' if corruption=='wrong_question' else 'PRO',question)
    other_result=_execute(client,other)['result']
    comparison_peer=(_create(client,project,run,'PRO')
                     if corruption=='wrong_question' else other)
    target_id=target['evaluation_id'];item_id=target['items'][0]['item_id']
    store=client.app.state.reference_evaluations
    # Establish that both original immutable results are authentic before tampering.
    store.authenticate_item_result(target,target['items'][0],own['result_id'])
    store.authenticate_item_result(other,other['items'][0],other_result['result_id'])
    result_id=own['result_id']
    if corruption in ('wrong_profile','wrong_question','legacy_target'):
        result_id=other_result['result_id']
        db.execute('UPDATE reference_evaluation_items SET result_id=? WHERE id=?',
                   (result_id,item_id))
    elif corruption=='strip_evaluation_profile':
        profile=dict(target['profile'])
        for key in ('profile_version','profile_id','max_output_tokens'):profile.pop(key)
        db.execute('UPDATE reference_evaluations SET profile_json=? WHERE id=?',
                   (json.dumps(profile),target_id))
    else:
        raw=db.one('SELECT result_json FROM reference_results WHERE id=?',(result_id,))
        result=json.loads(raw['result_json'])
        if corruption=='missing_receipts':
            result.pop('execution_receipts')
        elif corruption=='missing_call':
            absent_call='CALL-'+'0'*32
            assert db.one('SELECT id FROM model_calls WHERE id=?',(absent_call,),False) is None
            result['execution_receipts'][0]['model_call_id']=absent_call
        elif corruption=='malformed_receipt':
            result['execution_receipts'][0]['round']='not-an-integer'
        else:
            for key in ('profile_version','profile_id','max_output_tokens'):
                result['execution_profile'].pop(key)
        serialized=json.dumps(result,ensure_ascii=False,separators=(',',':'))
        db.execute('UPDATE reference_results SET result_json=?,result_hash=? WHERE id=?',
                   (serialized,hashlib.sha256(serialized.encode()).hexdigest(),result_id))
    before={table:db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in (
        'model_calls','reference_evaluation_items','reference_evaluation_adjudications',
        'reference_evaluation_adjudication_events')}
    prefix=f'/api/reference-evaluations/{target_id}'
    replay=client.post(prefix+f'/items/{item_id}/execute')
    card=client.get(prefix+'/scorecard')
    comparison=_compare(client,project,target,comparison_peer)
    adjudicated=client.post(prefix+f'/items/{item_id}/adjudication',json={
        'verdict':'FULLY_USABLE','unsupported_claim':False,'expected_version':0,
        'note':'Synthetic guard must reject this write.'})
    for response in (replay,card,comparison,adjudicated):
        assert response.status_code==409,response.text
    with pytest.raises(DomainError) as rejected:
        store.attach(target_id,item_id,result_id)
    assert rejected.value.code==409
    assert len(calls)==2
    assert before=={table:db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in before}


@pytest.mark.parametrize('sibling_kind',['result','failure'])
def test_adjudication_authenticates_corrupt_sibling_before_any_write(client,project,sibling_kind):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    second_question='What approved color applies to Finish key PT9?'
    calls=_channel(client,lambda _,content: (
        'not-json' if sibling_kind=='failure' and content['question']==second_question
        else _provider_answer()))
    created=client.post(f"/api/projects/{project['id']}/reference-evaluations",json={
        'run_id':run['id'],'name':'Synthetic sibling integrity',
        'questions':['What approved color applies?',second_question],'profile_id':'FLASH_NONE'})
    assert created.status_code==201,created.text
    evaluation=created.json()
    _execute(client,evaluation)
    second=_execute(client,{**evaluation,'items':[evaluation['items'][1]]})
    if sibling_kind=='result':
        receipt=second['result']['result']['execution_receipts'][0]
    else:
        receipt=second['failure']['execution_receipt']
    prefix=f"/api/reference-evaluations/{evaluation['evaluation_id']}"
    valid=client.get(prefix+'/scorecard')
    assert valid.status_code==200,valid.text
    db.execute('UPDATE model_calls SET reference_input_commitment_sha256=? WHERE id=?',
               ('0'*64,receipt['model_call_id']))
    rejected=client.post(prefix+f"/items/{evaluation['items'][0]['item_id']}/adjudication",json={
        'verdict':'FULLY_USABLE','unsupported_claim':False,'expected_version':0,'note':'Synthetic'})
    assert rejected.status_code==409,rejected.text
    for table in ('reference_evaluation_adjudications','reference_evaluation_adjudication_events'):
        assert db.one(f'SELECT COUNT(*) AS n FROM {table}')['n']==0
    assert len(calls)==2
