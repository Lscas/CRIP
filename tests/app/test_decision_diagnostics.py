"""Offline safe diagnostics for rejected evidence-loop decisions."""
from dataclasses import replace
import json

import httpx
import pytest

from app.evidence_loop import (_compile_answer, _compile_citation,
                               _validate_answer_coverage, validate_evidence_decision)
from app.gateway import InvalidModelOutput
from app.project_qa_v2 import validate_answer_v2
from .test_evidence_loop import _provider_answer
from .test_reference_results import _saved_run


def _parts():
    return [
        {'part_ref': 'P1', 'text': 'first requested field'},
        {'part_ref': 'P2', 'text': 'second requested field'},
    ]


def _covered_answer():
    return {
        'claims': [
            {'text': 'First field is blue.', 'citations': [{'type': 'TEXT', 'evidence_ref': 'E1'}]},
            {'text': 'Second field is green.', 'citations': [{'type': 'TEXT', 'evidence_ref': 'E1'}]},
        ],
        'calculations': [],
        'coverage': [
            {'part_ref': 'P1', 'claim_indexes': [0], 'calculation_indexes': []},
            {'part_ref': 'P2', 'claim_indexes': [1], 'calculation_indexes': []},
        ],
    }


@pytest.mark.parametrize(('mutate', 'validator'), [
    (lambda value: value['coverage'].pop(), 'answer_part_missing'),
    (lambda value: value['coverage'][0].update(claim_indexes=[], calculation_indexes=[]),
     'answer_part_empty_mapping'),
    (lambda value: value['coverage'][0].update(claim_indexes=[0, 0]),
     'answer_part_duplicate_index'),
    (lambda value: value['coverage'][0].update(claim_indexes=[2]),
     'answer_part_invalid_claim_index'),
    (lambda value: value['coverage'][0].update(calculation_indexes=[0]),
     'answer_part_invalid_calculation_index'),
    (lambda value: value['coverage'][1].update(claim_indexes=[0]),
     'answer_part_unmapped_claim'),
])
def test_coverage_errors_have_closed_safe_validator_codes(mutate, validator):
    answer = _covered_answer()
    mutate(answer)

    with pytest.raises(ValueError) as raised:
        _validate_answer_coverage(answer, _parts())

    assert getattr(raised.value, 'validator', None) == validator
    assert str(raised.value).startswith('QA V3 ANSWER')


def test_compile_and_citation_errors_have_closed_safe_validator_codes():
    rows = [{'evidence_id': 'EV-1', 'raw_text': 'The field is blue.'}]

    with pytest.raises(ValueError) as empty:
        _compile_answer({'claims': [], 'calculations': [], 'coverage': []}, rows)
    assert getattr(empty.value, 'validator', None) == 'answer_empty'

    over_limit = {
        'claims': [
            {'text': f'Field is blue {index}.', 'citations': [{'type': 'TEXT', 'evidence_ref': 'E1'}]}
            for index in range(13)
        ],
        'calculations': [], 'coverage': [],
    }
    with pytest.raises(ValueError) as too_many:
        _compile_answer(over_limit, rows)
    assert getattr(too_many.value, 'validator', None) == 'answer_atomic_claim_limit'

    with pytest.raises(ValueError) as outside:
        _compile_citation({'type': 'TEXT', 'evidence_ref': 'E2'}, {'E1': rows[0]}, {})
    assert getattr(outside.value, 'validator', None) == 'citation_reference_outside'

    with pytest.raises(ValueError) as unavailable:
        _compile_citation({'type': 'TEXT', 'evidence_ref': 'E1'},
                          {'E1': {'evidence_id': 'EV-1', 'raw_text': ''}}, {})
    assert getattr(unavailable.value, 'validator', None) == 'citation_source_unavailable'


