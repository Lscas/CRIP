import json
import io
import base64
import hashlib
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app.evidence_loop import (ProjectEvidenceLoop,_answer_parts,_fit_rows,
                               validate_evidence_decision)
from app.db import dumps
from app.gateway import Gateway,InvalidModelOutput,ModelResult,_evidence_source_groups
from app.reference_results import ReferenceResultStore
from app.settings import Settings
from .test_project_qa_v2 import _qa_fixture


def _answer(evidence_id='EV-1',quote='The approved color is blue.'):
    return {
        'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE','missing_facts':[],'requests':[],
        'answer':{'status':'ANSWERED','answer':'The approved color is blue.',
                  'claims':[{'text':'The approved color is blue.','citations':[
                      {'type':'TEXT','evidence_id':evidence_id,'quote':quote}]}],
                  'missing':[],'calculations':[]},
    }


def _empty_answer():
    return {'claims':[],'calculations':[],'coverage':[]}


def _provider_answer(evidence_ref='E1'):
    return {
        'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE','missing_facts':[],'requests':[],
        'answer':{'claims':[{'text':'The approved color is blue.','citations':[
            {'type':'TEXT','evidence_ref':evidence_ref}]}],
            'calculations':[],
            'coverage':[{'part_ref':'P1','claim_indexes':[0],
                         'calculation_indexes':[]}]},
    }


def test_decision_contract_accepts_narrow_request_and_rejects_repeat():
    decision={'status':'NEED_EVIDENCE','reason_code':'MISSING_IDENTIFIER',
              'missing_facts':['door requirements'],'requests':[
                  {'tool':'FIND_IDENTIFIER','query':'Sheet A5.01'}], 'answer':_empty_answer()}

    validate_evidence_decision(decision,[],'What are the door requirements?')
    with pytest.raises(ValueError,match='duplicated'):
        validate_evidence_decision(
            decision,[],'What are the door requirements?',decision['requests'])
    invalid=json.loads(json.dumps(decision));invalid['requests'][0]['query']='door requirements'
    with pytest.raises(ValueError,match='no supported explicit identifier'):
        validate_evidence_decision(invalid,[],'What are the door requirements?')
    leaked={**decision,'reasoning':'hidden'}
    with pytest.raises(Exception):
        validate_evidence_decision(leaked,[],'What are the door requirements?')


def test_decision_contract_uses_stable_empty_answer_and_generic_missing_fact():
    decision={
        'status':'CANNOT_ANSWER','reason_code':'NO_NEW_EVIDENCE',
        'missing_facts':[],'requests':[],'answer':_empty_answer(),
    }

    normalized=validate_evidence_decision(decision,[],'What is the approved color?')

    assert normalized['answer']==_empty_answer()
    assert normalized['missing_facts']==[
        'The supplied project evidence did not establish the requested facts.']


def test_decision_contract_reuses_qa_v2_claim_validation():
    evidence=[{'evidence_id':'EV-1','raw_text':'The approved color is blue.',
               'prompt_text':'The approved color is blue.'}]

    normalized=validate_evidence_decision(
        _provider_answer(),evidence,'What is the approved color?')
    assert normalized==_answer()
    wrong=_provider_answer();wrong['answer']['claims'][0]['text']='The approved color is blue code 3.'
    with pytest.raises(ValueError,match='number absent'):
        validate_evidence_decision(wrong,evidence,'What is the approved color?')
    outside=_provider_answer('E2')
    with pytest.raises(ValueError,match='outside the evidence bundle'):
        validate_evidence_decision(outside,evidence,'What is the approved color?')


def test_answer_parts_split_only_explicit_lists_and_parallel_questions():
    d2=_answer_parts(
        'On sheet A5.01, what are the general requirements for door thickness, '
        'door-handle type, and the side of the frame where relite glazing and stops occur?')
    d4=_answer_parts(
        'In ASK 001.2 / RFI 016, how is the wall attached at the beam, and what '
        'gypsum-board repair and acoustic-sealant requirements are shown?')

    assert [item['part_ref'] for item in d2]==['P1','P2','P3']
    assert [item['text'] for item in d2]==[
        'what are the general requirements for door thickness','door-handle type',
        'the side of the frame where relite glazing and stops occur']
    assert [item['part_ref'] for item in d4]==['P1','P2']
    assert _answer_parts('What color is approved?')==[
        {'part_ref':'P1','text':'What color is approved'}]


