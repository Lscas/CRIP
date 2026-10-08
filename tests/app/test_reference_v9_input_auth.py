"""V9 input provenance, persistence and failure authentication; synthetic only."""
from copy import deepcopy
import hashlib
import json

import pytest
from jsonschema import ValidationError

from app.db import DomainError, dumps
from app.evidence_loop import ProjectEvidenceLoop
from app.gateway import InvalidModelOutput
from app.reference_layout_input import (
    authenticate_receipt_sources, initial_manifest_sha256, navigation_identity,
    ordered_manifest_sha256, prompt_contract,
)
from app.reference_text_profiles import profile
from contracts.runtime_rules import validate_schema
from .test_layout_binding import base
from .test_reference_cases import _followup_result, _result
from .test_reference_profile_comparisons import _channel
from .test_reference_results import _saved_run

V9 = 'literal-page-selector-9'
QUESTION = 'What approved color applies?'


def _receipt(rows=None):
    rows = base() if rows is None else rows
    identity = navigation_identity(rows)
    inputs = [{'evidence_id': row['evidence_id'],
               'text_sha256': hashlib.sha256(row['raw_text'].encode()).hexdigest()} for row in rows]
    receipt = {
        'receipt_version': 'reference-model-input-receipt-3', 'model_call_id': 'CALL-' + '1' * 32,
        'round': 1, 'request_hash': '2' * 64, 'prompt_contract_hash': prompt_contract()[1],
        'question_hash': '3' * 64, 'provider': 'deepseek', 'model': 'deepseek-flash',
        'api_protocol': 'chat_completions', 'structured_output_mode': 'json_object',
        'inference_mode': 'disabled', 'cached': False, 'source_text_included': False,
        'prompt_content_included': False, 'chain_of_thought_included': False,
        'evidence_count': len(rows), 'evidence_inputs': inputs, 'visual_inputs': [],
        'system_text_bytes': len(prompt_contract()[0].encode()), 'user_text_bytes': 100,
        'image_bytes': 0, 'request_upper_bound_bytes': 100_000, 'max_output_tokens': 2600,
        'selector_version': V9, 'context_policy': 'COMPLETE_SELECTED_SCOPE_WITH_LAYOUT_BINDING_V1',
        'source_text_clipped': False, 'ordered_evidence_manifest_sha256': ordered_manifest_sha256(inputs, identity),
        'source_text_bytes': sum(len(row['raw_text'].encode()) for row in rows),
        'initial_evidence_manifest_sha256': '4' * 64, 'profile_neutral_input_sha256': '5' * 64,
        **identity,
    }
    return receipt, {row['evidence_id']: row for row in rows}


def _cannot(*_):
    return {'status': 'CANNOT_ANSWER', 'reason_code': 'UNSUPPORTED_TASK',
            'missing_facts': ['Synthetic source does not authorize this answer.'], 'requests': [],
            'answer': {'claims': [], 'calculations': [], 'coverage': []}}


def _real_result(client, project):
    db, run, document, _ = _saved_run(client, project, 'REFERENCE_QA')
    calls = _channel(client, _cannot)
    value = ProjectEvidenceLoop(db, client.app.state.gateway).ask(
        run, QUESTION, selector_version=V9, route=profile('FLASH_NONE'))
    return db, run, document, calls, value


def test_schema_and_source_identity_accept_complete_v9():
    receipt, sources = _receipt()
    validate_schema('reference-model-input-receipt', receipt)
    authenticate_receipt_sources(receipt, sources)
    # Input dictionary key order is not an input-content difference.
    inputs = [dict(reversed(list(item.items()))) for item in receipt['evidence_inputs']]
    identity = navigation_identity(list(sources.values()))
    assert ordered_manifest_sha256(inputs, dict(reversed(list(identity.items())))) == receipt['ordered_evidence_manifest_sha256']


def test_receipt3_prompt_assets_are_frozen_not_rewritten_in_place():
    # A changed contract requires a new receipt/prompt version and a retained
    # receipt3 reconstruction path, not an update of this historical golden.
    text, digest = prompt_contract()
    assert digest == '5a28a492d2bf6553f8fb6518132f23809e4ae846ea328b464efdf20707df732e'
    assert len(text.encode('utf-8')) == 10456


@pytest.mark.parametrize('field', [
    'layout_navigation_version', 'layout_navigation_sha256', 'layout_navigation_bytes',
    'initial_evidence_manifest_sha256', 'profile_neutral_input_sha256',
])
def test_v3_schema_requires_new_fields(field):
    receipt, _ = _receipt()
    receipt.pop(field)
    with pytest.raises(ValidationError):
        validate_schema('reference-model-input-receipt', receipt)


