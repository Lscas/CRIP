"""Full source transport and explicit-scope isolation; no live provider calls."""
import json

import httpx
import pytest

from app.db import Database
from app.evidence_loop import ProjectEvidenceLoop,validate_evidence_decision
from app.gateway import Gateway,InvalidModelOutput,ProviderPaused
from app.page_selector import COMPLETE_SELECTOR_VERSION,select_pages
from app.reference_results import MAX_RESULT_BYTES,ReferenceResultStore
from app.settings import Settings
from .test_page_selector import _raw_run,_spatial_text
from .test_project_qa_v2 import _qa_fixture
from .test_reference_cases import _evaluation_item,_result
from .test_reference_results import _saved_run
from app.settings import ROOT


def _no_answer():
    return {'status':'CANNOT_ANSWER','reason_code':'UNSUPPORTED_TASK',
            'missing_facts':['Project-specific supporting information is required.'],
            'requests':[],'answer':{'claims':[],'calculations':[],'coverage':[]}}


def test_v8_migration_preserves_frozen_v3_to_v7_evaluations_and_case_links(client,project):
    db,run,_,_=_saved_run(client,project,'REFERENCE_QA')
    question='What approved color applies to Finish key PT9?'
    result_id=_result(client.app.state.reference_results,run,question)
    for version in range(3,8):
        evaluation_id,item_id=_evaluation_item(db,project,run,question,result_id=result_id)
        db.execute('UPDATE reference_evaluations SET selector_version=? WHERE id=?',
                   (f'literal-page-selector-{version}',evaluation_id))
    case=client.app.state.reference_cases.create(project['id'],run['id'],question,
        evaluation_id=evaluation_id,question_id=item_id)
    tables=('reference_evaluations','reference_evaluation_items','reference_results',
            'reference_result_citations','reference_cases','reference_case_events')
    before={table:db.all(f'SELECT * FROM {table} ORDER BY rowid') for table in tables}
    with db.connect() as connection:
        # Recreate the real v7 CHECK in this synthetic database before upgrading.
        connection.executescript((ROOT/'migrations/020_reference_selector_v7.sql').read_text(encoding='utf-8'))
        connection.execute('DELETE FROM schema_migrations WHERE version=21');connection.commit()
    upgraded=Database(db.path)
    assert before=={table:upgraded.all(f'SELECT * FROM {table} ORDER BY rowid') for table in tables}
    assert upgraded.all('PRAGMA foreign_key_check')==[]
    assert upgraded.one('SELECT 1 FROM schema_migrations WHERE version=21') is not None
    assert client.app.state.reference_cases.get(case['case_id'])==case
    upgraded.execute('UPDATE reference_evaluations SET selector_version=? WHERE id=?',
                     (COMPLETE_SELECTOR_VERSION,evaluation_id))


def test_complete_selected_page_keeps_all_rows_layout_and_explicit_continuations(client,project):
    long_text='Full source remains available. '*2500+'END OF COMPLETE SOURCE'
    text,mapping=_spatial_text([(i*15,[('LEFTCOLUMN',10,80),('RIGHTCOLUMN',400,480)])
                               for i in range(700)])
    db,run,_=_raw_run(client,project,[
        {'page':1,'sheet':'A5.01','text':long_text},
        {'page':1,'sheet':'A5.01','text':text,'text_map':mapping},
        *[{'page':i,'sheet':'A5.01','text':f'Continuation page {i}'} for i in range(2,7)],
        {'page':8,'sheet':'A9.01','text':'Different scope without a matching term.'},
    ])
    legacy=select_pages(db,run,'Sheet A5.01')
    current=select_pages(db,run,'Sheet A5.01',selector_version=COMPLETE_SELECTOR_VERSION)
    assert len(legacy.selected_pages)==1 and legacy.byte_count==48_000
    assert {page.page_number for page in current.selected_pages}==set(range(1,7))
    assert current.byte_count>64_000 and current.max_bytes is None
    assert current.evidence_rows[0]['raw_text']==long_text
    assert current.evidence_rows[0]['prompt_text'].endswith('END OF COMPLETE SOURCE')
    assert len(current.evidence_rows[0]['layout_lines'].encode())>6_000
    assert current.public()['expanded_page_count']==2
    assert current.public()['source_text_clipped'] is False


