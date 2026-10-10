import json
from dataclasses import replace
from decimal import Decimal
import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.canonical import CanonicalGraph
from app.db import CallSafetyError,dumps
from app.evidence_bundle import build_evidence_bundle,effective_source_set
from app.gateway import Gateway
from app.project_qa_v2 import ProjectQAV2,validate_answer_v2
from app.settings import Settings
from scripts.benchmark_qa_v2_retrieval import benchmark_v2_database
from .conftest import upload


def _qa_fixture(client,project,documents):
    uploaded=[upload(client,project['id'],item['name'],item.get(
        'raw',('synthetic '+item['name']).encode())) for item in documents]
    run=client.app.state.runner.create(project['id'])
    db=client.app.state.db
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?",(run['id'],))
    base=json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    evidence_rows=[]
    for index,(document,item) in enumerate(zip(uploaded,documents),1):
        for part,fragment in enumerate(item['fragments'],1):
            evidence={
                **base,'evidence_id':f'EV-QA2-{index}-{part}','tenant_id':'local',
                'project_id':project['id'],'input_snapshot_id':run['snapshot_id'],
                'document_id':document['document_id'],'raw_text':fragment['text'],
                'locator':{
                    **base['locator'],'page_number':fragment.get('page',1),
                    'sheet':fragment.get('sheet'),
                    'section':fragment.get('section','Section 03 30 00 > Part 2'),
                    'paragraph':fragment.get('paragraph','2.3.6'),
                    'bbox':fragment.get('bbox',[10.0,20.0+part*50,300.0,60.0+part*50]),
                    'coordinate_system':fragment.get('coordinate_system','pdf-points'),
                },
                'internal_revision_date':item.get('date'),
                'revision_label':item.get('revision'),
                'extraction_method':fragment.get('method','TEXT_LAYER'),
                'content_basis':fragment.get('content_basis','SOURCE_TEXT'),
                'text_map':fragment.get('text_map',[]),
                'parser_version':'synthetic-qa-v2',
            }
            evidence_rows.append((run['id']+':'+evidence['evidence_id'],run['id'],project['id'],
                                  document['document_id'],json.dumps(evidence),'EXTRACTED',None,''))
        db.execute('INSERT INTO document_results VALUES(?,?,?,?)',
                   (run['id'],document['document_id'],'SUCCESS',json.dumps(
                       {'status':'SUCCESS','pages':[{'page':1,'status':'SUCCESS'}],
                        'warnings':[],'parser_version':'synthetic-qa-v2'})))
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',evidence_rows)
    run=client.app.state.runner.get(run['id'])
    CanonicalGraph(db).rebuild_run(run)
    return db,run,uploaded


def test_effective_sources_select_newest_explicit_base_and_keep_delta(client,project):
    db,run,uploaded=_qa_fixture(client,project,[
        {'name':'project-spec-rev-A.pdf','date':'2025-01-01','revision':'A',
         'fragments':[{'text':'Old dosage was one percent.'}]},
        {'name':'project-spec-rev-B.pdf','date':'2025-02-01','revision':'B',
         'fragments':[{'text':'The waterproofing admixture dosage is two percent.'}]},
        {'name':'RFI-0042-rev-1.pdf','date':'2025-03-01','revision':'1',
         'fragments':[{'text':'RFI 42 confirms the two percent dosage.',
                       'section':'RFI 42','paragraph':None}]},
    ])

    sources=effective_source_set(db,run)

    assert set(sources.document_ids)=={uploaded[1]['document_id'],uploaded[2]['document_id']}
    by_name={item.name:item for item in sources.documents}
    assert by_name['project-spec-rev-A.pdf'].reason=='SUPERSEDED_BY_EXPLICIT_ISSUE_DATE'
    assert by_name['project-spec-rev-B.pdf'].reason=='NEWEST_EXPLICIT_ISSUE_DATE'
    assert by_name['RFI-0042-rev-1.pdf'].reason=='DELTA_OVERLAY'
    assert sources.conflicts==()