def test_decision_contract_rejects_missing_empty_and_unmapped_part_coverage():
    question=(
        'What are the project name, DOE job number, project address, and stated scope of work?')
    evidence=[{'evidence_id':'EV-1','raw_text':'Project identity and scope are stated here.'}]
    claims=[{'text':text,'citations':[{'type':'TEXT','evidence_ref':'E1'}]} for text in (
        'The project name is stated.','The DOE job number is stated.',
        'The project address is stated.','The scope of work is stated.')]
    answer={
        'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE','missing_facts':[],'requests':[],
        'answer':{'claims':claims,'calculations':[],'coverage':[
            {'part_ref':f'P{index}','claim_indexes':[index-1],
             'calculation_indexes':[]} for index in range(1,5)]},
    }

    normalized=validate_evidence_decision(answer,evidence,question)
    assert normalized['status']=='ANSWER' and len(normalized['answer']['claims'])==4
    missing=json.loads(json.dumps(answer));missing['answer']['coverage'].pop()
    with pytest.raises(ValueError,match='coverage is incomplete'):
        validate_evidence_decision(missing,evidence,question)
    empty=json.loads(json.dumps(answer));empty['answer']['coverage'][0]['claim_indexes']=[]
    with pytest.raises(ValueError,match='empty part mapping'):
        validate_evidence_decision(empty,evidence,question)
    repeated=json.loads(json.dumps(answer));repeated['answer']['coverage'][0]['claim_indexes']=[0,0]
    with pytest.raises(ValueError,match='repeats an answer index'):
        validate_evidence_decision(repeated,evidence,question)
    unmapped=json.loads(json.dumps(answer));unmapped['answer']['coverage'][3]['claim_indexes']=[2]
    with pytest.raises(ValueError,match='unmapped answer content'):
        validate_evidence_decision(unmapped,evidence,question)


def test_decision_contract_compiles_requested_arithmetic_and_answer_text_locally():
    evidence=[
        {'evidence_id':'EV-CONTRACT','raw_text':'Contract duration: 589 calendar days.'},
        {'evidence_id':'EV-ONSITE','raw_text':'On-site construction duration: 367 calendar days.'},
    ]
    refs=[{'type':'TEXT','evidence_ref':'E1'},
          {'type':'TEXT','evidence_ref':'E2'}]
    citations=[{'type':'TEXT','evidence_id':'EV-CONTRACT',
                'quote':'Contract duration: 589 calendar days.'},
               {'type':'TEXT','evidence_id':'EV-ONSITE',
                'quote':'On-site construction duration: 367 calendar days.'}]
    decision={
        'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE','missing_facts':[],'requests':[],
        'answer':{
            'claims':[{'text':'The cited durations are 589 and 367 calendar days.',
                       'citations':refs}],
            'calculations':[{'operator':'SUBTRACT','operands':['589','367'],
                             'citations':refs}],
            'coverage':[{'part_ref':'P1','claim_indexes':[0],
                         'calculation_indexes':[0]}],
        },
    }

    normalized=validate_evidence_decision(
        decision,evidence,
        'How many calendar days are outside the 367-day on-site duration? Show the difference.')

    answer=normalized['answer']
    assert answer['answer']==(
        'The cited durations are 589 and 367 calendar days. 589 - 367 = 222.')
    assert answer['claims'][-1]=={'text':'589 - 367 = 222.','citations':citations}
    assert answer['calculations']==[{
        'operator':'SUBTRACT','operands':['589','367'],'result':'222','citations':citations}]
    assert 'answer' not in decision['answer'] and 'result' not in decision['answer']['calculations'][0]


def test_v3_prompt_exposes_nested_qa_v2_invariants_and_safe_diagnostics(client,tmp_path):
    gateway=Gateway(Settings(tmp_path/'v3-contract-prompt',start_worker=False),client.app.state.db)

    assert 'CIRP will join claim text, resolve source metadata' in gateway.evidence_decision_prompt
    assert 'an exact supplied `evidence_ref` such as `E1`' in gateway.evidence_decision_prompt
    assert 'one item may use `[base, percentage1, percentage2]`' in gateway.evidence_decision_prompt
    assert 'citations must collectively contain every operand' in gateway.evidence_decision_prompt
    assert 'visible only in `layout_lines` is not calculation support' in gateway.evidence_decision_prompt
    assert 'Do not state the result in a claim' in gateway.evidence_decision_prompt
    assert 'multi-field listing' in gateway.evidence_decision_prompt
    assert '`Sheet A3.40`' in gateway.evidence_decision_prompt
    assert '`Volume I cover sheet` is not a labelled identifier' in gateway.evidence_decision_prompt
    assert '`layout_lines`' in gateway.evidence_decision_prompt
    assert 'selected PDF page' in gateway.evidence_decision_prompt
    assert '`required_answer_parts`' in gateway.evidence_decision_prompt
    diagnostic=gateway._terminal_diagnostic(
        'PROJECT_EVIDENCE_DECISION',
        ValueError('QA V3 ANSWER decision is inconsistent'))
    assert diagnostic=={
        'kind':'CONTRACT_ERROR','class':'PROJECT_EVIDENCE_DECISION',
        'exception':'ValueError','validator':'answer_decision_consistency'}
    coverage_diagnostic=gateway._terminal_diagnostic(
        'PROJECT_EVIDENCE_DECISION',
        ValueError('QA V3 ANSWER coverage is incomplete'))
    assert coverage_diagnostic['validator']=='answer_part_coverage'
    operand_diagnostic=gateway._terminal_diagnostic(
        'PROJECT_EVIDENCE_DECISION',
        ValueError('QA V2 calculation operand is not in its cited source text'))
    assert operand_diagnostic['validator']=='calculation_operand_support'
    numeric_diagnostic=gateway._terminal_diagnostic(
        'PROJECT_EVIDENCE_DECISION',
        ValueError('QA V2 claim contains a number absent from its citations'))
    assert numeric_diagnostic['validator']=='numeric_support'
    gateway.close()


