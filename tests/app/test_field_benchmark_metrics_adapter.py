import hashlib
import json
import subprocess
import sys
from copy import deepcopy

import pytest
import httpx

from app.reference_text_profiles import profile
from scripts import field_benchmark_metrics as metrics


def _report():
    route = profile('FLASH_NONE')
    base = {
        'report_version': 'reference-live-smoke-3', 'evaluation_id': 'EVAL-SYN',
        'project_id': 'PROJECT-SYN', 'run_id': 'RUN-SYN', 'snapshot_id': 'SNAP-SYN',
        'selector_version': 'literal-page-selector-9', 'profile': route,
        'policy': {'automatic_retries': 0, 'human_adjudications_written': False,
                   'source_text_in_report': False, 'private_reasoning_saved': False,
                   'supplement_count_semantics': 'REQUESTED_AND_ACCEPTED_SEPARATE_LEGACY_ALIASES_ACCEPTED'},
        'unresolved_calls_at_completion': 0, 'items': [],
    }
    def complete(question_id, *, decisions, saved, current, replayed=False, terminal=False):
        return {'question_id': question_id, 'item_id': 'ITEM-' + question_id, 'state': 'COMPLETE',
                'elapsed_seconds': 1.25, 'already_terminal': terminal, 'replayed': replayed,
                'receipt_scope': 'COMPLETE_CHAIN', 'receipt_count': decisions,
                'model_decisions': decisions, 'model_call_count': saved,
                'all_receipts_cached': saved == 0,
                'model_call_count_exact': True, 'model_call_count_scope': 'SAVED_EXECUTION',
                'requested_supplement_rounds': 1, 'requested_supplement_requests': 1,
                'accepted_supplement_rounds': 1, 'accepted_supplement_requests': 1,
                'new_calls_this_run': current, 'new_calls_this_run_exact': True,
                'question': 'sealed synthetic question text must not be copied'}
    base['items'] = [
        complete('Q-FRESH', decisions=2, saved=2, current=2),
        complete('Q-CACHED', decisions=2, saved=0, current=0),
        complete('Q-REPLAY', decisions=1, saved=1, current=0, replayed=True, terminal=True),
        complete('Q-MIXED', decisions=2, saved=1, current=1),
        {'question_id': 'Q-FAIL', 'item_id': 'ITEM-Q-FAIL', 'state': 'FAILED',
         'elapsed_seconds': 2, 'already_terminal': False, 'replayed': False,
         'receipt_scope': 'TERMINAL_ONLY', 'receipt_count': 1, 'model_call_count': None,
         'model_call_count_exact': False, 'model_call_count_scope': 'SAVED_EXECUTION',
         'terminal_round': 1, 'terminal_receipt_cached': False,
         'terminal_new_call_lower_bound': 1, 'new_calls_this_run': None,
         'new_calls_this_run_exact': False, 'new_calls_this_run_lower_bound': 1,
         'failure_code': 'MODEL_OUTPUT_REJECTED'},
    ]
    return base


def _bound_template(report):
    raw = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
    return raw, metrics.review_template_from_smoke_bytes(raw, report)


def _reviewed(template):
    sidecar = deepcopy(template)
    for review in sidecar['reviews']:
        review['answerability'] = 'ANSWERABLE'
        review['judgment'] = 'UNREVIEWED'
    sidecar['reviews'][0]['judgment'] = 'CORRECT_COMPLETE'
    sidecar['reviews'][0]['human_minutes'] = 2
    return sidecar


def test_smoke3_template_binds_original_bytes_and_contains_only_review_placeholders(monkeypatch):
    import app.db
    monkeypatch.setattr(app.db, 'Database', lambda *args, **kwargs: pytest.fail('adapter must not open a database'))
    report = _report()
    raw, template = _bound_template(report)
    assert template['review_version'] == metrics.REVIEW_VERSION
    assert template['source_binding']['report_sha256'] == hashlib.sha256(raw).hexdigest()
    assert [review['answerability'] for review in template['reviews']] == [None] * 5
    assert [review['judgment'] for review in template['reviews']] == ['UNREVIEWED'] * 5
    assert all(review['human_minutes'] is None for review in template['reviews'])
    assert 'review_complete' not in template
    assert 'sealed synthetic question text' not in json.dumps(template)