@pytest.mark.parametrize('mutation', [
    'layout_sha256', 'layout_bytes', 'selector', 'context', 'vision', 'image_bytes', 'provider',
])
def test_v3_schema_rejects_old_layout_and_non_named_transport(mutation):
    receipt, _ = _receipt()
    if mutation.startswith('layout_'):
        receipt['evidence_inputs'][0][mutation] = '0' * 64 if mutation.endswith('sha256') else 1
    elif mutation == 'vision':
        receipt['visual_inputs'] = [{'region_id': 'V', 'image_sha256': '0' * 64, 'image_bytes': 1}]
    else:
        key, value = {'selector': ('selector_version', 'literal-page-selector-8'),
                      'context': ('context_policy', 'COMPLETE_SELECTED_SCOPE_V1'),
                      'image_bytes': ('image_bytes', 1), 'provider': ('provider', 'mock')}[mutation]
        receipt[key] = value
    with pytest.raises(ValidationError):
        validate_schema('reference-model-input-receipt', receipt)


@pytest.mark.parametrize('field', [
    'layout_navigation_sha256', 'layout_navigation_bytes', 'ordered_evidence_manifest_sha256',
    'source_text_bytes', 'system_text_bytes', 'prompt_contract_hash', 'evidence_count', 'initial_evidence_manifest_sha256',
])
def test_source_auth_rejects_receipt_tampering(field):
    receipt, sources = _receipt()
    receipt[field] = -1 if isinstance(receipt[field], int) else ('invalid' if field == 'initial_evidence_manifest_sha256' else '0' * 64)
    with pytest.raises(DomainError) as rejected:
        authenticate_receipt_sources(receipt, sources)
    assert rejected.value.code == 409


@pytest.mark.parametrize('mutation', ['text', 'method', 'page', 'document', 'map', 'missing', 'duplicate', 'reorder'])
def test_source_auth_rebuilds_actual_ordered_navigation(mutation):
    receipt, sources = _receipt()
    if mutation == 'text': sources['A']['raw_text'] = 'Z 511'
    elif mutation == 'method': sources['A']['extraction_method'] = 'OCR'
    elif mutation == 'page': sources['A']['locator']['page_number'] = 2
    elif mutation == 'document': sources['A']['document_id'] = 'OTHER'
    elif mutation == 'map': sources['A']['text_map'] = []
    elif mutation == 'missing': sources.pop('A')
    elif mutation == 'duplicate': receipt['evidence_inputs'][1] = deepcopy(receipt['evidence_inputs'][0])
    else:
        receipt['evidence_inputs'].reverse()
        # Even a recomputed manifest cannot authenticate the old E mapping.
        receipt['ordered_evidence_manifest_sha256'] = ordered_manifest_sha256(
            receipt['evidence_inputs'], navigation_identity(base()))
    with pytest.raises(DomainError):
        authenticate_receipt_sources(receipt, sources)


def test_nonprojected_bbox_change_is_not_falsely_claimed_as_authenticated():
    receipt, sources = _receipt()
    sources['A']['text_map'][0]['bbox'][0] += 0.01
    authenticate_receipt_sources(receipt, sources)


@pytest.mark.parametrize('mutation', ['selection', 'document', 'page', 'locator', 'groups', 'conflicts'])
def test_initial_manifest_commits_unbound_scope_not_only_navigation(mutation):
    rows = base()
    for row in rows: row['extraction_method'] = 'OCR'
    original_navigation = navigation_identity(rows)
    selection = 'selection'; groups = [{'scope': 'original'}]; conflicts = []
    original = initial_manifest_sha256(selection, rows, groups, conflicts)
    if mutation == 'selection': selection = 'changed'
    elif mutation == 'document': rows[0]['document_id'] = 'OTHER'
    elif mutation == 'page': rows[0]['locator']['page_number'] = 8
    elif mutation == 'locator': rows[0]['locator']['section'] = 'OTHER'
    elif mutation == 'groups': groups = [{'scope': 'changed'}]
    else: conflicts = ['Conflicting document conditions.']
    assert original_navigation == navigation_identity(rows)
    assert initial_manifest_sha256(selection, rows, groups, conflicts) != original


def test_real_v9_result_persists_and_authenticates_without_another_call(client, project):
    db, run, _, calls, value = _real_result(client, project)
    store = client.app.state.reference_results
    saved = store.save(run, QUESTION, value, 'deepseek', profile('FLASH_NONE')['text_model'])
    row = db.one('SELECT * FROM reference_results WHERE id=?', (saved['result_id'],))
    authenticated, receipts, _ = store.authenticate_saved_result(run, row)
    assert authenticated == value and receipts[0]['receipt_version'].endswith('-3')
    assert len(calls) == 1