def test_missing_version_metadata_keeps_competing_sources_and_flags_conflict(client,project):
    db,run,uploaded=_qa_fixture(client,project,[
        {'name':'A101-rev-A.pdf','date':'2025-01-01','revision':'A',
         'fragments':[{'text':'First drawing note.','sheet':'A101'}]},
        {'name':'A101-rev-B.pdf','date':None,'revision':None,
         'fragments':[{'text':'Second drawing note.','sheet':'A101'}]},
    ])

    sources=effective_source_set(db,run)

    assert set(sources.document_ids)=={item['document_id'] for item in uploaded}
    assert all(item.reason=='KEPT_AMBIGUOUS' for item in sources.documents)
    assert any('missing explicit version metadata' in conflict for conflict in sources.conflicts)


def test_revision_number_selects_newest_when_issue_dates_are_absent(client,project):
    db,run,uploaded=_qa_fixture(client,project,[
        {'name':'schedule-rev-1.pdf','date':None,'revision':'1',
         'fragments':[{'text':'Earlier equipment schedule.'}]},
        {'name':'schedule-rev-2.pdf','date':None,'revision':'2',
         'fragments':[{'text':'Current equipment schedule.'}]},
    ])

    sources=effective_source_set(db,run)

    assert sources.document_ids==(uploaded[1]['document_id'],)
    assert next(item for item in sources.documents if item.selected).reason=='NEWEST_EXPLICIT_REVISION'


def test_bundle_uses_effective_source_and_exact_source_citations(client,project):
    db,run,uploaded=_qa_fixture(client,project,[
        {'name':'project-spec-rev-A.pdf','date':'2025-01-01','revision':'A',
         'fragments':[{'text':'Old dosage was one percent.'}]},
        {'name':'project-spec-rev-B.pdf','date':'2025-02-01','revision':'B',
         'fragments':[{'text':'The waterproofing admixture dosage is two percent.'},
                      {'text':'Invented visual statement.',
                       'method':'VISION','content_basis':'MODEL_VISION_OUTPUT'}]},
    ])

    bundle=build_evidence_bundle(db,run,'What dosage does Section 03 30 00 require?')

    assert bundle.blocks and bundle.byte_count<=42_000
    assert {block.document_id for block in bundle.blocks}=={uploaded[1]['document_id']}
    text='\n'.join(block.text for block in bundle.blocks)
    assert 'two percent' in text and 'Old dosage' not in text and 'Invented visual' not in text
    citations=[citation for block in bundle.blocks for citation in block.citations]
    assert citations and all(citation.evidence_id.startswith('EV-QA2-2-') for citation in citations)
    assert all(citation.bbox and citation.coordinate_system=='pdf-points' for citation in citations)


def test_table_match_expands_the_structural_table_block(client,project):
    db,run,_=_qa_fixture(client,project,[
        {'name':'schedule-rev-1.pdf','date':'2025-01-01','revision':'1','fragments':[
            {'text':'Category | Count','paragraph':'Table 2 row 1'},
            {'text':'Structural | 13','paragraph':'Table 2 row 2'},
        ]},
    ])

    bundle=build_evidence_bundle(db,run,'What is the structural count?')

    assert len(bundle.blocks)==1
    assert bundle.blocks[0].path.endswith('Table 2')
    assert 'Category | Count' in bundle.blocks[0].text
    assert 'Structural | 13' in bundle.blocks[0].text
    assert len(bundle.blocks[0].citations)==2