def test_smoke3_bytes_api_rejects_mismatched_objects_and_non_strict_json():
    report = _report(); raw, _ = _bound_template(report)
    changed = deepcopy(report); changed['run_id'] = 'DIFFERENT'
    with pytest.raises(ValueError, match='exactly match'):
        metrics.review_template_from_smoke_bytes(raw, changed)
    for invalid in (b'{"x":1,"x":2}', b'{"x":NaN}'):
        with pytest.raises(ValueError):
            metrics.review_template_from_smoke_bytes(invalid, {})
    with pytest.raises(ValueError): metrics._strict_json(b'{"x":1e999}')


def test_smoke3_adapter_separates_saved_current_replay_mixed_and_terminal_unknown():
    raw, template = _bound_template(_report())
    adapted = metrics.adapt_smoke3_bytes(raw, _report(), _reviewed(template))
    records = adapted['records']
    assert [row['execution_mode'] for row in records] == ['FRESH', 'CACHED', 'REPLAY', 'MIXED', 'UNKNOWN']
    assert [row['cached'] for row in records] == [False, True, None, None, None]
    assert records[-1].get('model_decisions') is None
    assert adapted['current_execution']['current_model_calls'] == {
        'observed_question_count': 4, 'unknown_question_count': 1,
        'known_zero_question_count': 2, 'known_nonzero_question_count': 2,
        'observed_total': 3, 'total': None, 'mean_per_observed_question': .75,
    }
    assert adapted['source_observations']['terminal_new_call_lower_bounds'] == [{
        'question_id': 'Q-FAIL', 'item_id': 'ITEM-Q-FAIL', 'terminal_new_call_lower_bound': 1,
    }]
    assert adapted['current_execution']['items'][0] == {
        'question_id': 'Q-FRESH', 'item_id': 'ITEM-Q-FRESH', 'current_model_calls': 2,
    }
    assert adapted['observation_scopes']['elapsed_seconds'] == 'RUNNER_PREVIEW_EXECUTE_TO_TERMINAL_CHECK'
    assert adapted['review_authentication'] == 'CALLER_SUPPLIED_NOT_AUTHENTICATED'
    assert 'sealed synthetic question text' not in json.dumps(adapted)


def test_smoke3_adapter_accepts_replay_that_became_terminal_during_preview():
    report = _report(); report['items'][2]['already_terminal'] = False
    raw, template = _bound_template(report)
    adapted = metrics.adapt_smoke3_bytes(raw, report, _reviewed(template))
    assert adapted['records'][2]['execution_mode'] == 'REPLAY'


@pytest.mark.parametrize('change', ['sha', 'identity', 'missing', 'extra', 'reordered'])
def test_smoke3_adapter_rejects_sidecar_byte_identity_and_coverage_changes(change):
    report = _report(); raw, template = _bound_template(report); sidecar = _reviewed(template)
    if change == 'sha': sidecar['source_binding']['report_sha256'] = '0' * 64
    elif change == 'identity': sidecar['source_binding']['identity']['run_id'] = 'OTHER'
    elif change == 'missing': sidecar['reviews'].pop()
    elif change == 'extra': sidecar['reviews'].append(deepcopy(sidecar['reviews'][0]))
    else: sidecar['reviews'].reverse()
    with pytest.raises(ValueError): metrics.adapt_smoke3_bytes(raw, report, sidecar)


@pytest.mark.parametrize('field,value', [
    ('new_calls_this_run_exact', False), ('new_calls_this_run', 3),
    ('already_terminal', True), ('requested_supplement_requests', True),
])
def test_smoke3_adapter_rejects_current_and_complete_chain_contradictions(field, value):
    report = _report(); report['items'][0][field] = value; raw, template = _bound_template(report)
    with pytest.raises(ValueError): metrics.adapt_smoke3_bytes(raw, report, _reviewed(template))


@pytest.mark.parametrize('change', ['duplicate_question', 'duplicate_item', 'complete_terminal_only', 'terminal_has_counts'])
def test_smoke3_adapter_rejects_producer_identity_and_terminal_only_shape(change):
    report = _report()
    if change == 'duplicate_question': report['items'][1]['question_id'] = report['items'][0]['question_id']
    elif change == 'duplicate_item': report['items'][1]['item_id'] = report['items'][0]['item_id']
    elif change == 'complete_terminal_only': report['items'][0]['receipt_scope'] = 'TERMINAL_ONLY'
    else: report['items'][-1]['model_decisions'] = 1
    raw = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
    if change in ('duplicate_question', 'duplicate_item'):
        with pytest.raises(ValueError): metrics.review_template_from_smoke_bytes(raw, report)
    else:
        _, template = _bound_template(report)
        with pytest.raises(ValueError): metrics.adapt_smoke3_bytes(raw, report, _reviewed(template))