def test_coverage_distinguishes_unmapped_calculations():
    answer = _covered_answer()
    answer['calculations'] = [{}, {}]
    answer['coverage'][0]['calculation_indexes'] = [0]

    with pytest.raises(ValueError) as raised:
        _validate_answer_coverage(answer, _parts())

    assert getattr(raised.value, 'validator', None) == 'answer_part_unmapped_calculation'


@pytest.mark.parametrize('complete_context',[False,True])
def test_legacy_duplicate_complete_part_mapping_keeps_acceptance(complete_context):
    # A diagnostics-only patch cannot tighten a settled historical contract.
    answer=_covered_answer()
    answer['coverage'].append(dict(answer['coverage'][0]))
    decision={'status':'ANSWER','reason_code':'ENOUGH_EVIDENCE',
              'missing_facts':[],'requests':[],'answer':answer}
    rows=[{'evidence_id':'EV-1','raw_text':'First field is blue. Second field is green.'}]
    result=validate_evidence_decision(decision,rows,'Show both fields.',
                                      answer_parts=_parts(),complete_context=complete_context)
    assert result['answer']['status']=='ANSWERED'
    assert len(result['answer']['claims'])==2


def test_numeric_diagnostics_remain_structured_and_unchanged(client):
    rows = [{'evidence_id': 'EV-1', 'raw_text': 'Cited source says 7.'}]
    answer = {
        'status': 'ANSWERED', 'answer': 'The answer is 9.',
        'claims': [{'text': 'The answer is 9.', 'citations': [{
            'type': 'TEXT', 'evidence_id': 'EV-1', 'quote': 'Cited source says 7.'}]}],
        'missing': [], 'calculations': [],
    }
    with pytest.raises(ValueError) as raised:
        validate_answer_v2(answer, rows, 'What is the answer?')
    diagnostic = client.app.state.gateway._terminal_diagnostic(
        'PROJECT_EVIDENCE_DECISION', raised.value)
    assert diagnostic['validator'] == 'numeric_support'
    assert diagnostic['semantic_detail']['reason'] == 'CLAIM_NUMBER_UNSUPPORTED'


def test_plain_legacy_failure_keeps_its_existing_safe_category(client):
    diagnostic = client.app.state.gateway._terminal_diagnostic(
        'PROJECT_EVIDENCE_DECISION',
        ValueError('QA V3 ANSWER coverage has an empty part mapping'))
    assert diagnostic == {
        'kind': 'CONTRACT_ERROR', 'class': 'PROJECT_EVIDENCE_DECISION',
        'exception': 'ValueError', 'validator': 'answer_part_mapping',
    }


def test_gateway_settles_safe_local_code_without_response_or_retry(client, project):
    db, run, _, _ = _saved_run(client, project, 'REFERENCE_QA')
    gateway = client.app.state.gateway
    gateway.s = replace(
        gateway.s, provider='deepseek', live_enabled=True, api_key='synthetic-key',
        cheap_model='deepseek-flash', vision_enabled=False, structured_output_mode='json_object')
    rejected = _provider_answer()
    rejected['answer']['coverage'][0]['claim_indexes'] = []
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            'id': 'safe-local-contract-code',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(rejected)}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1},
        })

    gateway.client.close()
    gateway.client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = [{'evidence_id': 'EV-1', 'raw_text': 'The approved color is blue.'}]
    args = (run, 'What approved color applies?', rows, [], [], 0)
    with pytest.raises(InvalidModelOutput):
        gateway.evidence_decision_v3(*args)

    settled = db.one('SELECT state,response,error FROM model_calls WHERE run_id=?', (run['id'],))
    diagnostic = json.loads(settled['error'])
    assert settled['state'] == 'SETTLED_ERROR' and settled['response'] is None
    assert diagnostic['validator'] == 'answer_part_empty_mapping'
    assert set(diagnostic) == {'kind', 'class', 'exception', 'validator'}
    assert 'approved color' not in settled['error']

    with pytest.raises(InvalidModelOutput):
        gateway.evidence_decision_v3(*args)
    assert len(calls) == 1
    gateway.close()