def test_preview_endpoint_is_independent_and_makes_no_model_call(client,project,monkeypatch):
    _,run,_=_qa_fixture(client,project,[
        {'name':'project-spec-rev-A.pdf','date':'2025-01-01','revision':'A',
         'fragments':[{'text':'The waterproofing admixture dosage is two percent.'}]},
    ])
    monkeypatch.setattr(client.app.state.gateway,'answer',
                        lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('model called')))

    response=client.post(f"/api/projects/{project['id']}/questions-v2/preview",
                         json={'run_id':run['id'],'question':'What is the waterproofing dosage?'})

    assert response.status_code==200,response.text
    body=response.json()
    assert body['qa_version']=='2' and body['status']=='RETRIEVAL_READY'
    assert body['answer_basis']=='QA_V2_EVIDENCE_BUNDLE' and body['model_called'] is False
    assert body['evidence_bundle']['blocks']
    assert body['model_input']['input_version']=='qa-v2-model-input-2'
    assert body['model_input']['request_fits'] is True
    assert body['model_input']['request_upper_bound_bytes']<=body['model_input']['request_limit_bytes']


def test_claim_contract_binds_answer_to_exact_quotes_and_numbers():
    evidence=[{'evidence_id':'EV-1','raw_text':'The specified dosage is 2 percent.'}]
    answer={'status':'ANSWERED','answer':'The specified dosage is 2 percent.',
            'claims':[{'text':'The specified dosage is 2 percent.','citations':[
                {'type':'TEXT','evidence_id':'EV-1','quote':'The specified dosage is 2 percent.'}]}],
            'missing':[],'calculations':[]}

    validate_answer_v2(answer,evidence,'What dosage is specified?')
    wrong=json.loads(json.dumps(answer));wrong['claims'][0]['text']='The specified dosage is 3 percent.'
    wrong['answer']=wrong['claims'][0]['text']
    with pytest.raises(ValueError,match='number absent'):
        validate_answer_v2(wrong,evidence,'What dosage is specified?')
    outside=json.loads(json.dumps(answer));outside['claims'][0]['citations'][0]['quote']='dosage is 3 percent'
    with pytest.raises(ValueError,match='not exact'):
        validate_answer_v2(outside,evidence,'What dosage is specified?')


def test_claim_contract_rejects_explicit_wrong_building_without_inference():
    evidence=[
        {'evidence_id':'EV-I','raw_text':'BUILDING I: Type V-A construction.'},
        {'evidence_id':'EV-G','raw_text':'BUILDING G: Type V-A construction.'},
    ]
    correct={'status':'ANSWERED','answer':'The construction type is V-A.',
             'claims':[{'text':'The construction type is V-A.','citations':[
                 {'type':'TEXT','evidence_id':'EV-I','quote':'BUILDING I: Type V-A construction.'}]}],
             'missing':[],'calculations':[]}
    validate_answer_v2(correct,evidence,'What is the construction type for Building I?')

    wrong=json.loads(json.dumps(correct))
    wrong['answer']=wrong['claims'][0]['text']='Building G is Type V-A.'
    wrong['claims'][0]['citations']=[
        {'type':'TEXT','evidence_id':'EV-G','quote':'BUILDING G: Type V-A construction.'}]
    with pytest.raises(ValueError,match='explicit entity conflicts'):
        validate_answer_v2(wrong,evidence,'What is the construction type for Building I?')

    wrong_without_named_answer=json.loads(json.dumps(correct))
    wrong_without_named_answer['claims'][0]['citations']=[
        {'type':'TEXT','evidence_id':'EV-G','quote':'BUILDING G: Type V-A construction.'}]
    with pytest.raises(ValueError,match='different question entity'):
        validate_answer_v2(
            wrong_without_named_answer,evidence,'What is the construction type for Building I?')

    # Generic prose is deliberately outside this narrow guard; no semantic entity is inferred.
    validate_answer_v2(correct,evidence,'What is the construction type?')


def _one_claim(text,quote,evidence_id='EV-RELATION'):
    citation={'type':'TEXT','evidence_id':evidence_id,'quote':quote}
    return {'status':'ANSWERED','answer':text,'claims':[{'text':text,'citations':[citation]}],
            'missing':[],'calculations':[]}