@pytest.mark.parametrize('change', ['empty_evaluation', 'three_decisions_zero_supplement', 'integer_terminal_cached'])
def test_smoke3_adapter_rejects_sensitive_producer_shapes(change):
    report = _report()
    if change == 'empty_evaluation': report['evaluation_id'] = ''
    elif change == 'three_decisions_zero_supplement':
        item = report['items'][0]
        item.update(receipt_count=3, model_decisions=3, model_call_count=3, new_calls_this_run=3,
                    requested_supplement_rounds=0, requested_supplement_requests=0,
                    accepted_supplement_rounds=0, accepted_supplement_requests=0)
    else: report['items'][-1]['terminal_receipt_cached'] = 0
    raw = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
    if change == 'empty_evaluation':
        with pytest.raises(ValueError): metrics.review_template_from_smoke_bytes(raw, report)
    else:
        _, template = _bound_template(report)
        with pytest.raises(ValueError): metrics.adapt_smoke3_bytes(raw, report, _reviewed(template))


def test_smoke3_cli_writes_template_then_adapter_without_overwriting_inputs(tmp_path):
    report = _report(); smoke = tmp_path / 'smoke.json'; template = tmp_path / 'template.json'; output = tmp_path / 'adapted.json'
    smoke.write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
    command = [sys.executable, 'scripts/field_benchmark_metrics.py', str(smoke), '--smoke3-review-template', '--output', str(template)]
    assert subprocess.run(command, capture_output=True, text=True).returncode == 0
    reviewed = _reviewed(json.loads(template.read_text(encoding='utf-8')))
    template.write_text(json.dumps(reviewed), encoding='utf-8')
    command = [sys.executable, 'scripts/field_benchmark_metrics.py', str(smoke), '--smoke3-review', str(template), '--output', str(output)]
    assert subprocess.run(command, capture_output=True, text=True).returncode == 0
    assert json.loads(output.read_text(encoding='utf-8'))['adapter_version'] == metrics.ADAPTER_VERSION
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode != 0 and 'new path' in rejected.stderr


def test_real_mocktransport_runner_complete_failure_preserves_saved_terminal_observation():
    from scripts import reference_live_smoke as smoke
    from tests.app.test_reference_live_smoke_failure_chain import _chain, _executed
    from tests.app.test_reference_live_smoke_v9 import _fixture, _handler

    chain = _chain(2, (False, False))
    calls, original = _handler(failure=True, receipt_change=chain['execution_receipts'][-1])
    def handler(request):
        response = original(request)
        if request.url.path.endswith('/execute'):
            body = response.json()
            body.update(_executed(chain, replayed=body['replayed']))
            return httpx.Response(200, json=body)
        return response
    with httpx.Client(base_url='http://synthetic', transport=httpx.MockTransport(handler)) as client:
        report = smoke.run(client, _fixture(), ['Q1'], confirmed=True,
                           profile_id='FLASH_NONE', selector_version=smoke.V9)
    raw = json.dumps(report, ensure_ascii=False).encode('utf-8')
    template = metrics.review_template_from_smoke_bytes(raw, report)
    for review in template['reviews']:
        review['answerability'] = 'ANSWERABLE'; review['judgment'] = 'UNREVIEWED'
    adapted = metrics.adapt_smoke3_bytes(raw, report, template)
    item = report['items'][0]
    assert calls and adapted['records'][0]['execution_mode'] == 'FRESH'
    assert adapted['source_observations']['terminal_new_call_lower_bounds'] == [{
        'question_id': item['question_id'], 'item_id': item['item_id'],
        'terminal_new_call_lower_bound': 1,
    }]


@pytest.mark.parametrize(('saved','terminal_cached','terminal_lower','current'), [
    (0, False, 1, 0), (1, False, 1, 2), (2, True, 0, 2),
])
def test_complete_failure_rejects_inconsistent_saved_terminal_and_current_counts(
        saved, terminal_cached, terminal_lower, current):
    report = _report(); item = report['items'][0]
    item.update(state='FAILED', receipt_count=2, model_decisions=2, model_call_count=saved,
                all_receipts_cached=saved == 0, new_calls_this_run=current,
                requested_supplement_rounds=1, requested_supplement_requests=1,
                accepted_supplement_rounds=1, accepted_supplement_requests=1,
                terminal_round=2, terminal_receipt_cached=terminal_cached,
                terminal_new_call_lower_bound=terminal_lower)
    raw, template = _bound_template(report)
    with pytest.raises(ValueError): metrics.adapt_smoke3_bytes(raw, report, _reviewed(template))