@pytest.mark.parametrize('mutation', ['stripped_profile', 'global_profile', 'missing_commitment', 'source_text', 'source_map', 'mixed_chain'])
def test_real_v9_result_cannot_bypass_authentication(client, project, mutation):
    db, run, _, calls, value = _real_result(client, project)
    receipt = value['execution_receipts'][0]
    if mutation == 'stripped_profile': value.pop('execution_profile')
    elif mutation == 'global_profile':
        for key in ('profile_version', 'profile_id', 'max_output_tokens'): value['execution_profile'].pop(key)
    elif mutation == 'missing_commitment':
        db.execute('UPDATE model_calls SET reference_input_commitment_version=NULL,reference_input_commitment_sha256=NULL WHERE id=?', (receipt['model_call_id'],))
    elif mutation == 'mixed_chain':
        legacy = deepcopy(receipt)
        legacy.update(receipt_version='reference-model-input-receipt-2', selector_version='literal-page-selector-8')
        value['execution_receipts'].append(legacy)
    else:
        row = db.one('SELECT id,payload FROM evidence WHERE run_id=? LIMIT 1', (run['id'],))
        evidence = json.loads(row['payload'])
        if mutation == 'source_text': evidence['raw_text'] += ' Altered.'
        else:
            evidence['extraction_method'] = 'TEXT_LAYER'
            evidence['text_map'] = [{'start': 0, 'end': 1, 'bbox': [1, 1, 2, 2]}]
        db.execute('UPDATE evidence SET payload=? WHERE id=?', (dumps(evidence), row['id']))
    with pytest.raises(DomainError):
        client.app.state.reference_results.save(run, QUESTION, value, 'deepseek', profile('FLASH_NONE')['text_model'])
    assert db.one('SELECT COUNT(*) AS n FROM reference_results')['n'] == 0 and len(calls) == 1


def test_v9_settled_failure_reauthenticates_sources_and_frozen_selector(client, project):
    db, run, _, _ = _saved_run(client, project, 'REFERENCE_QA')
    calls = _channel(client, lambda *_: {'status': 'INVALID'})
    with pytest.raises(InvalidModelOutput) as rejected:
        ProjectEvidenceLoop(db, client.app.state.gateway).ask(run, QUESTION, selector_version=V9, route=profile('FLASH_NONE'))
    receipt = rejected.value.execution_receipt
    evaluation = {'project_id': project['id'], 'run_id': run['id'], 'snapshot_id': run['snapshot_id'], 'selector_version': V9}
    item = {'question_key': hashlib.sha256(QUESTION.encode()).hexdigest()}
    store = client.app.state.reference_evaluations
    store._authenticate_route_failure(evaluation, item, profile('FLASH_NONE'), receipt)
    with pytest.raises(DomainError, match='selector'):
        store._authenticate_route_failure({**evaluation, 'selector_version': 'literal-page-selector-8'}, item, profile('FLASH_NONE'), receipt)
    row = db.one('SELECT id,payload FROM evidence WHERE run_id=? LIMIT 1', (run['id'],))
    evidence = json.loads(row['payload']); evidence['raw_text'] += ' Altered.'
    db.execute('UPDATE evidence SET payload=? WHERE id=?', (dumps(evidence), row['id']))
    with pytest.raises(DomainError, match='hash'):
        store._authenticate_route_failure(evaluation, item, profile('FLASH_NONE'), receipt)
    assert len(calls) == 1


def test_v9_followup_proves_sent_attachment_without_human_approval(client, project):
    db, origin, document, _ = _saved_run(client, project, 'REFERENCE_QA')
    cases = client.app.state.reference_cases
    source = _result(client.app.state.reference_results, origin, QUESTION)
    case = cases.create(project['id'], origin['id'], QUESTION, result_id=source, attachments=[document['document_id']])
    run, _ = _followup_result(db, client, project, document, QUESTION)
    calls = _channel(client, _cannot)
    value = ProjectEvidenceLoop(db, client.app.state.gateway).ask(run, QUESTION, selector_version=V9, route=profile('FLASH_NONE'))
    saved = client.app.state.reference_results.save(run, QUESTION, value, 'deepseek', profile('FLASH_NONE')['text_model'])
    updated = cases.link_followup(case['case_id'], case['version'], run['id'], saved['result_id'], [document['document_id']], '')
    assert updated['status'] == 'IN_REVIEW'
    assert updated['followups'][0]['proof']['documents'][0]['text_input_rounds'] == [1]
    assert saved['review']['status'] == 'NOT_APPLICABLE'
    assert len(calls) == 1