def test_prompt_rows_are_bounded_by_complete_serialized_evidence_size():
    rows=[{
        'evidence_id':f'EV-{index:04d}','file_name':'dense-specification.pdf',
        'locator':f'Page 1 > Paragraph {index}','raw_text':'Quoted "source" \\ text '+('x'*180),
        'layout_lines':'FIELD || '+('y'*80),
    } for index in range(300)]

    fitted=_fit_rows(rows,4_000,layout_limit=1_000)
    sent=[{
        'evidence_id':item['evidence_id'],'file_name':item.get('file_name'),
        'locator':item.get('locator'),'text':item['prompt_text'],
        **({'layout_lines':item['layout_lines']} if item.get('layout_lines') else {}),
    } for item in fitted]
    source_only=[{key:value for key,value in item.items() if key!='layout_lines'} for item in sent]

    assert fitted and len(fitted)<len(rows)
    assert len(dumps(sent).encode('utf-8'))<=5_000
    assert len(dumps(source_only).encode('utf-8'))<=4_000
    assert any(item.get('layout_lines') for item in sent)
    assert all(row['raw_text'].startswith(row['prompt_text']) for row in fitted)
    assert [row['evidence_id'] for row in fitted]==[
        row['evidence_id'] for row in rows[:len(fitted)]]


def test_relation_pack_groups_pages_and_restores_source_order_without_text():
    groups=_evidence_source_groups([
        {'evidence_id':'EV-2','document_id':'D1','file_name':'schedule.pdf',
         'source_order':12,'locator':{'page_number':3,'sheet':'A-601',
                                      'section':'Door Schedule','paragraph':'Table 1 Row 2'},
         'raw_text':'Door 102 | PT-2'},
        {'evidence_id':'EV-1','document_id':'D1','file_name':'schedule.pdf',
         'source_order':11,'locator':{'page_number':3,'sheet':'A-601',
                                      'section':'Door Schedule','paragraph':'Table 1 Row 1'},
         'raw_text':'Door | Finish'},
        {'evidence_id':'EV-3','document_id':'D2','file_name':'finish-spec.pdf',
         'source_order':20,'locator':{'page_number':8,'section':'09 91 00'},
         'raw_text':'PT-2 is the approved finish.'},
    ])

    assert groups==[
        {'group_ref':'G1','file_name':'schedule.pdf','page_number':3,
         'ordered_evidence_refs':['E2','E1'],'scopes':[
             {'evidence_refs':['E2'],'sheet':'A-601','section':'Door Schedule',
              'paragraph':'Table 1 Row 1'},
             {'evidence_refs':['E1'],'sheet':'A-601','section':'Door Schedule',
              'paragraph':'Table 1 Row 2'}]},
        {'group_ref':'G2','file_name':'finish-spec.pdf','page_number':8,
         'ordered_evidence_refs':['E3'],'scopes':[
             {'evidence_refs':['E3'],'section':'09 91 00'}]},
    ]
    serialized=dumps(groups)
    assert 'Door 102' not in serialized and 'approved finish' not in serialized