@pytest.mark.parametrize('row_count',[310,8500])
def test_complete_context_transports_over_64kb_and_retains_receipt_and_cache(client,project,tmp_path,row_count):
    fragments=[{'page':1,'sheet':'A5.01','text':f'Original clause {index}. '+('unchanged '*35)}
               for index in range(row_count)]
    db,run,_=_raw_run(client,project,fragments)
    received=[]
    def handler(request):
        payload=json.loads(request.content);content=json.loads(payload['messages'][1]['content'])
        received.append(content)
        assert len(request.content)>64_000
        assert [item['text'] for item in content['evidence']]==[item['text'] for item in fragments]
        return httpx.Response(200,json={'id':'complete-context-synthetic',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(_no_answer())}}],
            'usage':{'prompt_tokens':30000,'completion_tokens':80}})
    gateway=Gateway(Settings(tmp_path/'complete',provider='deepseek',api_key='synthetic-key',
                             cheap_model='deepseek-flash',live_enabled=True),db,
                    httpx.Client(transport=httpx.MockTransport(handler)))
    service=ProjectEvidenceLoop(db,gateway)
    result=service.ask(run,'On Sheet A5.01, what information is available?')
    replay=service.ask(run,'On Sheet A5.01, what information is available?')
    receipt=result['execution_receipts'][0]
    assert receipt['receipt_version']=='reference-model-input-receipt-2'
    assert receipt['evidence_count']==row_count and receipt['source_text_bytes']>64_000
    assert receipt['source_text_clipped'] is False and len(receipt['ordered_evidence_manifest_sha256'])==64
    assert len(received)==1 and replay['model_call_count']==0
    if row_count==8500:assert len(json.dumps(result).encode())>MAX_RESULT_BYTES
    saved=ReferenceResultStore(db).save(run,result['question'],result,'deepseek','deepseek-flash')
    assert saved['result']['execution_receipts'][0]==receipt
    gateway.close()


def test_complete_continuation_requires_every_explicit_locator_dimension(client,project):
    db,run,_=_raw_run(client,project,[
        *[{'page':i,'sheet':'A1.01','section':'Section 01100','paragraph':'1.04',
           'text':f'Correct scope continuation {i}.'} for i in range(1,7)],
        *[{'page':i,'sheet':f'A{i}.02','section':'Section 01100','paragraph':'1.04',
           'text':'Another sheet with the same section and paragraph.'} for i in range(7,11)],
        {'page':11,'sheet':'A1.01','section':'Section 02200','paragraph':'1.04',
         'text':'Another section on the requested sheet.'},
    ])
    selected=select_pages(db,run,'Sheet A1.01 Section 01100 paragraph 1.04',
                          selector_version=COMPLETE_SELECTOR_VERSION)
    assert {page.page_number for page in selected.selected_pages}==set(range(1,7))
    assert selected.public()['expanded_page_count']==2


def test_complete_context_expands_separate_drawing_and_spec_scopes_without_parent_clause_leak(client,project):
    db,run,_=_qa_fixture(client,project,[
        {'name':'scope-drawing.pdf','fragments':[
            *[{'page':i,'sheet':'A1.01','section':None,'paragraph':None,
               'text':f'Named drawing continuation {i}.'} for i in range(1,7)],
            {'page':7,'sheet':'A9.99','section':None,'paragraph':None,'text':'Other drawing.'}]},
        {'name':'scope-spec.pdf','fragments':[
            *[{'page':i,'section':'Section 01100','paragraph':'1.04',
               'text':f'Named clause continuation {i}.'} for i in range(1,7)],
            {'page':7,'section':'Section 01100','paragraph':None,
             'text':'Parent section without the requested paragraph.'},
            {'page':8,'section':'Section 01100','paragraph':'1.05','text':'Other paragraph.'}]},
    ])
    selected=select_pages(db,run,'Does Sheet A1.01 satisfy Section 01100 paragraph 1.04?',
                          selector_version=COMPLETE_SELECTOR_VERSION)
    assert len(selected.selected_pages)==12
    assert {page.page_number for page in selected.selected_pages}==set(range(1,7))
    assert selected.public()['expanded_page_count']==8
    unnamed=select_pages(db,run,'Named continuation',selector_version=COMPLETE_SELECTOR_VERSION)
    assert len(unnamed.selected_pages)==4


def test_complete_aliases_are_not_limited_to_999_and_scope_is_still_checked():
    rows=[{'evidence_id':f'EV-{i}','raw_text':'Approved coating is blue.'} for i in range(1,1002)]
    decision={'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE','missing_facts':[],'requests':[],
              'answer':{'claims':[{'text':'Approved coating is blue.','citations':[
                  {'type':'TEXT','evidence_ref':'E1001'}]}],'calculations':[],
                  'coverage':[{'part_ref':'P1','claim_indexes':[0],'calculation_indexes':[]}]}}
    result=validate_evidence_decision(decision,rows,'What coating is approved?',complete_context=True)
    assert result['answer']['claims'][0]['citations'][0]['evidence_id']=='EV-1001'
    decision['answer']['claims'][0]['citations'][0]['evidence_ref']='E1002'
    with pytest.raises(ValueError,match='outside the evidence bundle'):
        validate_evidence_decision(decision,rows,'What coating is approved?',complete_context=True)


