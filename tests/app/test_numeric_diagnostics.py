"""Offline coverage for source-text-free numeric rejection diagnostics."""
from dataclasses import replace
from decimal import Decimal
import json

import httpx
import pytest

from app.answer_diagnostics import NumericEvidenceError,valid_semantic_detail
from app.db import DomainError,dumps
from app.gateway import Gateway,InvalidModelOutput
from app.project_qa_v2 import validate_answer_v2
from app.settings import Settings
from .conftest import upload
from .test_evidence_loop import _provider_answer
from .test_reference_profile_comparisons import _channel,_create,_execute
from .test_reference_results import _saved_run


def _answer(*,claims,calculations=None):
    return {'status':'ANSWERED','answer':' '.join(item['text'] for item in claims),
            'claims':claims,'missing':[],'calculations':calculations or []}


def _text_citation(evidence_id,quote):
    return {'type':'TEXT','evidence_id':evidence_id,'quote':quote}


def _settings(tmp_path):
    settings=Settings(tmp_path,provider='deepseek',live_enabled=True,
                      api_key='test-not-real',start_worker=False)
    return replace(settings)


def _run(db,project_id,runner):
    run=runner.create(project_id)
    db.execute("UPDATE runs SET status='PARTIAL',provider='deepseek' WHERE id=?",(run['id'],))
    return runner.get(run['id'])


def test_claim_diagnostic_distinguishes_cited_and_supplied_rows_without_leakage():
    rows=[
        {'evidence_id':'E1','raw_text':'cited source says 7; top secret 8001',
         'prompt_text':'cited source says 7'},
        {'evidence_id':'E2','raw_text':'other source says 9','prompt_text':'other source says 9',
         'layout_lines':'layout-only 123456'},
    ]
    claim={'text':'The answer is 9.','citations':[_text_citation('E1','cited source says 7')]}

    with pytest.raises(NumericEvidenceError) as raised:
        validate_answer_v2(_answer(claims=[claim]),rows,'What is the answer?')

    detail=raised.value.semantic_detail
    assert detail=={
        'reason':'CLAIM_NUMBER_UNSUPPORTED','claim_index':0,'numeric_index':0,
        'text_citation_count':1,'image_citation_count':0,
        'number_seen_in_cited_source':False,'number_seen_in_supplied_text':True,
    }
    encoded=dumps(detail)
    assert 'top secret' not in encoded and '8001' not in encoded and '123456' not in encoded
    assert '9' not in encoded and valid_semantic_detail(detail)


def test_claim_numeric_index_uses_literal_occurrence_order_not_set_order():
    rows=[{'evidence_id':'E1','raw_text':'The source says 8.','prompt_text':'The source says 8.'}]
    claim={'text':'First 8, then 9.','citations':[_text_citation('E1','The source says 8.')]}

    with pytest.raises(NumericEvidenceError) as raised:
        validate_answer_v2(_answer(claims=[claim]),rows,'What values apply?')

    assert raised.value.semantic_detail['numeric_index']==1


def test_supplied_number_flag_matches_existing_decimal_equivalence_only():
    rows=[
        {'evidence_id':'E1','raw_text':'The cited value is 7.','prompt_text':'The cited value is 7.'},
        {'evidence_id':'E2','raw_text':'Elsewhere it is 2,000.00.','prompt_text':'Elsewhere it is 2,000.00.'},
    ]
    claim={'text':'The answer is 2000.','citations':[_text_citation('E1','The cited value is 7.')]}

    with pytest.raises(NumericEvidenceError) as raised:
        validate_answer_v2(_answer(claims=[claim]),rows,'What is the answer?')

    assert raised.value.semantic_detail['number_seen_in_supplied_text'] is True