def test_claim_contract_binds_object_unit_and_condition_in_one_source_clause():
    object_quote='Building A: 100 psi; Building B: 200 psi.'
    object_evidence=[{'evidence_id':'EV-RELATION','raw_text':object_quote}]
    validate_answer_v2(_one_claim('Building A requires 100 psi.',object_quote),object_evidence,
                       'What pressure applies to Building A?')
    with pytest.raises(ValueError,match='object, number and unit'):
        validate_answer_v2(_one_claim('Building A requires 200 psi.',object_quote),object_evidence,
                           'What pressure applies to Building A?')

    unit_quote='The cable length is 10 feet.'
    unit_evidence=[{'evidence_id':'EV-RELATION','raw_text':unit_quote}]
    validate_answer_v2(_one_claim('The cable length is 10 ft.',unit_quote),unit_evidence,
                       'What is the cable length?')
    with pytest.raises(ValueError,match='object, number and unit'):
        validate_answer_v2(_one_claim('The cable length is 10 meters.',unit_quote),unit_evidence,
                           'What is the cable length?')

    condition_quote='Use 2 anchors when the support is adequate.'
    condition_evidence=[{'evidence_id':'EV-RELATION','raw_text':condition_quote}]
    validate_answer_v2(_one_claim('Use 2 anchors when the support is adequate.',condition_quote),
                       condition_evidence,'How many anchors are required?')
    for wrong in ('Use 2 anchors under all conditions.','Use 2 anchors.'):
        with pytest.raises(ValueError,match='removes a cited source condition'):
            validate_answer_v2(_one_claim(wrong,condition_quote),
                               condition_evidence,'How many anchors are required?')


