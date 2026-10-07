"""Pure decision boundaries; the separate input/Gateway suite authenticates PDFs."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.reference_projection_decision import (
    CONTRACT_VERSION, ProjectionDecisionError, prompt_contract, validate_projection_decision,
)


def _gap(part='P1', code='SOURCE_TEXT'):
    return {'part_ref': part, 'gap_code': code}


@pytest.fixture
def sample():
    bundle = SimpleNamespace(
        projection_input_sha256='a' * 64,
        required_parts=[{'part_ref': 'P1', 'text': 'Object?'}, {'part_ref': 'P2', 'text': 'Condition?'}],
        context={'projection_complete': True, 'source_conflicts': [], 'rows': [
            {'row_ref': 'X1', 'text': 'BRACKET A: IF EXPOSED ONLY', 'selectable': True},
            {'row_ref': 'X2', 'text': 'BRACKET B: AT EACH LEG', 'selectable': True}]})
    decision = {'contract_version': CONTRACT_VERSION, 'projection_input_sha256': 'a' * 64,
                'status': 'REVIEW_REQUIRED', 'reason_code': 'OBJECT_CONDITION_REVIEW_REQUIRED',
                'missing_facts': [], 'requests': [],
                'selections': [{'row_ref': 'X1', 'part_refs': ['P1', 'P2']}]}
    return bundle, decision


def test_selection_is_not_semantic_verification_or_answer(sample):
    bundle, decision = sample
    actual = validate_projection_decision(decision, bundle)
    assert actual == decision and actual is not decision
    assert not {'answer', 'claims', 'verification', 'confidence'} & actual.keys()
    actual['selections'][0]['part_refs'].clear()
    assert decision['selections'][0]['part_refs'] == ['P1', 'P2']


@pytest.mark.parametrize('change,code', [
    ('hash', 'input'), ('status', 'status'), ('unknown_row', 'row'),
    ('duplicate', 'duplicate'), ('unknown_part', 'part'), ('missing_part', 'coverage'),
    ('not_selectable', 'row'), ('incomplete', 'incomplete'), ('conflict', 'conflict'),
    ('missing_selection', 'status'), ('has_gap', 'status'), ('has_request', 'status'),
])
def test_review_rejects_invalid_source_mapping(sample, change, code):
    bundle, data = sample
    if change == 'hash': data['projection_input_sha256'] = 'b' * 64
    elif change == 'status': data['reason_code'] = 'MISSING_SOURCE_TEXT'
    elif change == 'unknown_row': data['selections'][0]['row_ref'] = 'X99'
    elif change == 'duplicate': data['selections'] *= 2
    elif change == 'unknown_part': data['selections'][0]['part_refs'] = ['P99']
    elif change == 'missing_part': data['selections'][0]['part_refs'] = ['P1']
    elif change == 'not_selectable': bundle.context['rows'][0]['selectable'] = False
    elif change == 'incomplete': bundle.context['projection_complete'] = False
    elif change == 'conflict': bundle.context['source_conflicts'] = ['Revision unresolved']
    elif change == 'missing_selection': data['selections'] = []
    elif change == 'has_gap': data['missing_facts'] = [_gap(code='OBJECT_CONDITION')]
    elif change == 'has_request': data['requests'] = [{'tool': 'SEARCH_TEXT', 'query': 'object'}]
    with pytest.raises(ProjectionDecisionError, match='projection_decision_' + code):
        validate_projection_decision(data, bundle)


@pytest.mark.parametrize('field,value', [
    ('answer', 'Install 2 anchors everywhere'), ('claims', []), ('confidence', 1),
    ('reasoning', 'A private chain'), ('status', 'ANSWER'),
])
def test_no_free_answer_or_hidden_reasoning_fields(sample, field, value):
    bundle, data = sample; data[field] = value
    with pytest.raises(ProjectionDecisionError, match='projection_decision_schema'):
        validate_projection_decision(data, bundle)


@pytest.mark.parametrize('field', ['text', 'quote', 'start', 'end', 'calculation'])
def test_rows_cannot_be_rewritten_or_sliced(sample, field):
    bundle, data = sample; data['selections'][0][field] = 'made up'
    with pytest.raises(ProjectionDecisionError, match='projection_decision_schema'):
        validate_projection_decision(data, bundle)


@pytest.mark.parametrize('status,reason,gap_code', [
    ('NEED_EVIDENCE', 'MISSING_IDENTIFIER', 'IDENTIFIER'),
    ('NEED_EVIDENCE', 'MISSING_SOURCE_TEXT', 'SOURCE_TEXT'),
    ('NEED_EVIDENCE', 'MISSING_SOURCE_TEXT', 'OBJECT_CONDITION'),
    ('NEED_USER_INPUT', 'MISSING_PROJECT_FILE', 'PROJECT_FILE'),
    ('NEED_USER_INPUT', 'UNREADABLE_PROJECT_SOURCE', 'READABLE_SOURCE'),
    ('NEED_USER_INPUT', 'UNREADABLE_PROJECT_SOURCE', 'PROJECTION_GEOMETRY'),
    ('CANNOT_ANSWER', 'SOURCE_PROJECTION_INCOMPLETE', 'PROJECTION_GEOMETRY'),
    ('CANNOT_ANSWER', 'CONFLICTING_SOURCES', 'SOURCE_CONFLICT'),
    ('CANNOT_ANSWER', 'UNSUPPORTED_TASK', 'UNSUPPORTED_TASK'),
    ('CANNOT_ANSWER', 'NO_NEW_EVIDENCE', 'LOCAL_RETRIEVAL_EXHAUSTED'),
])
def test_explicit_gap_actions(sample, status, reason, gap_code):
    bundle, data = sample
    data.update(status=status, reason_code=reason, selections=[], missing_facts=[_gap(code=gap_code)])
    if status == 'NEED_EVIDENCE': data['requests'] = [{'tool': 'FIND_IDENTIFIER', 'query': 'SHEET A-101'}]
    assert validate_projection_decision(data, bundle) == data
    invalid = deepcopy(data); invalid['missing_facts'] = []
    with pytest.raises(ProjectionDecisionError, match='projection_decision_gap'):
        validate_projection_decision(invalid, bundle)


@pytest.mark.parametrize('change,code', [
    ('duplicate', 'repeat'), ('repeat', 'repeat'), ('last_round', 'round_limit'),
    ('empty', 'request'), ('blank', 'request'), ('selection', 'status'),
    ('outside_tool', 'schema'), ('conflict', 'conflict'),
])
def test_supplement_bounds_are_local_not_model_discretion(sample, change, code):
    bundle, data = sample
    data.update(status='NEED_EVIDENCE', reason_code='MISSING_SOURCE_TEXT', selections=[],
                missing_facts=[_gap()], requests=[{'tool': 'SEARCH_TEXT', 'query': 'Detail A'}])
    kwargs = {}
    if change == 'duplicate': data['requests'].append({'tool': 'SEARCH_TEXT', 'query': 'DETAIL   A'})
    elif change == 'repeat': kwargs['request_history'] = [{'tool': 'SEARCH_TEXT', 'query': '  DETAIL A  '}]
    elif change == 'last_round': kwargs['remaining_decisions'] = 1
    elif change == 'empty': data['requests'] = []
    elif change == 'blank': data['requests'][0]['query'] = '  '
    elif change == 'selection': data['selections'] = [{'row_ref': 'X1', 'part_refs': ['P1']}]
    elif change == 'outside_tool': data['requests'][0]['tool'] = 'WEB_SEARCH'
    elif change == 'conflict': kwargs['source_conflicts'] = ['Conflicting revisions']
    with pytest.raises(ProjectionDecisionError, match='projection_decision_' + code):
        validate_projection_decision(data, bundle, **kwargs)


@pytest.mark.parametrize('gap', ['Install two anchors everywhere',
                               {'part_ref': 'P1', 'gap_code': 'Install two anchors everywhere'},
                               {'part_ref': 'P1', 'gap_code': 'SOURCE_TEXT', 'explanation': 'A hidden answer'}])
def test_missing_facts_cannot_smuggle_free_text_answers(sample, gap):
    bundle, data = sample
    data.update(status='CANNOT_ANSWER', reason_code='SOURCE_PROJECTION_INCOMPLETE',
                selections=[], missing_facts=[gap])
    with pytest.raises(ProjectionDecisionError, match='projection_decision_schema'):
        validate_projection_decision(data, bundle)


def test_gap_part_must_exist_and_identifier_must_be_explicit(sample):
    bundle, data = sample
    data.update(status='NEED_EVIDENCE', reason_code='MISSING_IDENTIFIER', selections=[],
                missing_facts=[_gap('P99')], requests=[{'tool': 'FIND_IDENTIFIER', 'query': 'SHEET A-101'}])
    with pytest.raises(ProjectionDecisionError, match='projection_decision_part'):
        validate_projection_decision(data, bundle)
    data['missing_facts'] = [_gap(code='IDENTIFIER')]; data['requests'][0]['query'] = 'zz'
    with pytest.raises(ProjectionDecisionError, match='projection_decision_request'):
        validate_projection_decision(data, bundle)


@pytest.mark.parametrize('status,reason,gap', [
    ('CANNOT_ANSWER', 'UNSUPPORTED_TASK', 'SOURCE_CONFLICT'),
    ('NEED_USER_INPUT', 'MISSING_PROJECT_FILE', 'SOURCE_TEXT'),
    ('CANNOT_ANSWER', 'SOURCE_PROJECTION_INCOMPLETE', 'PROJECT_FILE'),
])
def test_gap_reason_mismatch_cannot_drive_contradictory_handoff(sample, status, reason, gap):
    bundle, data = sample
    data.update(status=status, reason_code=reason, selections=[], missing_facts=[_gap(code=gap)])
    with pytest.raises(ProjectionDecisionError, match='projection_decision_gap'):
        validate_projection_decision(data, bundle)


def test_public_claim_limit_is_not_input_or_review_selection_limit(sample):
    bundle, data = sample
    bundle.context['rows'][0].update(text='CONDITION ' * 10000,
                                     projection_status='UNUSABLE', reason='CLAIM_TEXT_LIMIT')
    assert validate_projection_decision(data, bundle)['status'] == 'REVIEW_REQUIRED'


def test_prompt_is_separate_versioned_and_bound_to_schema():
    import hashlib
    system, schema, digest = prompt_contract()
    assert digest == hashlib.sha256(system.encode('utf-8')).hexdigest()
    assert schema['properties']['contract_version']['const'] == CONTRACT_VERSION
    assert 'ANSWER' not in schema['properties']['status']['enum']
    for marker in ('untrusted DATA', 'not instructions', 'WHOLE X rows', 'chain of thought',
                   'Do not repeat', 'Never return ANSWER'):
        assert marker in system


def test_review_packet_reauthenticates_pdf_and_contains_only_full_source_bindings(client, project):
    from .test_reference_projection_input import _input
    from app.reference_projection_decision import compile_projection_review
    from app.reference_projection_input import ProjectionInputError
    db, _run, documents, _selection, bundle = _input(client, project, [
        ('left.pdf', ['alpha BRACKET A IF EXPOSED ONLY']),
        ('right.pdf', ['alpha BRACKET B AT EACH LEG']),
    ])
    data = {'contract_version': CONTRACT_VERSION,
            'projection_input_sha256': bundle.projection_input_sha256,
            'status': 'REVIEW_REQUIRED', 'reason_code': 'OBJECT_CONDITION_REVIEW_REQUIRED',
            'missing_facts': [], 'requests': [],
            'selections': [{'row_ref': row['row_ref'], 'part_refs': ['P1']}
                           for row in bundle.context['rows']]}
    packet = compile_projection_review(data, bundle, db=db, uploads=client.app.state.uploads)
    assert packet['status'] == 'REVIEW_REQUIRED' and 'answer' not in packet
    assert not packet['verification']['object_condition_relations_verified']
    assert not packet['verification']['answer_completeness_verified']
    assert packet['verification']['source_projection_integrity']
    assert packet['verification']['part_selection_coverage_complete']
    assert len(packet['source_bindings']) == 2
    for binding, row in zip(packet['source_bindings'], bundle.context['rows']):
        assert binding['pdf_sha256'] == row['binding']['pdf_sha256']
        assert binding['row_text_sha256'] == row['text_sha256']
        assert binding['manifest_sha256'] in {m['manifest_sha256'] for m in bundle.manifests}
        assert binding['char_ids'] == row['char_ids']
        assert {'text', 'quote', 'answer', 'evidence_id'}.isdisjoint(binding)
    assert 'BRACKET A' not in str(packet)
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0
    document = db.one('SELECT * FROM documents WHERE id=?', (documents[0][0],))
    client.app.state.uploads.object_path(document).write_bytes(b'changed original source')
    with pytest.raises(ProjectionInputError):
        compile_projection_review(data, bundle, db=db, uploads=client.app.state.uploads)