@pytest.mark.parametrize('scope,cited,supplied',[
    ('unsent_tail',True,False),('layout_only',False,False),
    ('quote_omits',True,True),('raw_fallback',True,True),
])
def test_numeric_diagnostic_observation_domains_do_not_expand(scope,cited,supplied):
    row={'evidence_id':'E1','raw_text':'Only 7.','prompt_text':'Only 7.'}
    if scope=='layout_only':row['layout_lines']='Diagram only: 9'
    else:row['raw_text']='Only 7. Another context says 9.'
    if scope=='quote_omits':row['prompt_text']=row['raw_text']
    if scope=='raw_fallback':row.pop('prompt_text')
    claim={'text':'The answer is 9.','citations':[_text_citation('E1','Only 7.')]}
    with pytest.raises(NumericEvidenceError) as raised:
        validate_answer_v2(_answer(claims=[claim]),[row],'What value applies?')
    assert raised.value.semantic_detail['number_seen_in_cited_source'] is cited
    assert raised.value.semantic_detail['number_seen_in_supplied_text'] is supplied


def test_operand_diagnostic_has_its_own_closed_shape_and_source_scope():
    rows=[
        {'evidence_id':'E1','raw_text':'The cited value is 7.','prompt_text':'The cited value is 7.'},
        {'evidence_id':'E2','raw_text':'Another supplied row says 9.','prompt_text':'Another supplied row says 9.'},
    ]
    claim={'text':'Calculation requested.','citations':[_text_citation('E1','The cited value is 7.')]}
    calculation={'operator':'ADD','operands':['7','9'],'result':'16',
                 'citations':[_text_citation('E1','The cited value is 7.')]}

    with pytest.raises(NumericEvidenceError) as raised:
        validate_answer_v2(_answer(claims=[claim],calculations=[calculation]),rows,'Calculate the total.')

    assert raised.value.semantic_detail=={
        'reason':'OPERAND_UNSUPPORTED','calculation_index':0,'operand_index':1,
        'text_citation_count':1,'image_citation_count':0,
        'number_seen_in_cited_source':False,'number_seen_in_supplied_text':True,
    }


@pytest.mark.parametrize('detail',[
    {'reason':'CLAIM_NUMBER_UNSUPPORTED','claim_index':0,'numeric_index':0,
     'calculation_index':0,'text_citation_count':0,'image_citation_count':0,
     'number_seen_in_cited_source':False,'number_seen_in_supplied_text':False},
    {'reason':'OPERAND_UNSUPPORTED','calculation_index':0,'operand_index':0,
     'text_citation_count':False,'image_citation_count':0,
     'number_seen_in_cited_source':False,'number_seen_in_supplied_text':False},
    {'reason':'CLAIM_NUMBER_UNSUPPORTED','claim_index':1_000_001,'numeric_index':0,
     'text_citation_count':0,'image_citation_count':0,
     'number_seen_in_cited_source':False,'number_seen_in_supplied_text':False},
    {'reason':[],'claim_index':0,'numeric_index':0,
     'text_citation_count':0,'image_citation_count':0,
     'number_seen_in_cited_source':False,'number_seen_in_supplied_text':False},
])
def test_detail_validator_rejects_extra_types_and_ranges(detail):
    assert not valid_semantic_detail(detail)


def test_database_rejects_malformed_numeric_detail_before_settlement(client,project):
    db=client.app.state.db
    upload(client,project['id'],'synthetic.txt',b'synthetic')
    run=_run(db,project['id'],client.app.state.runner)
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?",(run['id'],))
    call=db.reserve(project['id'],run['id'],'synthetic-numeric-detail',Decimal('0'),'test-model',
                    'a'*64,Decimal('0'),Decimal('0'),allow_zero=True)
    malformed={'kind':'CONTRACT_ERROR','class':'PROJECT_ANSWER','exception':'ValueError',
               'validator':'numeric_support','semantic_detail':{
                   'reason':'CLAIM_NUMBER_UNSUPPORTED','claim_index':0,'numeric_index':0,
                   'calculation_index':0,'text_citation_count':0,'image_citation_count':0,
                   'number_seen_in_cited_source':False,'number_seen_in_supplied_text':False}}

    with pytest.raises(DomainError,match='安全终态'):
        db.finalize_model_call(call,Decimal('0'),{'prompt_tokens':1},None,diagnostic=malformed)
    assert db.one('SELECT actual_units,state,error FROM model_calls WHERE id=?',(call,))['actual_units'] is None

    malformed['semantic_detail']=None
    with pytest.raises(DomainError,match='安全终态'):
        db.finalize_model_call(call,Decimal('0'),{'prompt_tokens':1},None,diagnostic=malformed)
    assert db.one('SELECT actual_units,state,error FROM model_calls WHERE id=?',(call,))['actual_units'] is None