@pytest.mark.parametrize(('claim','expected_status','ledger_state'),[
    ('Building A requires 200 psi.',400,'SETTLED_ERROR'),
    ('Building A requires 100 psi.',200,'SETTLED'),
])
def test_questions_v2_endpoint_enforces_object_value_binding(
        client,project,claim,expected_status,ledger_state):
    quote='Building A: 100 psi; Building B: 200 psi.'
    db,run,_=_qa_fixture(client,project,[
        {'name':'pressure-schedule.pdf','date':'2025-01-01','revision':'A',
         'fragments':[{'text':quote}]},
    ])
    response_data=_one_claim(claim,quote,'EV-QA2-1-1')
    gateway=client.app.state.gateway
    gateway.s=replace(gateway.s,provider='deepseek',cheap_model='deepseek-flash',
                      live_enabled=True,api_key='synthetic-key',vision_enabled=False)
    gateway.client.close()
    gateway.client=httpx.Client(transport=httpx.MockTransport(lambda _request:httpx.Response(
        200,json={'id':'qa-v2-binding','choices':[{'finish_reason':'stop','message':{
            'content':json.dumps(response_data)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':25,'total_tokens':125}})))

    response=client.post(f"/api/projects/{project['id']}/questions-v2",json={
        'run_id':run['id'],'question':'What pressure applies to Building A?'})
    assert response.status_code==expected_status,response.text
    if expected_status==200:
        assert response.json()['status']=='ANSWERED'
    else:
        assert 'failed its claim and evidence contract' in response.json()['detail']
    call=db.one("SELECT state FROM model_calls WHERE task_key LIKE 'answer-v2:%'")
    assert call['state']==ledger_state


def test_claim_contract_recomputes_explicit_decimal_calculation():
    evidence=[{'evidence_id':'EV-1','raw_text':'Area A: 13 units. Area B: 7 units.'}]
    answer={'status':'ANSWERED','answer':'The requested total is 20 units.',
            'claims':[{'text':'The requested total is 20 units.','citations':[
                {'type':'TEXT','evidence_id':'EV-1','quote':'Area A: 13 units. Area B: 7 units.'}]}],
            'missing':[],'calculations':[{
                'operator':'ADD','operands':['13','7'],'result':'20','citations':[
                    {'type':'TEXT','evidence_id':'EV-1','quote':'Area A: 13 units. Area B: 7 units.'}]}]}

    validate_answer_v2(answer,evidence,'What is the total for Area A and Area B?')
    answer['calculations'][0]['result']='21'
    with pytest.raises(ValueError,match='Decimal arithmetic'):
        validate_answer_v2(answer,evidence,'What is the total for Area A and Area B?')


def test_claim_contract_computes_explicit_percentage_from_cited_source_numbers():
    quote='Liquidated damages are $2000.00 per day; punch-list delay is 10.0 percent.'
    citation={'type':'TEXT','evidence_id':'EV-1','quote':quote}
    evidence=[{'evidence_id':'EV-1','raw_text':quote}]
    answer={
        'status':'ANSWERED','answer':'2000 × 10% = 200.',
        'claims':[{'text':'2000 × 10% = 200.','citations':[citation]}],
        'missing':[],'calculations':[{
            'operator':'PERCENT_OF','operands':['2,000','10'],'result':'200',
            'citations':[citation],
        }],
    }

    validate_answer_v2(answer,evidence,'What is 10 percent of $2,000? Show the arithmetic.')
    answer['calculations'][0]['operands'].append('10')
    with pytest.raises(ValueError,match='base and percentage'):
        validate_answer_v2(answer,evidence,'What is 10 percent of $2,000? Show the arithmetic.')


def test_live_v2_answer_uses_new_prompt_contract_and_recovers_without_second_call(
        client,project,tmp_path):
    db,run,_=_qa_fixture(client,project,[
        {'name':'project-spec-rev-A.pdf','date':'2025-01-01','revision':'A',
         'fragments':[{'text':'The waterproofing admixture dosage is 2 percent.'}]},
    ])
    response_data={'status':'ANSWERED','answer':'The waterproofing admixture dosage is 2 percent.',
                   'claims':[{'text':'The waterproofing admixture dosage is 2 percent.',
                              'citations':[{'type':'TEXT','evidence_id':'EV-QA2-1-1',
                                            'quote':'The waterproofing admixture dosage is 2 percent.'}]}],
                   'missing':[],'calculations':[]}
    requests=[]

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={
            'id':'qa-v2-test','choices':[{'finish_reason':'stop','message':{
                'content':json.dumps(response_data)}}],
            'usage':{'prompt_tokens':120,'completion_tokens':30},
        })

    settings=Settings(tmp_path/'qa-v2-live',provider='deepseek',api_key='synthetic-key',
                      live_enabled=True)
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))
    service=ProjectQAV2(db,gateway)

    first=service.ask(run,'What is the waterproofing admixture dosage?')
    recovered=service.ask(run,'What  is the waterproofing admixture dosage?')

    assert first['status']=='ANSWERED' and first['model_called'] is True
    assert first['claims'][0]['citations'][0]['page_number']==1
    assert recovered['answer']==first['answer'] and recovered['cached'] is True
    assert recovered['model_called'] is False and len(requests)==1
    assert 'waterproofing admixture' not in requests[0]['messages'][0]['content'].lower()
    assert 'waterproofing admixture' in requests[0]['messages'][1]['content'].lower()
    sent_evidence=json.loads(requests[0]['messages'][1]['content'])['evidence']
    assert set(sent_evidence[0])=={'evidence_id','text'}
    call=db.one("SELECT task_key,state FROM model_calls WHERE task_key LIKE 'answer-v2:%'")
    assert call['state']=='SETTLED'
    gateway.close()


def test_v2_large_pdf_bundle_drops_repeated_local_metadata_and_reaches_http(client,project,tmp_path):
    db,run,_=_qa_fixture(client,project,[
        {'name':'large-drawing-set.pdf','date':'2025-01-01','revision':'A',
         'fragments':[{'text':'Alpha source statement 000.'}]},
    ])
    quote='Alpha source statement '+('X'*200)
    evidence=[{
        'evidence_id':f'EV-LARGE-{index:03d}','document_id':'DOC-LARGE',
        'file_name':'large-drawing-set-with-a-repeated-long-name-rev-A.pdf',
        'raw_text':quote,'prompt_text':quote,
        'locator':{'page_number':index+1,'path':'Sheet A101 > repeated detail path',
                   'bbox':[10.0,20.0,300.0,60.0],'coordinate_system':'pdf-points'},
    } for index in range(176)]
    response_data={'status':'ANSWERED','answer':'Alpha is present.',
                   'claims':[{'text':'Alpha is present.','citations':[{
                       'type':'TEXT','evidence_id':'EV-LARGE-000','quote':quote}]}],
                   'missing':[],'calculations':[]}
    requests=[]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={
            'id':'qa-v2-large','choices':[{'finish_reason':'stop','message':{
                'content':json.dumps(response_data)}}],
            'usage':{'prompt_tokens':500,'completion_tokens':25,'total_tokens':525}})
    settings=Settings(tmp_path/'qa-v2-large',provider='deepseek',api_key='synthetic-key',
                      live_enabled=True)
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))
    legacy_content={
        'question':'Is Alpha present?','effective_source_conflicts':[],
        'required_missing':[],'visual_regions':[],
        'evidence':[{'evidence_id':item['evidence_id'],'file_name':item['file_name'],
                     'locator':item['locator'],'text':item['prompt_text']} for item in evidence],
    }
    legacy_upper=(len((gateway.answer_v2_prompt+'\nJSON Schema:\n'+
                       dumps(gateway.answer_v2_schema)).encode('utf-8'))+
                  len(dumps(legacy_content).encode('utf-8'))+256)
    summary=gateway.answer_v2_input_summary('Is Alpha present?',evidence,[])

    result=gateway.answer_v2(run,'Is Alpha present?',evidence,[])

    assert legacy_upper>64_000
    assert summary['request_fits'] is True and summary['request_upper_bound_bytes']<64_000
    assert result.data['status']=='ANSWERED' and len(requests)==1
    sent=json.loads(requests[0]['messages'][1]['content'])['evidence']
    assert len(sent)==176 and all(set(item)=={'evidence_id','text'} for item in sent)
    gateway.close()


def test_v2_answer_endpoint_in_mock_mode_returns_bundle_without_model(client,project):
    _,run,_=_qa_fixture(client,project,[
        {'name':'project-spec-rev-A.pdf','date':'2025-01-01','revision':'A',
         'fragments':[{'text':'The waterproofing admixture dosage is 2 percent.'}]},
    ])

    response=client.post(f"/api/projects/{project['id']}/questions-v2",
                         json={'run_id':run['id'],'question':'What is the waterproofing dosage?'})

    assert response.status_code==200,response.text
    body=response.json()
    assert body['status']=='MODEL_DISABLED' and body['model_called'] is False
    assert body['answer_basis']=='QA_V2_EVIDENCE_BUNDLE'
    assert body['evidence_bundle']['blocks']


def test_visual_question_sends_one_overview_and_one_local_crop_with_reviewable_region(
        client,project,tmp_path):
    image=Image.new('RGB',(120,100),'white');buffer=io.BytesIO();image.save(buffer,format='PNG')
    db,run,_=_qa_fixture(client,project,[
        {'name':'pump-plan-rev-A.png','date':'2025-01-01','revision':'A','raw':buffer.getvalue(),
         'fragments':[{'text':'Pump symbol is shown near Grid A-2.','method':'OCR',
                       'bbox':[20.0,20.0,80.0,70.0],
                       'coordinate_system':'image-pixels-top-left-exif-normalized'}]},
    ])
    requests=[]

    def handler(request):
        payload=json.loads(request.content);requests.append(payload)
        parts=payload['messages'][1]['content']
        content=json.loads(parts[0]['text']);region=content['visual_regions'][0]
        answer={'status':'ANSWERED','answer':'The pump is shown near Grid A-2.',
                'claims':[{'text':'The pump is shown near Grid A-2.','citations':[{
                    'type':'IMAGE_REGION','region_id':region['region_id'],
                    'document_id':region['document_id'],'page_number':region['page_number'],
                    'bbox':region['bbox'],'observation':'The pump symbol appears near Grid A-2.',
                    'needs_review':True}]}],
                'missing':[],'calculations':[]}
        return httpx.Response(200,json={
            'id':'qa-v2-vision-test','choices':[{'finish_reason':'stop','message':{
                'content':json.dumps(answer)}}],
            'usage':{'prompt_tokens':300,'completion_tokens':50},
        })

    settings=Settings(tmp_path/'qa-v2-vision',provider='deepseek',api_key='synthetic-key',
                      live_enabled=True,vision_enabled=True)
    gateway=Gateway(settings,db,httpx.Client(transport=httpx.MockTransport(handler)))
    service=ProjectQAV2(db,gateway,client.app.state.uploads)

    result=service.ask(run,'Where is the pump shown on the plan?')

    assert result['status']=='ANSWERED'
    assert result['answer_basis']=='MODEL_QA_V2_MULTIMODAL_EVIDENCE'
    assert result['claims'][0]['citations'][0]['needs_review'] is True
    assert len(result['visual_regions'])==1 and len(requests)==1
    parts=requests[0]['messages'][1]['content']
    assert requests[0]['model']==settings.vision_model
    assert [item['type'] for item in parts].count('image_url')==2
    assert all(item['image_url']['url'].startswith('data:image/png;base64,')
               for item in parts if item['type']=='image_url')
    call=db.one("SELECT task_key,state FROM model_calls WHERE task_key LIKE 'answer-v2-vision:%'")
    assert call['state']=='SETTLED'
    gateway.close()


def test_paid_question_safety_allows_only_legacy_and_v2_answer_families(client,project):
    db,run,_=_qa_fixture(client,project,[
        {'name':'spec.pdf','date':'2025-01-01','revision':'1',
         'fragments':[{'text':'Synthetic source.'}]},
    ])

    with pytest.raises(CallSafetyError,match='not bound'):
        db.reserve(project['id'],run['id'],'answer-v3:'+'0'*64,Decimal('0'),'model',
                   '0'*64,Decimal('0'),Decimal('0'),interactive_question=True)


def test_qa_v2_retrieval_benchmark_is_read_only_and_scores_structural_table(client,project):
    db,run,_=_qa_fixture(client,project,[
        {'name':'drawing-index-rev-1.pdf','date':'2025-01-01','revision':'1','fragments':[
            {'text':'General | 7','paragraph':'Table 1 row 1'},
            {'text':'Architectural | 48','paragraph':'Table 1 row 2'},
            {'text':'Structural | 13','paragraph':'Table 1 row 3'},
            {'text':'Mechanical | 20','paragraph':'Table 1 row 4'},
            {'text':'Fire Protection | 8','paragraph':'Table 1 row 5'},
            {'text':'Plumbing | 19','paragraph':'Table 1 row 6'},
        ]},
    ])
    with db.connect() as connection:connection.execute('PRAGMA wal_checkpoint(FULL)')
    before=db.path.stat()

    result=benchmark_v2_database(
        db.path,[{'id':'S5','question':(
            'How many Volume I drawing sheets are listed for each discipline: General, Architectural, '
            'Structural, Mechanical, Fire Protection, and Plumbing?')}],run['id'])

    after=db.path.stat()
    assert result['atoms_found']==6 and result['questions_complete']==1
    assert result['model_calls_before']==result['model_calls_after'] and result['paid_calls_made']==0
    assert (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns)
