"""Project-file Q&A stays run-scoped, evidence-grounded, and budgeted."""
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from app.db import BudgetError, Database
from app.gateway import Gateway, InvalidModelOutput
from app.questions import ProjectQuestions, expanded_query_terms, retrieve_evidence
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
    response_data = {'status': 'ANSWERED', 'answer': 'Use 2 inch Type L copper.', 'citations': [{
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

    assert result['status'] == 'ANSWERED' and result['answer'] == 'Use 2 inch Type L copper.'
    assert result['citations'][0]['evidence_id'] == 'EV-QA-1'
    assert result['citations'][0]['start'] == 0
    assert recovered['answer'] == result['answer'] and recovered['cached'] is True
    assert len(requests) == 1
    assert 'Domestic water service pipe' not in requests[0]['messages'][0]['content']
    assert 'Domestic water service pipe' in requests[0]['messages'][1]['content']
    call = db.one('SELECT task_key,state FROM model_calls WHERE run_id=?', (run['id'],))
    assert call['task_key'].startswith('answer:') and call['state'] == 'SETTLED'


def test_live_answer_rejects_a_quote_not_in_supplied_evidence(client, project, tmp_path):
    run = _evidence(client, project, ['Domestic water service pipe shall be 2 inch Type L copper.'])
    db = client.app.state.db
    bad = {'status': 'ANSWERED', 'answer': 'Use PVC.', 'citations': [
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