@pytest.mark.parametrize('result_class',['PROJECT_ANSWER','PROJECT_EVIDENCE_DECISION'])
@pytest.mark.parametrize('reason,message',[
    ('CLAIM_NUMBER_UNSUPPORTED','QA V2 claim contains a number absent from its citations'),
    ('OPERAND_UNSUPPORTED','QA V2 calculation operand is not in its cited source text'),
])
def test_internal_numeric_detail_preserves_every_legacy_diagnostic_field(client,result_class,reason,message):
    gateway=client.app.state.gateway
    detail={'reason':reason,'text_citation_count':1,'image_citation_count':0,
            'number_seen_in_cited_source':False,'number_seen_in_supplied_text':False}
    detail.update({'claim_index':0,'numeric_index':0} if reason=='CLAIM_NUMBER_UNSUPPORTED'
                  else {'calculation_index':0,'operand_index':0})
    old=gateway._terminal_diagnostic(result_class,ValueError(message))
    new=gateway._terminal_diagnostic(result_class,NumericEvidenceError(message,detail))
    assert new.pop('semantic_detail')==detail
    assert new==old
    policy=gateway._terminal_diagnostic(result_class,NumericEvidenceError(message,detail),kind='POLICY_ERROR')
    assert 'semantic_detail' not in policy
    spoofed=ValueError(message)
    spoofed.semantic_detail=detail
    assert 'semantic_detail' not in gateway._terminal_diagnostic(result_class,spoofed)


def test_database_revalidates_all_detail_fields_and_kind_before_any_write(client,project):
    db=client.app.state.db
    upload(client,project['id'],'synthetic.txt',b'synthetic')
    run=_run(db,project['id'],client.app.state.runner)
    db.execute("UPDATE runs SET status='RUNNING' WHERE id=?",(run['id'],))
    call=db.reserve(project['id'],run['id'],'synthetic-detail-boundary',Decimal('0'),'test-model',
                    'b'*64,Decimal('0'),Decimal('0'),allow_zero=True)
    detail={'reason':'CLAIM_NUMBER_UNSUPPORTED','claim_index':0,'numeric_index':1,
            'text_citation_count':1,'image_citation_count':0,
            'number_seen_in_cited_source':False,'number_seen_in_supplied_text':True}
    valid={'kind':'CONTRACT_ERROR','class':'PROJECT_EVIDENCE_DECISION','exception':'ValueError',
           'validator':'numeric_support','semantic_detail':detail}
    bad_details=[None,[],{**detail,'raw_text':'PRIVATE_MARKER'},
                 {**detail,'reason':{}},{**detail,'reason':'UNKNOWN'},
                 {**detail,'claim_index':True},{**detail,'numeric_index':-1},
                 {**detail,'image_citation_count':1_000_001},
                 {**detail,'number_seen_in_supplied_text':1},
                 {**detail,'operand_index':0},{**detail,'number_value':'9000'}]
    before=db.one('SELECT * FROM model_calls WHERE id=?',(call,))
    for bad in [*[{**valid,'semantic_detail':item} for item in bad_details],
                {**valid,'kind':'POLICY_ERROR'},{**valid,'class':'MATERIAL_EXTRACTION'},
                {**valid,'validator':'citation_scope'},{**valid,'exception':'OSError'},
                {**valid,'path':'claims.0'},
                {key:value for key,value in valid.items() if key!='validator'},
                {key:value for key,value in valid.items() if key!='exception'}]:
        with pytest.raises(DomainError,match='安全终态'):
            db.finalize_model_call(call,Decimal('0'),{},None,diagnostic=bad)
        assert db.one('SELECT * FROM model_calls WHERE id=?',(call,))==before
    db.finalize_model_call(call,Decimal('0'),{},None,diagnostic=valid)
    assert json.loads(db.one('SELECT error FROM model_calls WHERE id=?',(call,))['error'])==valid
    db.finalize_model_call(call,Decimal('0'),{},None,diagnostic=valid)


