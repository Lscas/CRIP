"""Project-file Q&A stays run-scoped, evidence-grounded, and budgeted."""
from decimal import Decimal
import json
from pathlib import Path

import httpx
import pytest

from app.db import BudgetError
from app.gateway import Gateway, InvalidModelOutput
from app.questions import ProjectQuestions, retrieve_evidence
from app.settings import Settings
from .conftest import upload


def _evidence(client, project, rows):
    document = upload(client, project['id'], 'project-spec.txt', b'project source')
    run = client.app.state.runner.create(project['id'])
    db = client.app.state.db
    db.execute("UPDATE runs SET status='PARTIAL' WHERE id=?", (run['id'],))
    base = json.loads(Path('examples/evidence.json').read_text(encoding='utf-8'))[0]
    for index, text in enumerate(rows, 1):
        eid = f'EV-QA-{index}'
        payload = {**base, 'evidence_id': eid, 'tenant_id': 'local',
                   'project_id': project['id'], 'input_snapshot_id': run['snapshot_id'],
                   'document_id': document['document_id'], 'raw_text': text,
                   'locator': {**base['locator'], 'section': f'Section {index}'}}
        db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?)',
                   (run['id'] + ':' + eid, run['id'], project['id'], document['document_id'],
                    json.dumps(payload), 'EXTRACTED', None, ''))
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