def test_v3_preview_is_independent_and_keeps_existing_routes(client,project):
    _,run,_=_qa_fixture(client,project,[
        {'name':'finish-spec.pdf','date':'2025-01-01','revision':'1',
         'fragments':[{'text':'The coating requirement is listed in the finish schedule.'}]},
    ])

    response=client.post(f"/api/projects/{project['id']}/questions-v3/preview",
                         json={'run_id':run['id'],'question':'What is the coating requirement?'})

    assert response.status_code==200,response.text
    body=response.json()
    assert body['qa_version']=='3' and body['feature']=='BOUNDED_EVIDENCE_LOOP'
    assert body['model_called'] is False and body['max_model_decisions']==3
    paths={route.path for route in client.app.routes}
    assert f'/api/projects/{{pid}}/questions' in paths
    assert f'/api/projects/{{pid}}/questions-v2' in paths
    assert f'/api/projects/{{pid}}/questions-v3' in paths


@pytest.mark.parametrize('selector_version',['literal-page-selector-7','literal-page-selector-8'])
def test_live_v3_requests_new_text_then_returns_claim_bound_answer(client,project,tmp_path,selector_version):
    db,run,_=_qa_fixture(client,project,[
        {'name':'finish-spec.pdf','date':'2025-01-01','revision':'1','fragments':[
            {'text':'The coating requirement is listed in the finish schedule.',
             'section':'Section 09 90 00','paragraph':'1.1','text_map':[
                 {'start':0,'end':3,'bbox':[10,10,25,20]},
                 {'start':4,'end':11,'bbox':[100,10,145,20]}]},
            {'text':'The approved color is blue. Finish key PT9 applies.',
             'page':2,'section':'Section 09 91 00','paragraph':'2.2','text_map':[
                 {'start':0,'end':3,'bbox':[10,10,25,20]},
                 {'start':4,'end':12,'bbox':[100,10,150,20]}]},
        ]},
    ])
    requests=[]

    def handler(request):
        payload=json.loads(request.content);requests.append(payload)
        content=json.loads(payload['messages'][1]['content'])
        assert content['required_answer_parts']==[
            {'part_ref':'P1','text':'What is required for coating'}]
        assert content['source_groups']
        grouped_refs=[ref for group in content['source_groups']
                      for ref in group['ordered_evidence_refs']]
        assert sorted(grouped_refs,key=lambda value:int(value[1:]))==[
            item['evidence_ref'] for item in content['evidence']]
        assert all(set(item)<= {'evidence_ref','text','layout_lines'}
                   for item in content['evidence'])
        assert all('file_name' in group for group in content['source_groups'])
        if content['round']==1:
            data={'status':'NEED_EVIDENCE','reason_code':'MISSING_SOURCE_TEXT',
                  'missing_facts':['approved coating color'],'requests':[
                      {'tool':'SEARCH_TEXT','query':'Finish key PT9 blue'}], 'answer':_empty_answer()}
        else:
            source=next(item for item in content['evidence'] if 'approved color is blue' in item['text'])
            data=_provider_answer(source['evidence_ref'])
        return httpx.Response(200,json={
            'id':f'qa-v3-{len(requests)}','choices':[{'finish_reason':'stop','message':{
                'content':json.dumps(data)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':30},
        })

    settings=Settings(tmp_path/'qa-v3-live',provider='deepseek',api_key='synthetic-key',
                      live_enabled=True)
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))

    service=ProjectEvidenceLoop(db,gateway)
    result=service.ask(run,'What is required for coating?',selector_version)
    recovered=service.ask(run,'What  is required for coating?',selector_version)

    assert result['status']=='ANSWERED' and result['answer']=='The approved color is blue.'
    assert result['model_call_count']==2 and len(result['decision_trace'])==2
    assert result['decision_trace'][0]['status']=='NEED_EVIDENCE'
    assert result['decision_trace'][1]['status']=='ANSWER'
    receipts=result['execution_receipts']
    assert [item['round'] for item in receipts]==[1,2]
    receipt_version='reference-model-input-receipt-'+('2' if selector_version.endswith('-8') else '1')
    assert all(item['receipt_version']==receipt_version
               and item['source_text_included'] is False
               and item['prompt_content_included'] is False
               and item['chain_of_thought_included'] is False
               and item['evidence_count']==len(item['evidence_inputs'])
               and all({'evidence_id','text_sha256'}<=set(evidence)
                       and ({'layout_sha256','layout_bytes'}<=set(evidence)
                            if 'layout_sha256' in evidence else set(evidence)=={
                                'evidence_id','text_sha256'})
                       for evidence in item['evidence_inputs']) for item in receipts)
    assert len(requests)==2
    assert all(item['evidence_ref'].startswith('E') and 'evidence_id' not in item
               for payload in requests
               for item in json.loads(payload['messages'][1]['content'])['evidence'])
    sent_batches=[json.loads(payload['messages'][1]['content'])['evidence'] for payload in requests]
    assert any(item.get('layout_lines') for batch in sent_batches for item in batch)
    for receipt,batch in zip(receipts,sent_batches):
        for recorded,sent in zip(receipt['evidence_inputs'],batch):
            if sent.get('layout_lines'):
                encoded=sent['layout_lines'].encode('utf-8')
                assert recorded['layout_bytes']==len(encoded)
                assert recorded['layout_sha256']==hashlib.sha256(encoded).hexdigest()
    assert recovered['answer']==result['answer'] and recovered['model_call_count']==0
    assert recovered['model_called'] is False and all(item['cached'] for item in recovered['decision_trace'])
    assert [item['model_call_id'] for item in recovered['execution_receipts']]==[
        item['model_call_id'] for item in receipts]
    assert [item['request_hash'] for item in recovered['execution_receipts']]==[
        item['request_hash'] for item in receipts]
    assert all(item['cached'] for item in recovered['execution_receipts'])
    assert len(requests)==2
    calls=db.all("SELECT id,task_key,state,request_hash FROM model_calls WHERE task_key LIKE 'answer-v3-r%'")
    assert len(calls)==2 and all(item['state']=='SETTLED' for item in calls)
    assert {(item['id'],item['request_hash']) for item in calls}=={
        (item['model_call_id'],item['request_hash']) for item in receipts}
    gateway.close()


def test_deepseek_v3_low_thinking_is_bounded_and_private_reasoning_is_not_saved(
        client,project,tmp_path):
    db,run,_=_qa_fixture(client,project,[
        {'name':'finish-spec.pdf','date':'2025-01-01','revision':'1','fragments':[
            {'text':'The approved color is blue. Finish key PT9 applies.',
             'page':2,'section':'Section 09 91 00','paragraph':'2.2'},
        ]},
    ])
    requests=[]

    def handler(request):
        payload=json.loads(request.content);requests.append(payload)
        assert payload['thinking']=={'type':'enabled'}
        assert payload['reasoning_effort']=='low'
        assert payload['max_tokens']==8000
        content=json.loads(payload['messages'][1]['content'])
        source=next(item for item in content['evidence']
                    if 'approved color is blue' in item['text'])
        return httpx.Response(200,json={
            'id':'qa-v3-thinking-low-1','choices':[{'finish_reason':'stop','message':{
                'reasoning_content':'private synthetic reasoning must never persist',
                'content':json.dumps(_provider_answer(source['evidence_ref']))}}],
            'usage':{'prompt_tokens':100,'completion_tokens':45,'total_tokens':145,
                     'completion_tokens_details':{'reasoning_tokens':15}},
        })

    settings=Settings(
        tmp_path/'qa-v3-thinking-low',provider='deepseek',api_key='synthetic-key',
        live_enabled=True,reference_reasoning_effort='low')
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))

    result=ProjectEvidenceLoop(db,gateway).ask(
        run,'What color is approved for Finish key PT9?')

    assert result['status']=='ANSWERED' and len(requests)==1
    receipt=result['execution_receipts'][0]
    assert receipt['inference_mode']=='thinking-low'
    assert receipt['chain_of_thought_included'] is False
    row=db.one('SELECT response,error,usage FROM model_calls WHERE run_id=?',(run['id'],))
    assert 'private synthetic reasoning' not in dumps(row)
    assert json.loads(row['usage'])['completion_tokens_details']['reasoning_tokens']==15
    gateway.close()