def test_complete_context_never_reuses_legacy_request_identity(client,project,tmp_path):
    db,run,_=_raw_run(client,project,[{'page':1,'text':'Approved coating is blue.'}])
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'id':'synthetic-mode-test',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(_no_answer())}}],
            'usage':{'prompt_tokens':100,'completion_tokens':30}})
    gateway=Gateway(Settings(tmp_path/'identity',provider='deepseek',api_key='synthetic-key',
                             live_enabled=True),db,httpx.Client(transport=httpx.MockTransport(handler)))
    rows=[{'evidence_id':'EV-PAGE-1','raw_text':'Approved coating is blue.'}]
    old=gateway.evidence_decision_v3(run,'What coating is approved?',rows,[],[],0)
    new=gateway.evidence_decision_v3(run,'What coating is approved?',rows,[],[],0,
                                   selector_version=COMPLETE_SELECTOR_VERSION)
    old_again=gateway.evidence_decision_v3(run,'What coating is approved?',rows,[],[],0)
    assert len(requests)==2 and old_again.cached
    assert old.request_id!=new.request_id and old.request_id==old_again.request_id
    assert old.execution_receipt['receipt_version'].endswith('-1')
    assert new.execution_receipt['receipt_version'].endswith('-2')
    with pytest.raises(InvalidModelOutput,match='clipped'):
        gateway.evidence_decision_v3(run,'What coating is approved?',
            [{**rows[0],'prompt_text':'Approved'}],[],[],0,selector_version=COMPLETE_SELECTOR_VERSION)
    assert len(requests)==2
    gateway.close()


def test_complete_continuations_preserve_document_paragraph_and_building_scope(client,project):
    db,run,uploaded=_qa_fixture(client,project,[
        {'name':'alpha-spec.pdf','fragments':[
            *[{'page':i,'section':'Section 01100','paragraph':'1.04',
               'text':f'Building G construction sequence continuation {i}.'} for i in range(1,7)],
            {'page':8,'section':'Section 01100','paragraph':'1.05',
             'text':'Building G unrelated construction sequence exception.'},
            {'page':9,'section':'Section 01100','paragraph':'1.04',
             'text':'Building I construction sequence is different.'}]},
        {'name':'unrelated-document.pdf','fragments':[
            {'page':10,'section':'Section 09900','paragraph':'1.04',
             'text':'Completely unrelated material.'}]},
    ])
    selection=select_pages(db,run,'For Building G, Section 01100 paragraph 1.04 construction sequence?',
                           selector_version=COMPLETE_SELECTOR_VERSION)
    assert {page.page_number for page in selection.selected_pages}==set(range(1,7))
    assert {page.document_id for page in selection.selected_pages}=={uploaded[0]['document_id']}
    assert selection.excluded_entity_pages==1
    assert selection.public()['excluded_by_page_limit']>=1
    assert selection.public()['max_pages'] is None
    assert selection.public()['anchor_page_count']==4


@pytest.mark.parametrize('provider_code,expected_class',[
    ('context_length_exceeded','MODEL_CONTEXT_LIMIT'),('invalid_request_error','REQUEST_REJECTED')])
def test_provider_context_rejection_is_classified_without_truncation_or_retry(
        client,project,tmp_path,provider_code,expected_class):
    db,run,_=_raw_run(client,project,[{'text':'Source context retained in full.'}])
    sent=[]
    def handler(request):
        sent.append(request)
        return httpx.Response(400,json={'error':{'code':provider_code,
            'message':'PRIVATE PROVIDER RESPONSE MUST NOT BE SAVED'}})
    gateway=Gateway(Settings(tmp_path/'rejection',provider='deepseek',api_key='synthetic-key',
                             live_enabled=True),db,httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderPaused):
        ProjectEvidenceLoop(db,gateway).ask(run,'What source context is retained?')
    with pytest.raises(ProviderPaused):
        ProjectEvidenceLoop(db,gateway).ask(run,'What source context is retained?')
    assert len(sent)==1
    call=db.one('SELECT * FROM model_calls')
    serialized=json.dumps(call)
    assert expected_class in serialized and 'PRIVATE PROVIDER RESPONSE' not in serialized
    assert call['actual_units'] is None
    gateway.close()
