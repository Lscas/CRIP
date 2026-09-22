"""Project-file Q&A stays run-scoped, evidence-grounded, and budgeted."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from app.db import BudgetError, Database, dumps, paid_task_key
from app.gateway import Gateway, InvalidModelOutput
from app.questions import (
    ProjectQuestions, _workflow_index_status_conflicts, expanded_query_terms,
    numeric_rfi_search_terms, requested_workflow_counts, requested_workflow_list,
    requires_source_diversity, requires_workflow_inventory,
    requires_workflow_status_index, retrieve_evidence, search_query_terms,
    validate_answer_model,
)
from app.settings import ROOT, Settings
from app.workflows import build_workflow_index
from .conftest import upload


def _evidence(client, project, rows):
    document = upload(client, project['id'], 'project-spec.txt', b'project source')
    run = client.app.state.runner.create(project['id'])
    db = client.app.state.db
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?", (run['id'],))
    base = json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    values = []
    for index, text in enumerate(rows, 1):
        eid = f'EV-QA-{index}'
        payload = {**base, 'evidence_id': eid, 'tenant_id': 'local',
                   'project_id': project['id'], 'input_snapshot_id': run['snapshot_id'],
                   'document_id': document['document_id'], 'raw_text': text,
                   'locator': {**base['locator'], 'section': f'Section {index}'}}
        values.append((run['id'] + ':' + eid, run['id'], project['id'], document['document_id'],
                       json.dumps(payload), 'EXTRACTED', None, ''))
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)', values)
    return client.app.state.runner.get(run['id'])


def _source_evidence(client, project, sources):
    documents=[]
    for name,rows in sources:
        document=upload(client,project['id'],name,(name+' source').encode())
        documents.append((name,document,rows))
    run=client.app.state.runner.create(project['id'])
    db=client.app.state.db
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?",(run['id'],))
    base=json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    values=[];index=0
    for name,document,rows in documents:
        for text in rows:
            index+=1;eid=f'EV-SOURCE-{index}'
            payload={**base,'evidence_id':eid,'tenant_id':'local','project_id':project['id'],
                     'input_snapshot_id':run['snapshot_id'],'document_id':document['document_id'],
                     'raw_text':text,'locator':{**base['locator'],'section':name}}
            values.append((run['id']+':'+eid,run['id'],project['id'],document['document_id'],
                           json.dumps(payload),'EXTRACTED',None,''))
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',values)
    return client.app.state.runner.get(run['id'])


def _live_settings(tmp_path):
    return Settings(tmp_path, provider='deepseek', live_enabled=True, prices_confirmed=True,
                    api_key='test-not-real', input_rate=Decimal('1'),
                    output_rate=Decimal('2'), start_worker=False)


def test_retrieval_ranks_specific_project_evidence(client, project):
    run = _evidence(client, project, [
        'Landscaping irrigation requirements are shown elsewhere.',
        'Domestic water service pipe shall be 2 inch Type L copper.',
        'Concrete compressive strength shall be 4,000 psi.',
    ])

    found = retrieve_evidence(client.app.state.db, run, 'What is the water service pipe material and size?')

    assert found and found[0]['evidence_id'] == 'EV-QA-2'
    assert all(item['project_id'] == project['id'] for item in found)


def test_retrieval_does_not_lose_late_exact_match_behind_common_terms(client, project):
    rows = [f'General water coordination note {index}.' for index in range(160)]
    rows.append('RFI 42 response: Domestic water service pipe shall be 2 inch Type L copper.')
    run = _evidence(client, project, rows)

    found = retrieve_evidence(
        client.app.state.db,
        run,
        'What does RFI 42 require for the water service pipe material and size?',
    )

    assert found and found[0]['evidence_id'] == 'EV-QA-161'


def test_retrieval_matches_a_zero_padded_numeric_rfi_reference_under_candidate_pressure(
        client, project):
    rows = [
        f'Specification note references an RFI response for water service pipe material size '
        f'requirements {index}.' for index in range(160)
    ]
    rows.append(
        'Specification note references RFI 0042 response: '
        'Water service pipe shall be 2 inch Type L copper.')
    run = _evidence(client, project, rows)

    db=client.app.state.db
    found = retrieve_evidence(
        db,
        run,
        'What does RFI 42 require for the water service pipe material and size?',
    )
    db.evidence_search_available=False
    fallback=retrieve_evidence(
        db,run,'What does RFI 42 require for the water service pipe material and size?')

    assert found and found[0]['evidence_id'] == 'EV-QA-161'
    assert fallback and fallback[0]['evidence_id'] == 'EV-QA-161'


def test_retrieval_matches_a_zero_padded_rfi_in_email_under_candidate_pressure(
        client, project):
    rows = [
        'From: contractor@example.test\nSubject: RFI response\n'
        f'General response coordination note {index}.' for index in range(160)
    ]
    rows.append(
        'From: architect@example.test\n'
        'Subject: Request for Information No. 00077 response\n'
        'Request for Information No. 00077 response: Use the reviewed Type L copper detail.')
    run = _evidence(client, project, rows)

    found = retrieve_evidence(
        client.app.state.db,
        run,
        'Who answered Request for Information No. 77 in the email?',
    )

    assert found and found[0]['evidence_id'] == 'EV-QA-161'


@pytest.mark.parametrize(('question','expected'),[
    ('What does RFI ARC-42 require?','EV-QA-2'),
    ('What is the status of Submittal 23-01?','EV-QA-4'),
])
def test_zero_padding_does_not_merge_prefixed_rfi_or_submittal_identifiers(
        client, project, question, expected):
    run = _evidence(client, project, [
        'RFI ARC-0042 response: Use copper pipe.',
        'RFI ARC-42 response: Use stainless steel pipe.',
        'Submittal 23-001 status: Pending.',
        'Submittal 23-01 status: Approved as noted.',
    ])

    found=retrieve_evidence(client.app.state.db,run,question)

    assert found and found[0]['evidence_id']==expected


def test_numeric_rfi_search_aliases_are_bounded_and_require_a_pure_numeric_identifier():
    primary=search_query_terms(
        'What did RFI 42 require for the water service pipe material size and inspection?')
    aliases=numeric_rfi_search_terms(
        'What did RFI 42 require for the water service pipe material size and inspection?')

    assert len(primary)<=24 and len(aliases)<=24
    assert {'rfi 0042','request for information number 0042'}.issubset(aliases)
    assert numeric_rfi_search_terms('What is shown on page 42?')==[]
    assert numeric_rfi_search_terms('What does RFI ARC-42 require?')==[]
    assert numeric_rfi_search_terms('What does RFI 42-1 require?')==[]
    assert numeric_rfi_search_terms('What is the status of Submittal 42?')==[]


def test_retrieval_maps_question_terms_to_bounded_workflow_synonyms(client, project):
    rows = ['Submittal 23-01 cover sheet.' for _ in range(160)]
    rows.append('Submittal 23-01 status: Approved as noted for AHU-1.')
    run = _evidence(client, project, rows)

    found = retrieve_evidence(
        client.app.state.db,
        run,
        'Was Submittal 23-01 accepted?',
    )

    assert found and found[0]['evidence_id'] == 'EV-QA-161'


def test_retrieval_does_not_count_a_synonym_inside_an_unrelated_word(client, project):
    run = _evidence(client, project, [
        'Oversized equipment storage requirements.',
        'Equipment dimensions: 24 x 36 inches.',
    ])

    found = retrieve_evidence(client.app.state.db, run, 'What is the equipment size?')

    assert found and found[0]['evidence_id'] == 'EV-QA-2'


def test_retrieval_expands_rfi_response_and_email_sender_terms(client, project):
    rows = ['RFI 42 question remains open.' for _ in range(160)]
    rows.append('RFI 42 response: Use 2 inch Type L copper.')
    rows.extend('Email thread for RFI 42.' for _ in range(160))
    rows.append(
        'From: architect@example.com\nSubject: RFI 42 response\nUse 2 inch Type L copper.')
    run = _evidence(client, project, rows)

    rfi = retrieve_evidence(client.app.state.db, run, 'What answer was issued for RFI 42?')
    email = retrieve_evidence(client.app.state.db, run, 'Who sent the reply for RFI 42?')

    assert rfi and rfi[0]['evidence_id'] == 'EV-QA-161'
    assert email and email[0]['evidence_id'] == 'EV-QA-322'


def test_retrieval_treats_workflow_phrases_as_single_bounded_concepts(client, project):
    rows = ['Drawing 23-01 cover page.' for _ in range(160)]
    rows.append('Submittal 23-01 status: Approved as noted.')
    rows.extend('Page 42 general note.' for _ in range(160))
    rows.append('RFI 42 response: Use Type L copper.')
    run = _evidence(client, project, rows)

    submittal = retrieve_evidence(client.app.state.db, run, 'What is Shop Drawing 23-01?')
    rfi = retrieve_evidence(client.app.state.db, run, 'Show Request for Information 42.')

    assert submittal and submittal[0]['evidence_id'] == 'EV-QA-161'
    assert rfi and rfi[0]['evidence_id'] == 'EV-QA-322'


def test_query_expansion_is_bounded_and_never_adds_opposite_status():
    expanded = expanded_query_terms(
        'Was Submittal 23-01 accepted and was RFI 42 answered in the email '
        'specification revision?')

    assert len(expanded) <= 24
    assert {'approved', 'response', 'message'}.issubset(expanded)
    assert 'rejected' not in expanded


def test_comparison_question_keeps_spec_rfi_submittal_and_email_sources(client, project):
    repeated = [
        'Specification comparison note: pipe requirements reference RFI 42, '
        'Submittal 23-01, and the email response.'
    ] * 10
    run = _source_evidence(client, project, [
        ('project-spec.txt', repeated),
        ('RFI-42-response.txt', ['RFI 42 response: Use 2 inch Type L copper pipe.']),
        ('Submittal-23-01.txt', ['Submittal 23-01: Type L copper pipe approved as noted.']),
        ('architect-email.eml', [
            'From: architect@example.com\nSubject: RFI 42 response\nUse Type L copper pipe.']),
    ])

    found = retrieve_evidence(
        client.app.state.db,
        run,
        'Compare the pipe requirements in the specification, RFI 42, '
        'Submittal 23-01, and the email response.',
    )

    assert {item['file_name'] for item in found} == {
        'project-spec.txt', 'RFI-42-response.txt', 'Submittal-23-01.txt',
        'architect-email.eml',
    }


def test_source_diversity_requires_comparison_or_multiple_named_sources():
    assert requires_source_diversity('Compare the pipe requirements.') is True
    assert requires_source_diversity('What do the specification and RFI 42 require?') is True
    assert requires_source_diversity('What is the pipe size?') is False


def test_non_comparison_question_keeps_relevance_first_results(client, project):
    run = _source_evidence(client, project, [
        ('project-spec.txt', ['Pipe size shall be 2 inch Type L copper.'] * 8),
        ('RFI-42-response.txt', ['RFI 42 mentions copper piping.']),
    ])

    found = retrieve_evidence(client.app.state.db, run, 'What is the pipe size?')

    assert len(found) == 8
    assert {item['file_name'] for item in found} == {'project-spec.txt'}


def test_comparison_diversifies_workflow_sections_inside_one_document(client, project):
    rows = [
        'Specification comparison note: pipe requirements reference RFI 42 response.'
    ] * 200
    rows.append('RFI 42 response: Use 2 inch Type L copper pipe.')
    run = _evidence(client, project, rows)

    found = retrieve_evidence(
        client.app.state.db, run, 'Compare the specification requirements and RFI 42 response.')

    assert 'EV-QA-201' in {item['evidence_id'] for item in found}


def test_retrieval_fallback_does_not_apply_row_order_cutoff(client, project):
    rows = [f'General equipment coordination note {index}.' for index in range(160)]
    rows.append('Submittal 23-01 status: Approved as noted for AHU-1.')
    run = _evidence(client, project, rows)
    client.app.state.db.evidence_search_available = False

    found = retrieve_evidence(
        client.app.state.db,
        run,
        'What is the status of Submittal 23-01 for AHU-1?',
    )

    assert found and found[0]['evidence_id'] == 'EV-QA-161'


def test_retrieval_ranks_workflow_identifiers_and_email_metadata(client, project):
    run = _evidence(client, project, [
        'General equipment coordination requirements.',
        'Submittal 23-01 status: Approved as noted for AHU-1.',
        'Email subject: RFI 42 response. From: architect@example.com. '
        'Domestic water service pipe shall be 2 inch Type L copper.',
    ])

    submittal = retrieve_evidence(
        client.app.state.db, run, 'What is the status of Submittal 23-01 for AHU-1?')
    email = retrieve_evidence(
        client.app.state.db, run, 'What did the architect email say in the RFI 42 response?')

    assert submittal and submittal[0]['evidence_id'] == 'EV-QA-2'
    assert email and email[0]['evidence_id'] == 'EV-QA-3'


def test_retrieval_indexes_updated_sheet_locator(client, project):
    run = _evidence(client, project, ['Mechanical equipment schedule.'])
    db = client.app.state.db
    row = db.one('SELECT id,payload FROM evidence WHERE run_id=?', (run['id'],))
    payload = json.loads(row['payload'])
    payload['locator']['sheet'] = 'M1.1'
    db.execute('UPDATE evidence SET payload=? WHERE id=?', (json.dumps(payload), row['id']))

    found = retrieve_evidence(db, run, 'What is shown on sheet M1.1?')
    db.evidence_search_available = False
    fallback = retrieve_evidence(db, run, 'What is shown on sheet M1.1?')

    assert found and found[0]['evidence_id'] == 'EV-QA-1'
    assert fallback and fallback[0]['evidence_id'] == 'EV-QA-1'


def test_excluded_vision_output_cannot_crowd_out_source_evidence(client, project):
    rows = ['RFI 42 water service pipe material size.' for _ in range(160)]
    rows.append('RFI 42 source response: Water service pipe shall be 2 inch Type L copper.')
    run = _evidence(client, project, rows)
    db = client.app.state.db
    for row in db.all('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY rowid LIMIT 160',
                      (run['id'],)):
        payload = json.loads(row['payload'])
        payload['content_basis'] = 'MODEL_VISION_OUTPUT'
        db.execute('UPDATE evidence SET payload=? WHERE id=?', (json.dumps(payload), row['id']))

    found = retrieve_evidence(
        db, run, 'What does RFI 42 require for the water service pipe material and size?')

    assert found and found[0]['evidence_id'] == 'EV-QA-161'


def test_evidence_search_migration_backfills_an_existing_database(tmp_path):
    path = tmp_path / 'before-evidence-search.sqlite3'
    payload = {
        'evidence_id': 'EV-OLD-1',
        'project_id': 'P-old',
        'document_id': 'D-old',
        'raw_text': 'RFI 77 response requires a 3 inch copper service.',
        'locator': {'section': 'RFI 77'},
    }
    with sqlite3.connect(path) as connection:
        connection.executescript((ROOT / 'migrations/001_initial.sql').read_text(encoding='utf-8'))
        connection.execute("INSERT INTO projects VALUES('P-old','Old project','now')")
        connection.execute(
            "INSERT INTO documents VALUES('D-old','P-old','old-rfi.txt',1,'sha','object','now')")
        connection.execute('''INSERT INTO runs VALUES(
            'R-old','P-old','mock','SN-old','["D-old"]','COMPLETED','DONE','','now',0,1,
            '{}','{}',0)''')
        connection.execute(
            "INSERT INTO evidence VALUES('E-old','R-old','P-old','D-old',?,'EXTRACTED',NULL,'')",
            (json.dumps(payload),),
        )

    db = Database(path)
    found = retrieve_evidence(db, {'id': 'R-old'}, 'What is required by RFI 77?')

    assert db.evidence_search_available is True
    assert db.one('SELECT COUNT(*) AS count FROM evidence_search')['count'] == 1
    assert db.one('SELECT version FROM schema_migrations WHERE version=7')['version'] == 7
    assert found and found[0]['evidence_id'] == 'EV-OLD-1'


def test_missing_fts5_uses_supported_fallback():
    class MissingFtsConnection:
        class EmptyResult:
            @staticmethod
            def fetchone():
                return None

        @staticmethod
        def execute(query, args=()):
            return MissingFtsConnection.EmptyResult()

        @staticmethod
        def executescript(script):
            raise sqlite3.OperationalError('no such module: fts5')

    assert Database._install_evidence_search(MissingFtsConnection()) is False


def test_mock_question_returns_retrieval_context_without_fabricated_answer(client, project):
    run = _evidence(client, project, ['Domestic water service pipe shall be 2 inch Type L copper.'])

    response = client.post(f'/api/projects/{project["id"]}/questions', json={
        'run_id': run['id'], 'question': 'What is the water service pipe material and size?'
    })

    assert response.status_code == 200
    value = response.json()
    assert value['status'] == 'MODEL_DISABLED'
    assert 'Mock mode' in value['answer']
    assert value['citations'][0]['quote'] == 'Domestic water service pipe shall be 2 inch Type L copper.'
    assert client.app.state.db.all('SELECT id FROM model_calls WHERE run_id=?', (run['id'],)) == []


def test_live_answer_is_exactly_cited_and_budgeted(client, project, tmp_path):
    run = _evidence(client, project, ['Domestic water service pipe shall be 2 inch Type L copper.'])
    db = client.app.state.db
    response_data = {'status': 'ANSWERED', 'answer': 'Use 2 inch Type L copper.',
                     'source_findings': [], 'citations': [{
        'evidence_id': 'EV-QA-1',
        'quote': 'Domestic water service pipe shall be 2 inch Type L copper.'
    }]}
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            'id': 'question-test',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(response_data)}}],
            'usage': {'prompt_tokens': 120, 'completion_tokens': 30},
        })

    gateway = Gateway(_live_settings(tmp_path), db,
                      httpx.Client(transport=httpx.MockTransport(handler)))
    service = ProjectQuestions(db, gateway)
    result = service.ask(run, 'What is the water service pipe material and size?')
    recovered = service.ask(run, 'What is the water service pipe material and size?')
    equivalent = service.ask(run, 'What  is the water service pipe material and size?')

    assert result['status'] == 'ANSWERED' and result['answer'] == 'Use 2 inch Type L copper.'
    assert result['citations'][0]['evidence_id'] == 'EV-QA-1'
    assert result['citations'][0]['start'] == 0
    assert recovered['answer'] == result['answer'] and recovered['cached'] is True
    assert equivalent['answer'] == result['answer'] and equivalent['cached'] is True
    assert len(requests) == 1
    assert 'Domestic water service pipe' not in requests[0]['messages'][0]['content']
    assert 'Domestic water service pipe' in requests[0]['messages'][1]['content']
    call = db.one('SELECT task_key,state FROM model_calls WHERE run_id=?', (run['id'],))
    assert call['task_key'].startswith('answer:') and call['state'] == 'SETTLED'


def test_question_recovers_a_settled_pre_normalization_task_without_http(client, project, tmp_path):
    run = _evidence(client, project, [
        'Domestic water service pipe shall be 2 inch Type L copper.',
    ])
    question='What  is the water service pipe material and size?'
    evidence=retrieve_evidence(client.app.state.db,run,question)
    settings=_live_settings(tmp_path)
    requests=[]
    gateway=Gateway(settings,client.app.state.db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(500))[1])))
    evidence_fingerprint=[
        (item['evidence_id'],item.get('prompt_text',item['raw_text'])) for item in evidence]
    legacy=[run['project_id'],run['snapshot_id'],settings.api_base_url,settings.cheap_model,
            gateway.answer_prompt_hash,question,evidence_fingerprint]
    family='answer:'+hashlib.sha256(dumps(legacy).encode()).hexdigest()
    attempt=client.app.state.db.reserve(
        run['project_id'],run['id'],paid_task_key(family,0),Decimal('0.01'),
        settings.cheap_model,'legacy-request',settings.input_rate,settings.output_rate,
        interactive_question=True)
    response_data={
        'status':'ANSWERED','answer':'Use 2 inch Type L copper.','source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1',
                      'quote':'Domestic water service pipe shall be 2 inch Type L copper.'}],
    }
    client.app.state.db.finalize_model_call(
        attempt,Decimal('0.01'),{'prompt_tokens':1,'completion_tokens':1},
        'legacy-question',response=response_data)

    result=ProjectQuestions(client.app.state.db,gateway).ask(run,question)

    assert result['cached'] is True and result['answer']=='Use 2 inch Type L copper.'
    assert requests==[]


def test_live_answer_rejects_a_quote_not_in_supplied_evidence(client, project, tmp_path):
    run = _evidence(client, project, ['Domestic water service pipe shall be 2 inch Type L copper.'])
    db = client.app.state.db
    bad = {'status': 'ANSWERED', 'answer': 'Use PVC.', 'source_findings': [], 'citations': [
        {'evidence_id': 'EV-QA-1', 'quote': 'Domestic water service pipe shall be PVC.'}]}
    gateway = Gateway(_live_settings(tmp_path), db, httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            'id': 'question-bad-quote',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(bad)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20},
        }))))

    with pytest.raises(InvalidModelOutput, match='question response'):
        ProjectQuestions(db, gateway).ask(run, 'What is the water service pipe material?')

    call = db.one('SELECT state,response,error FROM model_calls WHERE run_id=?', (run['id'],))
    assert call['state'] == 'SETTLED_ERROR' and call['response'] is None
    assert json.loads(call['error'])['class'] == 'PROJECT_ANSWER'


def test_live_answer_settles_an_opposite_disposition_error_without_retry(
        client, project, tmp_path):
    source='Submittal 23-01 status: REJECTED.'
    run=_evidence(client,project,[source])
    db=client.app.state.db
    wrong={
        'status':'ANSWERED','answer':'Submittal 23-01 is approved.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(200,json={
            'id':'question-opposite-disposition',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(wrong)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':20},
        }))[1])))

    with pytest.raises(InvalidModelOutput,match='question response'):
        ProjectQuestions(db,gateway).ask(run,'What is the status of Submittal 23-01?')

    call=db.one('SELECT state,response,error FROM model_calls WHERE run_id=?',(run['id'],))
    assert len(requests)==1
    assert call['state']=='SETTLED_ERROR' and call['response'] is None
    assert json.loads(call['error'])['class']=='PROJECT_ANSWER'


def test_live_answer_settles_a_conflicting_disposition_error_without_retry(
        client, project, tmp_path):
    sources=['Submittal 23-01 status: PENDING.','Submittal 23-01 status: REJECTED.']
    run=_evidence(client,project,sources)
    db=client.app.state.db
    wrong={
        'status':'ANSWERED','answer':'Submittal 23-01 is rejected.',
        'source_findings':[],
        'citations':[{'evidence_id':f'EV-QA-{index}','quote':source}
                     for index,source in enumerate(sources,1)],
    }
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),db,httpx.Client(transport=httpx.MockTransport(
        lambda request:(requests.append(request),httpx.Response(200,json={
            'id':'question-conflicting-disposition',
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(wrong)}}],
            'usage':{'prompt_tokens':110,'completion_tokens':20},
        }))[1])))

    with pytest.raises(InvalidModelOutput,match='question response'):
        ProjectQuestions(db,gateway).ask(run,'What is the status of Submittal 23-01?')

    call=db.one('SELECT state,response,error FROM model_calls WHERE run_id=?',(run['id'],))
    assert len(requests)==1
    assert call['state']=='SETTLED_ERROR' and call['response'] is None
    assert json.loads(call['error'])['class']=='PROJECT_ANSWER'


def test_answer_rejects_a_numeric_claim_missing_from_its_citation(client, project):
    run = _evidence(client, project, [
        'Domestic water service pipe shall be 2 inch Type L copper.',
    ])
    evidence=retrieve_evidence(
        client.app.state.db,run,'What is the water service pipe material and size?')
    wrong={
        'status':'ANSWERED','answer':'Use 4 inch Type L copper.','source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1',
                      'quote':'Domestic water service pipe shall be 2 inch Type L copper.'}],
    }

    with pytest.raises(ValueError,match='numeric'):
        validate_answer_model(wrong,evidence,'What is the water service pipe material and size?')


def test_numeric_grounding_accepts_commas_and_leading_zero_formatting(client, project):
    run = _evidence(client, project, [
        'RFI 0042 response requires concrete with 4,000 psi compressive strength.',
    ])
    evidence=retrieve_evidence(
        client.app.state.db,run,'What concrete compressive strength is required?')
    grounded={
        'status':'ANSWERED','answer':'RFI 42 requires 4000 psi concrete.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1',
                      'quote':'RFI 0042 response requires concrete with 4,000 psi compressive strength.'}],
    }

    validate_answer_model(grounded,evidence,'What concrete compressive strength is required?')


def test_numeric_grounding_does_not_merge_comma_separated_values(client, project):
    run = _evidence(client, project, ['Grid coordinates are 1,2.'])
    evidence=retrieve_evidence(client.app.state.db,run,'What are the grid coordinates?')
    wrong={
        'status':'ANSWERED','answer':'The grid coordinate is 12.','source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':'Grid coordinates are 1,2.'}],
    }

    with pytest.raises(ValueError,match='numeric'):
        validate_answer_model(wrong,evidence,'What are the grid coordinates?')


@pytest.mark.parametrize(('source','question','claim'),[
    ('RFI 42 status: CLOSED.', 'What is the status of RFI 42?',
     'RFI 42 is open.'),
    ('Submittal 23-01 status: REJECTED.', 'What is the status of Submittal 23-01?',
     'Submittal 23-01 is approved.'),
    ('From: architect@example.test\nSubject: Submittal review\nStatus: PENDING.',
     'What status does the email report?', 'The email reports an approved status.'),
])
def test_answer_rejects_a_workflow_disposition_opposite_to_its_citation(
        client, project, source, question, claim):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='disposition'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source','question','claim'),[
    ('RFI 42 status: CLOSED.', 'What is the status of RFI 42?',
     'RFI 42 is resolved.'),
    ('Submittal 23-01 status: APPROVED AS NOTED.',
     'What is the status of Submittal 23-01?', 'Submittal 23-01 is approved.'),
    ('Submittal 23-01 status: NOT APPROVED.',
     'What is the status of Submittal 23-01?', 'Submittal 23-01 is rejected.'),
    ('From: architect@example.test\nSubject: Submittal review\nStatus: UNDER REVIEW.',
     'What status does the email report?', 'The email reports a pending status.'),
])
def test_workflow_disposition_grounding_accepts_bounded_equivalent_wording(
        client, project, source, question, claim):
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    validate_answer_model(grounded,evidence,question)


def test_disposition_grounding_does_not_invent_an_approval_qualifier(client, project):
    source='Submittal 23-01 status: APPROVED.'
    question='What is the status of Submittal 23-01?'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    embellished={
        'status':'ANSWERED','answer':'Submittal 23-01 is approved as noted.',
        'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='disposition'):
        validate_answer_model(embellished,evidence,question)


@pytest.mark.parametrize(('source','claim'),[
    ('RFI 42 status: CLOSED.','RFI 42 is not closed.'),
    ('Submittal 23-01 status: REJECTED.','Submittal 23-01 is not rejected.'),
])
def test_disposition_grounding_does_not_drop_negation(
        client, project, source, claim):
    question='What is the workflow status?'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    wrong={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    with pytest.raises(ValueError,match='disposition'):
        validate_answer_model(wrong,evidence,question)


@pytest.mark.parametrize(('source','claim'),[
    ('RFI 42 status: OPEN WAITING FOR SUBMISSION.',
     'RFI 42 is open and pending.'),
    ('RFI 42 status: CLOSED-DRAFT.','RFI 42 is closed and draft.'),
    ('Submittal 23-01 status: REVIEWED.','Submittal 23-01 was reviewed.'),
])
def test_disposition_grounding_covers_remaining_parser_status_boundaries(
        client, project, source, claim):
    question='What is the workflow status?'
    run=_evidence(client,project,[source])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    grounded={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':'EV-QA-1','quote':source}],
    }

    validate_answer_model(grounded,evidence,question)


def test_disposition_grounding_uses_each_comparison_finding_own_citations(client, project):
    run=_source_evidence(client,project,[
        ('RFI-42-response.txt',['RFI 42 status: CLOSED.']),
        ('Submittal-23-01.txt',['Submittal 23-01 status: APPROVED.']),
    ])
    evidence=retrieve_evidence(
        client.app.state.db,run,'Compare the status of RFI 42 and Submittal 23-01.')
    swapped={
        'status':'ANSWERED','answer':'The sources report different dispositions.','citations':[],
        'source_findings':[
            {'source_type':'RFI','file_name':'RFI-42-response.txt',
             'statement':'RFI 42 is approved.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':'RFI 42 status: CLOSED.'}]},
            {'source_type':'SUBMITTAL','file_name':'Submittal-23-01.txt',
             'statement':'Submittal 23-01 is closed.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'Submittal 23-01 status: APPROVED.'}]},
        ],
    }

    with pytest.raises(ValueError,match='source finding contains a workflow disposition'):
        validate_answer_model(
            swapped,evidence,'Compare the status of RFI 42 and Submittal 23-01.')


@pytest.mark.parametrize(('sources','question','claim'),[
    (['RFI 42 status: OPEN.','RFI 42 status: CLOSED.'],
     'What is the status of RFI 42?','RFI 42 is closed.'),
    (['Submittal 23-01 status: PENDING.','Submittal 23-01 status: REJECTED.'],
     'What is the status of Submittal 23-01?','Submittal 23-01 is rejected.'),
    (['Submittal 23-01 status: APPROVED.',
      'Submittal 23-01 status: APPROVED AS NOTED.'],
     'What is the status of Submittal 23-01?','Submittal 23-01 is approved as noted.'),
    (['Submittal 23-01 status: PENDING.','Submittal 23-01 status: SUBMITTED.'],
     'What is the status of Submittal 23-01?','Submittal 23-01 is pending.'),
    (['From: architect@example.test\nSubject: Submittal review\nStatus: PENDING.',
      'From: contractor@example.test\nSubject: Submittal review\nStatus: APPROVED.'],
     'What status does the email report?','The email reports an approved status.'),
])
def test_answer_rejects_a_silent_choice_between_conflicting_cited_dispositions(
        client, project, sources, question, claim):
    run=_evidence(client,project,sources)
    evidence=retrieve_evidence(client.app.state.db,run,question)
    selected={item['raw_text']:item['evidence_id'] for item in evidence}
    ambiguous={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':selected[source],'quote':source} for source in sources],
    }

    with pytest.raises(ValueError,match='conflicting'):
        validate_answer_model(ambiguous,evidence,question)


@pytest.mark.parametrize(('sources','question','claim'),[
    (['RFI 42 status: OPEN.','RFI 42 status: CLOSED.'],
     'What is the status of RFI 42?','RFI 42 is closed.'),
    (['Submittal 23-01 status: PENDING.','Submittal 23-01 status: REJECTED.'],
     'What is the status of Submittal 23-01?','Submittal 23-01 is rejected.'),
    (['From: architect@example.test\nSubject: Submittal review\nStatus: PENDING.',
      'From: contractor@example.test\nSubject: Submittal review\nStatus: APPROVED.'],
     'What status does the email report?','The email reports an approved status.'),
])
def test_answer_cannot_hide_a_conflicting_retrieved_disposition_by_omitting_its_citation(
        client, project, sources, question, claim):
    run=_evidence(client,project,sources)
    evidence=retrieve_evidence(client.app.state.db,run,question)
    selected=next(item for item in evidence if item['raw_text']==sources[-1])
    hidden={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':selected['evidence_id'],'quote':sources[-1]}],
    }

    with pytest.raises(ValueError,match='conflicting'):
        validate_answer_model(hidden,evidence,question)


@pytest.mark.parametrize(('sources','question','claim'),[
    (['Request for Information No. 0042 status: CLOSED.','RFI 43 status: OPEN.'],
     'What is the status of RFI 42?','RFI 42 is closed.'),
    (['Submittal 23-01 status: APPROVED.','Submittal 23-02 status: REJECTED.'],
     'What is the status of Submittal 23-01?','Submittal 23-01 is approved.'),
])
def test_retrieved_disposition_conflicts_remain_isolated_by_exact_workflow_identity(
        client, project, sources, question, claim):
    run=_evidence(client,project,sources)
    evidence=retrieve_evidence(client.app.state.db,run,question)
    selected=next(item for item in evidence if item['raw_text']==sources[0])
    grounded={
        'status':'ANSWERED','answer':claim,'source_findings':[],
        'citations':[{'evidence_id':selected['evidence_id'],'quote':sources[0]}],
    }

    validate_answer_model(grounded,evidence,question)


def test_source_finding_cannot_hide_a_conflicting_retrieved_disposition(
        client, project):
    question='Compare the status of RFI 42 and Submittal 23-01.'
    run=_source_evidence(client,project,[
        ('RFI-42-response.txt',['RFI 42 status: OPEN.','RFI 42 status: CLOSED.']),
        ('Submittal-23-01.txt',['Submittal 23-01 status: APPROVED.']),
    ])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    hidden={
        'status':'ANSWERED','answer':'The RFI is closed and the Submittal is approved.',
        'citations':[],
        'source_findings':[
            {'source_type':'RFI','file_name':'RFI-42-response.txt',
             'statement':'RFI 42 is closed.',
             'citations':[{'evidence_id':'EV-SOURCE-2','quote':'RFI 42 status: CLOSED.'}]},
            {'source_type':'SUBMITTAL','file_name':'Submittal-23-01.txt',
             'statement':'Submittal 23-01 is approved.',
             'citations':[{'evidence_id':'EV-SOURCE-3',
                           'quote':'Submittal 23-01 status: APPROVED.'}]},
        ],
    }

    with pytest.raises(ValueError,match='retrieved source finding cites conflicting'):
        validate_answer_model(hidden,evidence,question)


def test_comparison_keeps_different_source_dispositions_isolated(client, project):
    question='Compare the status of RFI 42 and Submittal 23-01.'
    run=_source_evidence(client,project,[
        ('RFI-42-response.txt',['RFI 42 status: CLOSED.']),
        ('Submittal-23-01.txt',['Submittal 23-01 status: APPROVED.']),
    ])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    comparison={
        'status':'ANSWERED',
        'answer':'The RFI is closed while the Submittal is approved.',
        'citations':[],
        'source_findings':[
            {'source_type':'RFI','file_name':'RFI-42-response.txt',
             'statement':'RFI 42 is closed.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':'RFI 42 status: CLOSED.'}]},
            {'source_type':'SUBMITTAL','file_name':'Submittal-23-01.txt',
             'statement':'Submittal 23-01 is approved.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'Submittal 23-01 status: APPROVED.'}]},
        ],
    }

    validate_answer_model(comparison,evidence,question)


def test_comparison_uses_question_identity_when_a_finding_omits_the_number(
        client, project):
    question='Compare the status of RFI 42 and Submittal 23-01.'
    run=_source_evidence(client,project,[
        ('combined-rfis.txt',['RFI 42 status: CLOSED.','RFI 43 status: OPEN.']),
        ('Submittal-23-01.txt',['Submittal 23-01 status: APPROVED.']),
    ])
    evidence=retrieve_evidence(client.app.state.db,run,question)
    comparison={
        'status':'ANSWERED','answer':'The RFI is closed while the Submittal is approved.',
        'citations':[],
        'source_findings':[
            {'source_type':'RFI','file_name':'combined-rfis.txt',
             'statement':'The RFI is closed.',
             'citations':[{'evidence_id':'EV-SOURCE-1','quote':'RFI 42 status: CLOSED.'}]},
            {'source_type':'SUBMITTAL','file_name':'Submittal-23-01.txt',
             'statement':'The Submittal is approved.',
             'citations':[{'evidence_id':'EV-SOURCE-3',
                           'quote':'Submittal 23-01 status: APPROVED.'}]},
        ],
    }

    validate_answer_model(comparison,evidence,question)


def test_comparison_answer_rejects_a_mixed_summary_without_each_source(client, project, tmp_path):
    run = _source_evidence(client, project, [
        ('project-spec.txt', ['Specification requires 2 inch Type L copper pipe.']),
        ('RFI-42-response.txt', ['RFI 42 response requires 3 inch Type L copper pipe.']),
    ])
    db = client.app.state.db
    incomplete = {
        'status': 'ANSWERED',
        'answer': 'The specification requires 2 inch pipe and RFI 42 changes it to 3 inch.',
        'source_findings': [{
            'source_type': 'SPECIFICATION',
            'file_name': 'project-spec.txt',
            'statement': 'The specification requires 2 inch Type L copper pipe.',
            'citations': [{
                'evidence_id': 'EV-SOURCE-1',
                'quote': 'Specification requires 2 inch Type L copper pipe.',
            }],
        }],
        'citations': [],
    }
    gateway = Gateway(_live_settings(tmp_path), db, httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            'id': 'question-incomplete-comparison',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(incomplete)}}],
            'usage': {'prompt_tokens': 120, 'completion_tokens': 30},
        }))))

    with pytest.raises(InvalidModelOutput, match='question response'):
        ProjectQuestions(db, gateway).ask(
            run, 'Compare the specification and RFI 42 pipe requirements.')


def test_comparison_answer_returns_each_source_with_inline_evidence(client, project, tmp_path):
    run = _source_evidence(client, project, [
        ('project-spec.txt', ['Specification requires 2 inch Type L copper pipe.']),
        ('RFI-42-response.txt', ['RFI 42 response requires 3 inch Type L copper pipe.']),
    ])
    db = client.app.state.db
    comparison = {
        'status': 'ANSWERED',
        'answer': 'RFI 42 increases the specified pipe size from 2 inches to 3 inches.',
        'citations': [],
        'source_findings': [
            {
                'source_type': 'SPECIFICATION',
                'file_name': 'project-spec.txt',
                'statement': 'The specification requires 2 inch Type L copper pipe.',
                'citations': [{
                    'evidence_id': 'EV-SOURCE-1',
                    'quote': 'Specification requires 2 inch Type L copper pipe.',
                }],
            },
            {
                'source_type': 'RFI',
                'file_name': 'RFI-42-response.txt',
                'statement': 'RFI 42 requires 3 inch Type L copper pipe.',
                'citations': [{
                    'evidence_id': 'EV-SOURCE-2',
                    'quote': 'RFI 42 response requires 3 inch Type L copper pipe.',
                }],
            },
        ],
    }
    requests=[]
    gateway = Gateway(_live_settings(tmp_path), db, httpx.Client(transport=httpx.MockTransport(
        lambda request: (requests.append(request), httpx.Response(200, json={
            'id': 'question-valid-comparison',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(comparison)}}],
            'usage': {'prompt_tokens': 160, 'completion_tokens': 60},
        }))[1])))
    service=ProjectQuestions(db,gateway)

    result=service.ask(run,'Compare the specification and RFI 42 pipe requirements.')
    recovered=service.ask(run,'Compare the specification and RFI 42 pipe requirements.')

    assert result['citations']==[]
    assert [item['source_type'] for item in result['source_findings']]==['SPECIFICATION','RFI']
    assert result['source_findings'][1]['citations'][0]['quote'].startswith('RFI 42 response')
    assert recovered['cached'] is True and len(requests)==1


def test_comparison_source_type_must_match_the_cited_file(client, project):
    run = _source_evidence(client, project, [
        ('project-spec.txt', ['Specification requires 2 inch Type L copper pipe.']),
        ('RFI-42-response.txt', ['RFI 42 response requires 3 inch Type L copper pipe.']),
    ])
    evidence=retrieve_evidence(
        client.app.state.db,run,'Compare the specification and RFI 42 pipe requirements.')
    mislabeled={
        'status':'ANSWERED','answer':'The sources differ.','citations':[],
        'source_findings':[
            {'source_type':'EMAIL','file_name':'project-spec.txt','statement':'Two inch pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-1',
                           'quote':'Specification requires 2 inch Type L copper pipe.'}]},
            {'source_type':'RFI','file_name':'RFI-42-response.txt','statement':'Three inch pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'RFI 42 response requires 3 inch Type L copper pipe.'}]},
        ],
    }

    with pytest.raises(ValueError,match='type does not match'):
        validate_answer_model(
            mislabeled,evidence,'Compare the specification and RFI 42 pipe requirements.')


def test_comparison_rejects_a_numeric_finding_missing_from_its_own_citation(client, project):
    run = _source_evidence(client, project, [
        ('project-spec.txt', ['Specification requires 2 inch Type L copper pipe.']),
        ('RFI-42-response.txt', ['RFI 42 response requires 3 inch Type L copper pipe.']),
    ])
    evidence=retrieve_evidence(
        client.app.state.db,run,'Compare the specification and RFI 42 pipe requirements.')
    wrong={
        'status':'ANSWERED','answer':'The sources specify different pipe sizes.','citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'project-spec.txt',
             'statement':'The specification requires 4 inch Type L copper pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-1',
                           'quote':'Specification requires 2 inch Type L copper pipe.'}]},
            {'source_type':'RFI','file_name':'RFI-42-response.txt',
             'statement':'RFI 42 requires 3 inch Type L copper pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'RFI 42 response requires 3 inch Type L copper pipe.'}]},
        ],
    }

    with pytest.raises(ValueError,match='numeric'):
        validate_answer_model(
            wrong,evidence,'Compare the specification and RFI 42 pipe requirements.')


def test_comparison_accepts_a_csi_section_in_a_generic_file_as_specification(client, project):
    run = _source_evidence(client, project, [
        ('combined-project.pdf', ['Domestic water pipe shall be 2 inch Type L copper.']),
        ('RFI-42-response.txt', ['RFI 42 response requires 3 inch Type L copper pipe.']),
    ])
    db=client.app.state.db
    row=db.one('SELECT id,payload FROM evidence WHERE run_id=? ORDER BY id LIMIT 1',(run['id'],))
    payload=json.loads(row['payload'])
    payload['locator']['section']='Section 22 11 16'
    db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    evidence=retrieve_evidence(
        db,run,'Compare the specification and RFI 42 pipe requirements.')
    comparison={
        'status':'ANSWERED','answer':'RFI 42 increases the pipe size.','citations':[],
        'source_findings':[
            {'source_type':'SPECIFICATION','file_name':'combined-project.pdf',
             'statement':'The specification requires 2 inch Type L copper pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-1',
                           'quote':'Domestic water pipe shall be 2 inch Type L copper.'}]},
            {'source_type':'RFI','file_name':'RFI-42-response.txt',
             'statement':'RFI 42 requires 3 inch Type L copper pipe.',
             'citations':[{'evidence_id':'EV-SOURCE-2',
                           'quote':'RFI 42 response requires 3 inch Type L copper pipe.'}]},
        ],
    }

    validate_answer_model(
        comparison,evidence,'Compare the specification and RFI 42 pipe requirements.')


def test_question_api_rejects_cross_project_run(client, project):
    run = _evidence(client, project, ['Use Type L copper.'])
    other = client.post('/api/projects', json={'name': 'Other project'}).json()

    response = client.post(f'/api/projects/{other["id"]}/questions', json={
        'run_id': run['id'], 'question': 'What pipe material is required?'
    })

    assert response.status_code == 404
    assert client.app.state.db.all('SELECT id FROM model_calls WHERE run_id=?', (run['id'],)) == []


def test_paid_question_is_blocked_before_http_while_an_analysis_is_active(client, project, tmp_path):
    run = _evidence(client, project, ['Domestic water service pipe shall be 2 inch Type L copper.'])
    active = client.app.state.runner.create(project['id'])
    assert active['status'] == 'QUEUED'
    requests = []
    gateway = Gateway(_live_settings(tmp_path), client.app.state.db,
                      httpx.Client(transport=httpx.MockTransport(
                          lambda request: (requests.append(request), httpx.Response(500))[1])))

    with pytest.raises(BudgetError, match='active analysis'):
        ProjectQuestions(client.app.state.db, gateway).ask(
            run, 'What is the water service pipe material and size?')

    assert requests == []
    assert client.app.state.db.all('SELECT id FROM model_calls WHERE run_id=?', (run['id'],)) == []


@pytest.mark.parametrize(('kind','identifier','role','statuses'),[
    ('RFI','42','RESPONSE',('OPEN','CLOSED')),
    ('SUBMITTAL','23-01','SUBMITTAL',('PENDING','REJECTED')),
    ('SUBMITTAL','23-02','SUBMITTAL',('APPROVED AS NOTED','APPROVED')),
])
def test_full_workflow_index_blocks_a_status_conflict_hidden_from_retrieval(
        client, project, tmp_path, kind, identifier, role, statuses):
    question=f'What is the status of {kind.title()} {identifier}?'
    run=_evidence(client,project,[f'{kind} {identifier} status: {statuses[0]}.'])
    current=client.app.state.db.one('''SELECT e.document_id,d.name FROM evidence e
                                       JOIN documents d ON d.id=e.document_id
                                       WHERE e.run_id=?''',(run['id'],))
    workflow_index=build_workflow_index([
        {'document_id':current['document_id'],'name':current['name'],'summary':{
            'document_type':'EMAIL','workflow_contexts':[{
                'workflow_type':kind,'identifier':identifier,'role':role,'status':statuses[0]}]}},
        {'document_id':'D-ARCHIVE','name':'archive.eml','classification_source':'MANUAL','summary':{
            'document_type':'EMAIL','workflow_contexts':[{
                'workflow_type':kind,'identifier':identifier,'role':role,'status':statuses[1]}]}},
    ])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),client.app.state.db,
                    httpx.Client(transport=httpx.MockTransport(
                        lambda request:(requests.append(request),httpx.Response(500))[1])))

    result=ProjectQuestions(client.app.state.db,gateway).ask(run,question,workflow_index)

    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert f'{kind} {identifier}' in result['answer'] and result['retrieved_count']==1
    assert all(status in result['answer'] for status in statuses)
    assert current['name'] in result['answer'] and 'archive.eml' in result['answer']
    assert '[detected]' in result['answer'] and '[manual correction]' in result['answer']
    conflict=result['workflow_conflicts'][0]
    assert conflict['workflow_type']==kind and conflict['identifier']==identifier
    assert {item['status'] for item in conflict['statuses']}==set(statuses)
    assert {source['file_name'] for item in conflict['statuses']
            for source in item['sources']}=={current['name'],'archive.eml'}
    assert {source['classification_source'] for item in conflict['statuses']
            for source in item['sources']}=={'DETECTED','MANUAL'}
    detected=next(source for item in conflict['statuses'] for source in item['sources']
                  if source['classification_source']=='DETECTED')
    assert detected['citation']['quote']==f'{kind} {identifier} status: {statuses[0]}.'
    assert all('citation' not in source for item in conflict['statuses']
               for source in item['sources'] if source['classification_source']=='MANUAL')
    assert requests==[]
    assert client.app.state.db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_workflow_conflict_citation_does_not_borrow_another_workflow_status(
        client, project, tmp_path):
    run=_evidence(client,project,['RFI 42 status: OPEN.\nRFI 43 status: CLOSED.'])
    current=client.app.state.db.one('''SELECT e.document_id,d.name FROM evidence e
                                       JOIN documents d ON d.id=e.document_id
                                       WHERE e.run_id=?''',(run['id'],))
    workflow_index=build_workflow_index([
        {'document_id':current['document_id'],'name':current['name'],'summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[
                {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'OPEN'},
                {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'CLOSED'},
            ]}},
    ])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),client.app.state.db,
                    httpx.Client(transport=httpx.MockTransport(
                        lambda request:(requests.append(request),httpx.Response(500))[1])))

    result=ProjectQuestions(client.app.state.db,gateway).ask(
        run,'What is the status of RFI 42?',workflow_index)

    sources={item['status']:item['sources'][0]
             for item in result['workflow_conflicts'][0]['statuses']}
    assert sources['OPEN']['citation']['quote']=='RFI 42 status: OPEN.'
    assert 'citation' not in sources['CLOSED']
    assert requests==[]


def test_full_workflow_status_guard_is_exact_and_ignores_role_only_ambiguity():
    role_only=build_workflow_index([
        {'document_id':'D-Q1','name':'question-a.txt','summary':{
            'document_type':'RFI_QUESTION','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]}},
        {'document_id':'D-Q2','name':'question-b.txt','summary':{
            'document_type':'RFI_QUESTION','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':'OPEN'}]}},
    ])
    other_identifier=build_workflow_index([
        {'document_id':'D-43A','name':'rfi-43-open.txt','summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'43','role':'RESPONSE','status':'OPEN'}]}},
        {'document_id':'D-43B','name':'rfi-43-closed.txt','summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[{
                'workflow_type':'RFI','identifier':'43','role':'RESPONSE','status':'CLOSED'}]}},
    ])

    assert next(item for item in role_only['items'] if item['kind']=='RFI')['state']=='AMBIGUOUS'
    assert _workflow_index_status_conflicts('What is the status of RFI 42?',role_only)==[]
    assert _workflow_index_status_conflicts('What is the status of RFI 42?',other_identifier)==[]
    assert _workflow_index_status_conflicts('What status does the email report?',other_identifier)==[]
    assert requires_workflow_status_index('What is the status of RFI 42?') is True
    assert requires_workflow_status_index('What does RFI 42 require?') is False


def test_question_api_uses_email_workflow_index_beyond_retrieved_passages(client, project):
    run=_source_evidence(client,project,[
        ('current.eml',['From: architect@example.test\nSubject: RFI 42\nRFI 42 status: OPEN.']),
        ('archive.eml',['Archived coordination note.']),
    ])
    db=client.app.state.db
    documents={row['name']:row['id'] for row in db.all(
        'SELECT id,name FROM documents WHERE project_id=?',(project['id'],))}
    summaries={
        'current.eml':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'OPEN'}]},
        'archive.eml':{'document_type':'EMAIL','workflow_contexts':[{
            'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'CLOSED'}]},
    }
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO document_results VALUES(?,?,?,?)',[
            (run['id'],documents[name],'SUCCESS',json.dumps(summary))
            for name,summary in summaries.items()])

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'What is the status of RFI 42?'})

    assert response.status_code==200
    result=response.json()
    assert result['status']=='INSUFFICIENT_EVIDENCE'
    assert result['retrieved_count']==1 and result['citations']==[]
    assert 'RFI 42' in result['answer']
    assert {source['file_name'] for item in result['workflow_conflicts'][0]['statuses']
            for source in item['sources']}=={'current.eml','archive.eml'}
    sources={source['file_name']:source for item in result['workflow_conflicts'][0]['statuses']
             for source in item['sources']}
    assert sources['current.eml']['citation']['quote']=='RFI 42 status: OPEN.'
    assert 'citation' not in sources['archive.eml']
    assert db.all('SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


@pytest.mark.parametrize(('question','expected'),[
    ('How many RFIs, Submittals, and Emails are in this run?',('RFI','SUBMITTAL','EMAIL')),
    ('What is the number of requests for information?',('RFI',)),
    ('Count the e-mails in the project.',('EMAIL',)),
    ('How many open RFIs?',()),
    ('How many RFI documents are there?',()),
    ('How many RFIs mention concrete?',()),
    ('How many RFI 42 responses are there?',()),
])
def test_workflow_inventory_question_boundary(question,expected):
    assert requested_workflow_counts(question)==expected
    assert requires_workflow_inventory(question) is bool(expected)


@pytest.mark.parametrize(('question','expected'),[
    ('List all RFIs, Submittals, and Emails in this run.',('RFI','SUBMITTAL','EMAIL')),
    ('Show me the e-mails.',('EMAIL',)),
    ('Which RFIs and Submittals are in this project?',('RFI','SUBMITTAL')),
    ('What Emails are included in the analysis?',('EMAIL',)),
    ('List open RFIs.',()),
    ('List RFI documents.',()),
    ('List RFI 42 responses.',()),
    ('List RFIs mentioning concrete.',()),
])
def test_workflow_inventory_list_question_boundary(question,expected):
    assert requested_workflow_list(question)==expected
    assert requires_workflow_inventory(question) is bool(expected)


def test_workflow_inventory_answer_skips_retrieval_and_live_provider(
        client, project, tmp_path, monkeypatch):
    run=_evidence(client,project,['RFI 42 question.'])
    index=build_workflow_index([
        {'document_id':'MAIL','name':'question.eml','summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[
                {'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':None}]}},
        {'document_id':'RESPONSE','name':'response.pdf','summary':{
            'document_type':'RFI_RESPONSE','workflow_contexts':[
                {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]}},
        {'document_id':'REFERENCE','name':'minutes.txt','summary':{
            'document_type':'OTHER','workflow_references':[
                {'workflow_type':'RFI','identifier':'99'}]}},
        {'document_id':'SUBMITTAL','name':'submittal.pdf','summary':{
            'document_type':'SUBMITTAL','workflow_contexts':[
                {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL',
                 'status':'PENDING'}]}},
        {'document_id':'EMAIL','name':'coordination.eml','summary':{'document_type':'EMAIL'}},
    ])
    requests=[]
    gateway=Gateway(_live_settings(tmp_path),client.app.state.db,
                    httpx.Client(transport=httpx.MockTransport(
                        lambda request:(requests.append(request),httpx.Response(500))[1])))
    service=ProjectQuestions(client.app.state.db,gateway)
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('inventory answer read evidence'))

    result=service.ask(
        run,'How many RFIs, Submittals, and Emails are in this run?',index)

    assert result['status']=='ANSWERED' and result['answer_basis']=='WORKFLOW_INDEX'
    assert result['answer']==('The complete workflow index for the selected analysis run contains '
                              '2 RFI identifiers, 1 Submittal identifier, and 2 Email files.')
    assert result['workflow_counts']==[
        {'kind':'RFI','count':2},{'kind':'SUBMITTAL','count':1},{'kind':'EMAIL','count':2}]
    assert result['retrieved_count']==0 and result['citations']==[]
    listed=service.ask(run,'List all RFIs, Submittals, and Emails in this run.',index)
    assert listed['answer']==(
        'The complete workflow index for the selected analysis run contains:\n'
        'RFI identifiers (2): 42, 99.\n'
        'Submittal identifier (1): 23-01.\n'
        'Email files (2): coordination.eml, question.eml.')
    assert listed['workflow_inventory']==[
        {'kind':'RFI','total':2,'values':['42','99'],'truncated':False},
        {'kind':'SUBMITTAL','total':1,'values':['23-01'],'truncated':False},
        {'kind':'EMAIL','total':2,'values':['coordination.eml','question.eml'],'truncated':False},
    ]
    large=build_workflow_index([
        {'document_id':f'RFI-{number}','name':f'rfi-{number}.txt','summary':{
            'document_type':'RFI_QUESTION','workflow_contexts':[
                {'workflow_type':'RFI','identifier':str(number),'role':'QUESTION','status':None}]}}
        for number in range(1,56)])
    large_result=service.ask(run,'List all RFIs in this run.',large)
    assert large_result['workflow_inventory'][0]['total']==55
    assert len(large_result['workflow_inventory'][0]['values'])==50
    assert large_result['workflow_inventory'][0]['truncated'] is True
    assert '55; first 50 shown' in large_result['answer']
    assert requests==[]
    assert client.app.state.db.all(
        'SELECT id FROM model_calls WHERE run_id=?',(run['id'],))==[]


def test_question_api_loads_complete_workflow_index_for_inventory_count(
        client, project, monkeypatch):
    run=_source_evidence(client,project,[
        ('question.eml',['RFI 42 question.']),('response.pdf',['RFI 42 response.']),
        ('submittal.pdf',['Submittal 23-01 pending.']),('coordination.eml',['Coordination.']),
    ])
    db=client.app.state.db
    documents={row['name']:row['id'] for row in db.all(
        'SELECT id,name FROM documents WHERE project_id=?',(project['id'],))}
    summaries={
        'question.eml':{'document_type':'EMAIL','workflow_contexts':[
            {'workflow_type':'RFI','identifier':'42','role':'QUESTION','status':None}]},
        'response.pdf':{'document_type':'RFI_RESPONSE','workflow_contexts':[
            {'workflow_type':'RFI','identifier':'42','role':'RESPONSE','status':'ANSWERED'}]},
        'submittal.pdf':{'document_type':'SUBMITTAL','workflow_contexts':[
            {'workflow_type':'SUBMITTAL','identifier':'23-01','role':'SUBMITTAL','status':'PENDING'}]},
        'coordination.eml':{'document_type':'EMAIL'},
    }
    with db.connect(True) as connection:
        connection.executemany('INSERT INTO document_results VALUES(?,?,?,?)',[
            (run['id'],documents[name],'SUCCESS',json.dumps(summary))
            for name,summary in summaries.items()])
    monkeypatch.setattr('app.questions.retrieve_evidence',
                        lambda *_args,**_kwargs:pytest.fail('inventory answer read evidence'))

    response=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'How many RFIs, Submittals, and Emails are in this run?'})

    assert response.status_code==200
    result=response.json()
    assert result['workflow_counts']==[
        {'kind':'RFI','count':1},{'kind':'SUBMITTAL','count':1},{'kind':'EMAIL','count':2}]
    assert result['retrieved_count']==0 and result['answer_basis']=='WORKFLOW_INDEX'
    listed=client.post(f'/api/projects/{project["id"]}/questions',json={
        'run_id':run['id'],'question':'List all RFIs, Submittals, and Emails in this run.'})
    assert listed.status_code==200
    assert listed.json()['workflow_inventory']==[
        {'kind':'RFI','total':1,'values':['42'],'truncated':False},
        {'kind':'SUBMITTAL','total':1,'values':['23-01'],'truncated':False},
        {'kind':'EMAIL','total':2,'values':['coordination.eml','question.eml'],'truncated':False},
    ]