def test_live_v3_recovers_same_safe_receipt_after_contract_rejection_without_retry(
        client,project,tmp_path):
    db,run,_=_qa_fixture(client,project,[
        {'name':'finish-spec.pdf','date':'2025-01-01','revision':'1','fragments':[
            {'text':'The approved color is blue. Finish key PT9 applies.',
             'page':2,'section':'Section 09 91 00','paragraph':'2.2'},
        ]},
    ])
    requests=[]

    def handler(request):
        requests.append(request)
        invalid={
            'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE','missing_facts':[],
            'requests':[],'answer':{
                'status':'ANSWERED','answer':'untrusted response must not persist',
                'claims':[],'missing':[],'calculations':[],'coverage':[],
            },
        }
        return httpx.Response(200,json={
            'id':'qa-v3-rejected-1','choices':[{'finish_reason':'stop','message':{
                'content':json.dumps(invalid)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':20},
        })

    settings=Settings(tmp_path/'qa-v3-rejected',provider='deepseek',api_key='synthetic-key',
                      live_enabled=True)
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))
    service=ProjectEvidenceLoop(db,gateway)

    with pytest.raises(InvalidModelOutput) as first_error:
        service.ask(run,'What color is approved for Finish key PT9?')
    with pytest.raises(InvalidModelOutput) as recovered_error:
        service.ask(run,'What  color is approved for Finish key PT9?')

    first=first_error.value.execution_receipt
    recovered=recovered_error.value.execution_receipt
    assert first is not None and recovered is not None
    assert first['cached'] is False and recovered['cached'] is True
    assert recovered['model_call_id']==first['model_call_id']
    assert recovered['request_hash']==first['request_hash']
    assert recovered['evidence_inputs']==first['evidence_inputs']
    assert len(requests)==1
    call=db.one('SELECT id,state,request_hash,response FROM model_calls')
    assert call['id']==first['model_call_id'] and call['state']=='SETTLED_ERROR'
    assert call['request_hash']==first['request_hash'] and call['response'] is None
    gateway.close()


def test_custom_multimodal_v3_sends_only_selected_original_page_and_audits_hash(
        client,project,tmp_path):
    image=Image.new('RGB',(120,100),'white');raw=io.BytesIO();image.save(raw,format='PNG')
    db,run,_=_qa_fixture(client,project,[
        {'name':'pump-plan.png','date':'2025-01-01','revision':'1','raw':raw.getvalue(),
         'fragments':[{'text':'Pump P-101 location is identified on the plan.',
                       'section':'Mechanical plan','paragraph':'Equipment location'}]},
    ])
    requests=[]

    def handler(request):
        payload=json.loads(request.content);requests.append(payload)
        parts=payload['messages'][1]['content']
        assert isinstance(parts,list) and len(parts)==3
        content=json.loads(parts[0]['text']);region=content['visual_regions'][0]
        assert region['region_ref']=='V1' and 'region_id' not in region
        assert parts[1]['text'].endswith('region V1.')
        assert parts[2]['image_url']['url'].startswith('data:image/png;base64,')
        sent_png=base64.b64decode(parts[2]['image_url']['url'].split(',',1)[1])
        assert hashlib.sha256(sent_png).hexdigest()==region['image_sha256']
        data={
            'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE','missing_facts':[],'requests':[],
            'answer':{
                'claims':[{'text':'The pump is shown near Grid A-2.','citations':[{
                    'type':'IMAGE_REGION','region_ref':region['region_ref'],
                    'observation':'The selected plan page shows Pump P-101 near Grid A-2.',
                    'needs_review':True,
                }]}],
                'calculations':[],
                'coverage':[{'part_ref':'P1','claim_indexes':[0],
                             'calculation_indexes':[]}],
            },
        }
        return httpx.Response(200,json={
            'id':'qa-v3-multimodal-1','choices':[{'finish_reason':'stop','message':{
                'content':json.dumps(data)}}],
            'usage':{'prompt_tokens':130,'completion_tokens':45},
        })

    settings=Settings(
        tmp_path/'qa-v3-multimodal',provider='custom-0123456789abcdef',
        api_base_url='https://models.example/v1',api_key='synthetic-valid-key',
        cheap_model='gpt-5.6',vision_enabled=True,vision_model='gpt-5.6',live_enabled=True)
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))
    service=ProjectEvidenceLoop(db,gateway,client.app.state.uploads)

    result=service.ask(run,'Where is Pump P-101 shown?')
    recovered=service.ask(run,'Where  is Pump P-101 shown?')

    assert result['status']=='ANSWERED' and result['answer_basis']=='MODEL_QA_V3_MULTIMODAL_EVIDENCE_LOOP'
    assert requests[0]['model']=='gpt-5.6' and len(result['visual_regions'])==1
    assert len(result['visual_regions'][0]['image_sha256'])==64
    citation=result['claims'][0]['citations'][0]
    assert citation['image_sha256']==result['visual_regions'][0]['image_sha256']
    assert citation['needs_review'] is True and result['model_call_count']==1
    receipt=result['execution_receipts'][0]
    assert receipt['model']=='gpt-5.6' and receipt['evidence_count']>=1
    assert receipt['image_bytes']>0 and receipt['visual_inputs']==[{
        'region_id':result['visual_regions'][0]['region_id'],
        'image_sha256':result['visual_regions'][0]['image_sha256'],
        'image_bytes':receipt['image_bytes'],
    }]
    assert recovered['answer']==result['answer'] and recovered['model_call_count']==0
    assert len(requests)==1
    calls=db.all("SELECT task_key,state FROM model_calls WHERE task_key LIKE 'answer-v3-r%'")
    assert len(calls)==1 and calls[0]['state']=='SETTLED'
    gateway.close()


