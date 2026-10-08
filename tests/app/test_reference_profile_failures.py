"""Named-profile terminal-failure authentication, with no external transport."""
from __future__ import annotations

import json

import pytest

from app.db import DomainError
from .test_reference_profile_comparisons import _channel,_compare,_create
from .test_reference_results import _saved_run


def _malformed(*_args):
    # Valid provider envelope, deliberately invalid bounded-decision body.
    return {'status':'NOT_A_REFERENCE_DECISION'}


def _named_failure(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    calls=_channel(client,_malformed)
    evaluation=_create(client,project,run,'FLASH_NONE')
    item=evaluation['items'][0]
    response=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{item['item_id']}/execute")
    assert response.status_code==200,response.text
    body=response.json();failure=body['failure']
    assert failure is not None and len(calls)==1
    return db,evaluation,item,failure,calls


def test_named_contract_failure_is_saved_and_replayed_without_second_http(client,project):
    db,evaluation,item,failure,calls=_named_failure(client,project)
    replay=client.post(
        f"/api/reference-evaluations/{evaluation['evaluation_id']}"
        f"/items/{item['item_id']}/execute")
    assert replay.status_code==200,replay.text
    assert replay.json()['replayed'] is True and replay.json()['failure']==failure
    assert len(calls)==1
    store=client.app.state.reference_evaluations
    _,again=store.fail(evaluation['evaluation_id'],item['item_id'],
                        'MODEL_OUTPUT_REJECTED',failure['execution_receipt'])
    assert again==failure and len(calls)==1
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluation_failures')['n']==1


@pytest.mark.parametrize('tamper',[
    'policy','commitment','legacy_call','stripped_profile',
    'missing_receipt','missing_call','malformed_receipt',
])
def test_tampered_named_failure_cannot_replay_or_be_reused(client,project,tamper):
    db,evaluation,item,failure,calls=_named_failure(client,project)
    receipt=failure['execution_receipt'];call_id=receipt['model_call_id']
    if tamper=='policy':
        db.execute('UPDATE model_calls SET error=? WHERE id=?',(
            json.dumps({'kind':'POLICY_ERROR','class':'REASONING_NOT_DISABLED'}),call_id))
    elif tamper=='commitment':
        db.execute('UPDATE model_calls SET reference_input_commitment_sha256=? WHERE id=?',
                   ('0'*64,call_id))
    elif tamper=='legacy_call':
        db.execute('''UPDATE model_calls SET reference_input_commitment_version=NULL,
                      reference_input_commitment_sha256=NULL WHERE id=?''',(call_id,))
    elif tamper=='stripped_profile':
        row=db.one('SELECT profile_json FROM reference_evaluations WHERE id=?',
                   (evaluation['evaluation_id'],))
        profile=json.loads(row['profile_json'])
        for key in ('profile_version','profile_id','max_output_tokens'):profile.pop(key)
        db.execute('UPDATE reference_evaluations SET profile_json=? WHERE id=?',(
            json.dumps(profile),evaluation['evaluation_id']))
    elif tamper=='missing_receipt':
        db.execute('UPDATE reference_evaluation_failures SET execution_receipt_json=NULL WHERE id=?',
                   (failure['failure_id'],))
    else:
        absent_call='CALL-'+'0'*32
        assert db.one('SELECT id FROM model_calls WHERE id=?',(absent_call,),False) is None
        receipt['model_call_id' if tamper=='missing_call' else 'round']=(
            absent_call if tamper=='missing_call' else 'not-an-integer')
        db.execute('UPDATE reference_evaluation_failures SET execution_receipt_json=? WHERE id=?',
                   (json.dumps(receipt),failure['failure_id']))
    count=db.one('SELECT COUNT(*) AS n FROM reference_evaluation_failures')['n']
    with pytest.raises(DomainError) as rejected:
        client.app.state.reference_evaluations.fail(
            evaluation['evaluation_id'],item['item_id'],'MODEL_OUTPUT_REJECTED',receipt)
    assert rejected.value.code==409
    assert db.one('SELECT COUNT(*) AS n FROM reference_evaluation_failures')['n']==count
    replay=client.post(f"/api/reference-evaluations/{evaluation['evaluation_id']}"
                      f"/items/{item['item_id']}/execute")
    assert replay.status_code==409,replay.text
    card=client.get(f"/api/reference-evaluations/{evaluation['evaluation_id']}/scorecard")
    assert card.status_code==409,card.text
    peer=client.post(f"/api/reference-evaluations/{evaluation['evaluation_id']}/clone",
                     json={'profile_id':'PRO'})
    # Clone is only metadata. A malformed stored receipt may reject even that read.
    if tamper=='malformed_receipt':
        assert peer.status_code==409,peer.text
    else:
        assert peer.status_code==201,peer.text
        compared=_compare(client,project,evaluation,peer.json()['evaluation'])
        assert compared.status_code==409,compared.text
    assert len(calls)==1