def test_gateway_omits_inconsistent_optional_detail_and_keeps_safe_old_error(client):
    detail={'reason':'OPERAND_UNSUPPORTED','calculation_index':0,'operand_index':0,
            'text_citation_count':0,'image_citation_count':0,
            'number_seen_in_cited_source':False,'number_seen_in_supplied_text':False}
    message='QA V2 claim contains a number absent from its citations'
    gateway=client.app.state.gateway
    assert gateway._terminal_diagnostic('PROJECT_EVIDENCE_DECISION',NumericEvidenceError(message,detail))==(
        gateway._terminal_diagnostic('PROJECT_EVIDENCE_DECISION',ValueError(message)))


def test_named_v3_failure_retains_safe_diagnostic_with_commitment_and_no_retry(client,project):
    secret='PRIVATE_SOURCE_MARKER'
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA',text=f'{secret}. The approved value is 7.')
    rejected=_provider_answer()
    rejected['answer']['claims'][0]['text']='The approved value is 9.'
    calls=_channel(client,lambda *_:rejected)
    evaluation=_create(client,project,run,'FLASH_NONE','What is the approved value?')
    first=_execute(client,evaluation)
    assert first['result'] is None
    failure=first['failure']
    assert failure['validator_category']=='numeric_support'
    call=db.one('SELECT * FROM model_calls WHERE id=?',
                (failure['execution_receipt']['model_call_id'],))
    diagnostic=json.loads(call['error'])
    assert call['state']=='SETTLED_ERROR' and call['response'] is None
    assert call['reference_input_commitment_sha256']
    assert diagnostic['semantic_detail']['reason']=='CLAIM_NUMBER_UNSUPPORTED'
    assert diagnostic['semantic_detail']['number_seen_in_supplied_text'] is False
    assert secret not in call['error'] and 'The approved value is 9.' not in call['error']
    assert _execute(client,evaluation)['replayed'] is True
    assert len(calls)==1


def test_gateway_settles_safe_numeric_detail_and_recovery_does_not_send_again(client,project,tmp_path):
    db=client.app.state.db
    upload(client,project['id'],'synthetic.txt',b'synthetic')
    run=_run(db,project['id'],client.app.state.runner)
    rows=[{'evidence_id':'E1','raw_text':'Cited source says 7.','prompt_text':'Cited source says 7.'}]
    rejected=_answer(claims=[{'text':'The answer is 9.',
                              'citations':[_text_citation('E1','Cited source says 7.')]}])
    calls=[]
    def transport(request):
        calls.append(request)
        return httpx.Response(200,json={'id':'synthetic-upstream','choices':[{
            'finish_reason':'stop','message':{'content':json.dumps(rejected)}}],
            'usage':{'prompt_tokens':1,'completion_tokens':1}})
    gateway=Gateway(_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(transport)))

    with pytest.raises(InvalidModelOutput):
        gateway.answer_v2(run,'What is the answer?',rows,[])
    stored=db.one('SELECT state,error FROM model_calls WHERE run_id=?',(run['id'],))
    diagnostic=json.loads(stored['error'])
    assert stored['state']=='SETTLED_ERROR'
    assert diagnostic['exception']=='ValueError' and diagnostic['validator']=='citation_scope'
    assert diagnostic['semantic_detail']['reason']=='CLAIM_NUMBER_UNSUPPORTED'
    assert '9' not in dumps(diagnostic['semantic_detail'])

    with pytest.raises(InvalidModelOutput):
        gateway.answer_v2(run,'What is the answer?',rows,[])
    assert len(calls)==1
    gateway.close()