def test_v8_multiround_visual_audit_keeps_prior_images_out_of_final_claims(client,project,tmp_path,monkeypatch):
    # Exercise the same image-budget replacement with just two real pages.
    monkeypatch.setattr('app.evidence_loop.MAX_VISUAL_PAGES',1)
    first=io.BytesIO();Image.new('RGB',(80,60),'red').save(first,format='PNG')
    second=io.BytesIO();Image.new('RGB',(80,60),'blue').save(second,format='PNG')
    db,run,_=_qa_fixture(client,project,[
        {'name':'alpha.png','date':'2025-01-01','revision':'1','raw':first.getvalue(),
         'fragments':[{'text':'Alpha pump marker is on this page.','section':'Alpha','paragraph':'1'}]},
        {'name':'filler-a.png','date':'2025-01-01','revision':'1','raw':b'filler-a','fragments':[{'text':'Alpha pump marker supporting note A.','section':'Filler','paragraph':'1'}]},
        {'name':'filler-b.png','date':'2025-01-01','revision':'1','raw':b'filler-b','fragments':[{'text':'Alpha pump marker supporting note B.','section':'Filler','paragraph':'1'}]},
        {'name':'filler-c.png','date':'2025-01-01','revision':'1','raw':b'filler-c','fragments':[{'text':'Alpha pump marker supporting note C.','section':'Filler','paragraph':'1'}]},
        {'name':'zeta-beta.png','date':'2025-01-01','revision':'1','raw':second.getvalue(),
         'fragments':[{'text':'BetaUnique marker is on this page.','section':'Beta','paragraph':'1'}]},
    ])
    payloads=[]
    def handler(request):
        payload=json.loads(request.content);payloads.append(payload)
        content=json.loads(payload['messages'][1]['content'][0]['text'])
        if content['round']==1:
            decision={'status':'NEED_EVIDENCE','reason_code':'MISSING_SOURCE_TEXT',
                'missing_facts':['beta marker'],'requests':[{'tool':'SEARCH_TEXT','query':'BetaUnique'}],
                'answer':_empty_answer()}
            return httpx.Response(200,json={'id':'visual-round-1','choices':[{'finish_reason':'stop',
                'message':{'content':json.dumps(decision)}}],'usage':{'prompt_tokens':10,'completion_tokens':5}})
        region=content['visual_regions'][0]
        decision={'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE','missing_facts':[],'requests':[],
            'answer':{'claims':[{'text':'The final image is the beta marker.','citations':[{'type':'IMAGE_REGION',
                'region_ref':region['region_ref'],'observation':'The selected beta page is visible.','needs_review':True}]}],
            'calculations':[],'coverage':[{'part_ref':'P1','claim_indexes':[0],'calculation_indexes':[]}]}}
        return httpx.Response(200,json={'id':'visual-round-2','choices':[{'finish_reason':'stop',
            'message':{'content':json.dumps(decision)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5}})
    settings=Settings(tmp_path/'v8-multivisual',provider='custom-0123456789abcdef',
        api_base_url='https://models.example/v1',api_key='synthetic-valid-key',cheap_model='gpt-5.6',
        vision_enabled=True,vision_model='gpt-5.6',live_enabled=True)
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))
    result=ProjectEvidenceLoop(db,gateway,client.app.state.uploads).ask(
        run,'Where is the Alpha pump marker?',selector_version='literal-page-selector-8')
    assert len(payloads)==2 and len(result['execution_receipts'])==2
    first_regions=result['execution_receipts'][0]['visual_inputs']
    final_regions=result['visual_regions'];audited=result['sent_visual_regions']
    assert len(first_regions)==1 and len(final_regions)>=1
    assert {item['region_id'] for item in first_regions}|{item['region_id'] for item in final_regions} <= {item['region_id'] for item in audited}
    final_citation=result['claims'][0]['citations'][0]
    assert final_citation['region_id'] in {item['region_id'] for item in final_regions}
    assert final_citation['region_id'] not in {item['region_id'] for item in first_regions}
    store=ReferenceResultStore(db)
    saved=store.save(run,result['question'],result,'custom-0123456789abcdef','gpt-5.6')
    assert saved['result']['sent_visual_regions']==audited
    stored=db.one('SELECT * FROM reference_results WHERE id=?',(saved['result_id'],))
    assert len(store.authenticate_saved_result(run,stored)[1])==2
    from app.project_qa_v2 import validate_answer_v2
    prior=next(item for item in audited if item['region_id']==first_regions[0]['region_id'])
    old_claim={'text':'An earlier source image is visible.','citations':[{
        'type':'IMAGE_REGION',**{key:prior[key] for key in ('region_id','document_id','page_number','bbox')},
        'observation':'An earlier source image is visible.','needs_review':True}]}
    with pytest.raises(ValueError,match='outside the supplied visual regions'):
        validate_answer_v2({'status':'ANSWERED','answer':old_claim['text'],'claims':[old_claim],
                           'missing':[],'calculations':[]},[],result['question'],final_regions)
    gateway.close()


