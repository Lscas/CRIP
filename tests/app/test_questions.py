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
    ProjectQuestions, expanded_query_terms, numeric_rfi_search_terms,
    requires_source_diversity, retrieve_evidence, search_query_terms, validate_answer_model,
)
from app.settings import ROOT, Settings
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