def test_decision_contract_expands_one_base_with_multiple_percentages():
    quote='Liquidated damages are $2,000; punch list is 10 percent and closeout is 5 percent.'
    evidence=[{'evidence_id':'EV-1','raw_text':quote,'prompt_text':quote}]
    refs=[{'type':'TEXT','evidence_ref':'E1'}]
    decision={
        'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE','missing_facts':[],'requests':[],
        'answer':{'claims':[],'calculations':[{
            'operator':'PERCENT_OF','operands':['2,000','10','5'],'citations':refs,
        }], 'coverage':[{'part_ref':'P1','claim_indexes':[],
                         'calculation_indexes':[0]}]},
    }

    normalized=validate_evidence_decision(
        decision,evidence,'What are 10 percent and 5 percent of $2,000? Show the arithmetic.')

    answer=normalized['answer']
    assert answer['answer']=='2,000 × 10% = 200. 2,000 × 5% = 100.'
    assert [item['result'] for item in answer['calculations']]==['200','100']
    assert all(item['citations'][0]['evidence_id']=='EV-1'
               for item in answer['calculations'])


def test_v3_stops_without_second_model_call_when_request_finds_nothing(client,project,tmp_path):
    db,run,_=_qa_fixture(client,project,[
        {'name':'finish-spec.pdf','date':'2025-01-01','revision':'1',
         'fragments':[{'text':'The coating requirement is listed in the finish schedule.'}]},
    ])
    requests=[]

    def handler(request):
        requests.append(request)
        data={'status':'NEED_EVIDENCE','reason_code':'MISSING_SOURCE_TEXT',
              'missing_facts':['approved coating color'],'requests':[
                  {'tool':'SEARCH_TEXT','query':'unicorn ultraviolet coefficient'}],
              'answer':_empty_answer()}
        return httpx.Response(200,json={
            'id':'qa-v3-empty','choices':[{'finish_reason':'stop','message':{
                'content':json.dumps(data)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':30},
        })

    settings=Settings(tmp_path/'qa-v3-empty',provider='deepseek',api_key='synthetic-key',
                      live_enabled=True)
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))

    result=ProjectEvidenceLoop(db,gateway).ask(run,'What is the approved coating color?')

    assert result['status']=='CANNOT_ANSWER'
    assert result['answer_basis']=='QA_V3_NO_NEW_EVIDENCE'
    assert result['model_call_count']==1 and len(requests)==1
    assert result['decision_trace'][-1]['reason_code']=='NO_NEW_EVIDENCE'
    gateway.close()


def test_v3_never_exceeds_three_model_decisions(client,project):
    db,run,_=_qa_fixture(client,project,[
        {'name':'finish-spec.pdf','date':'2025-01-01','revision':'1','fragments':[
            {'text':'Initial coating topic.','section':'Section 09 90 00','paragraph':'1.1'},
            {'text':'BetaUniqueValue.','page':2,'section':'Section 09 91 00','paragraph':'2.1'},
            {'text':'GammaUniqueValue.','page':3,'section':'Section 09 92 00','paragraph':'3.1'},
        ]},
    ])
    decisions=[
        {'status':'NEED_EVIDENCE','reason_code':'MISSING_SOURCE_TEXT',
         'missing_facts':['second fact'],'requests':[{
             'tool':'SEARCH_TEXT','query':'BetaUniqueValue'}],'answer':_empty_answer()},
        {'status':'NEED_EVIDENCE','reason_code':'MISSING_SOURCE_TEXT',
         'missing_facts':['third fact'],'requests':[{
             'tool':'SEARCH_TEXT','query':'GammaUniqueValue'}],'answer':_empty_answer()},
        {'status':'NEED_EVIDENCE','reason_code':'MISSING_SOURCE_TEXT',
         'missing_facts':['fourth fact'],'requests':[{
             'tool':'SEARCH_TEXT','query':'Fourth marker delta'}],'answer':_empty_answer()},
    ]

    class FakeGateway:
        s=SimpleNamespace(provider='custom')
        def __init__(self):self.calls=0
        def evidence_decision_v3(self,*_args,**_kwargs):
            value=decisions[self.calls];self.calls+=1
            return ModelResult(value,None,False,'custom')

    gateway=FakeGateway()
    result=ProjectEvidenceLoop(db,gateway).ask(run,'What is the initial coating topic?')

    assert gateway.calls==3 and result['model_call_count']==3
    assert result['status']=='CANNOT_ANSWER' and result['answer_basis']=='QA_V3_ROUND_LIMIT'
