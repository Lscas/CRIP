"""End-to-end internal loop tests: synthetic PDFs and MockTransport only."""
from __future__ import annotations

import copy
from dataclasses import replace
import json

import httpx
import pytest

from app.db import DomainError
from app.evidence_loop import ProjectEvidenceLoop
from app.gateway import InvalidModelOutput
from app.reference_projection_decision import CONTRACT_VERSION
from app.reference_projection_loop_receipt import chain_sha256
from app.reference_text_profiles import profile
from .test_gateway_projection import _gateway
from .test_reference_projection_input import _run_with_pages

SELECTOR = 'literal-page-selector-10'
QUESTION = 'alpha?'


def _setup(client, project, tmp_path, responder, pages=None):
    db, run, _ = _run_with_pages(client, project, [('notes.pdf', pages or [
        'alpha initial note', 'bravo bracket IF exposed', 'charlie connector IF covered'])])
    sent = []

    def handler(request):
        body = json.loads(request.content)
        content = json.loads(body['messages'][1]['content'])
        sent.append(content)
        response = responder(content)
        return httpx.Response(200, json={'id': f'loop-{len(sent)}', 'choices': [
            {'finish_reason': 'stop', 'message': {'content': json.dumps(response)}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})

    gateway = _gateway(tmp_path, db, handler)
    return db, run, gateway, ProjectEvidenceLoop(db, gateway, client.app.state.uploads), sent


def _decision(content, query=None):
    return {
        'contract_version': CONTRACT_VERSION,
        'projection_input_sha256': content['projection_input_sha256'],
        'status': 'NEED_EVIDENCE' if query else 'REVIEW_REQUIRED',
        'reason_code': 'MISSING_SOURCE_TEXT' if query else 'OBJECT_CONDITION_REVIEW_REQUIRED',
        'missing_facts': [{'part_ref': 'P1', 'gap_code': 'SOURCE_TEXT'}] if query else [],
        'requests': [{'tool': 'SEARCH_TEXT', 'query': query}] if query else [],
        'selections': [] if query else [{'row_ref': content['projection_context']['rows'][-1]['row_ref'],
                                         'part_refs': ['P1']}],
    }


def _preview(loop, run):
    return loop.preview(run, QUESTION, SELECTOR, profile('FLASH_NONE'))['preview_proof']


def _ask(loop, run, proof):
    return loop.ask(run, QUESTION, SELECTOR, profile('FLASH_NONE'), preview_proof=proof)


def test_actual_three_decisions_append_and_replay_without_new_http(client, project, tmp_path):
    def respond(content):
        return _decision(content, {1: 'bravo', 2: 'charlie'}.get(content['round']))
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, respond)
    try:
        proof = _preview(loop, run)
        assert sent == []
        result = _ask(loop, run, proof)
        replay = _ask(ProjectEvidenceLoop(db, gateway, client.app.state.uploads), run, proof)
    finally:
        gateway.close()
    assert result['status'] == replay['status'] == 'REVIEW_REQUIRED'
    assert result['model_call_count'] == 3 and replay['model_call_count'] == 0
    assert result['supplement_round_count'] == 2
    assert result['accepted_supplement_request_count'] == 2
    assert len(sent) == db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 3
    assert [item['remaining_model_decisions'] for item in sent] == [3, 2, 1]
    assert [len(item['projection_context']['rows']) for item in sent] == [1, 2, 3]
    assert sent[2]['projection_context']['rows'][:2] == sent[1]['projection_context']['rows']
    assert sent[1]['projection_context']['rows'][:1] == sent[0]['projection_context']['rows']
    assert result['claims'] == [] and result['answer'] == ''
    assert result['review_packet']['verification']['object_condition_relations_verified'] is False
    assert all(item['receipt_version'] == 'reference-model-input-receipt-5'
               for item in result['execution_receipts'])
    assert [r['model_call_id'] for r in replay['execution_receipts']] == [
        r['model_call_id'] for r in result['execution_receipts']]


def test_no_new_pages_stops_locally_after_one_decision(client, project, tmp_path):
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, lambda body: _decision(body, 'alpha'))
    try:
        result = _ask(loop, run, _preview(loop, run))
    finally:
        gateway.close()
    assert result['status'] == 'CANNOT_ANSWER' and result['reason_code'] == 'NO_NEW_EVIDENCE'
    assert result['answer_basis'] == 'PROJECTION_LOCAL_RETRIEVAL_EXHAUSTED'
    assert result['model_call_count'] == len(sent) == 1
    assert len(result['execution_receipts']) == 1
    assert result['supplement_round_count'] == result['accepted_supplement_request_count'] == 1
    assert result['missing'] == [{'part_ref': 'P1', 'gap_code': 'LOCAL_RETRIEVAL_EXHAUSTED'}]


def test_no_new_reports_only_the_authenticated_missing_part(client, project, tmp_path):
    def respond(body):
        value = _decision(body, 'alpha')
        value['missing_facts'] = [{'part_ref': 'P2', 'gap_code': 'SOURCE_TEXT'}]
        return value
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, respond)
    question = 'What alpha, and what bravo?'
    try:
        proof = loop.preview(run, question, SELECTOR, profile('FLASH_NONE'))['preview_proof']
        result = loop.ask(run, question, SELECTOR, profile('FLASH_NONE'), preview_proof=proof)
    finally:
        gateway.close()
    assert result['missing'] == [{'part_ref': 'P2', 'gap_code': 'LOCAL_RETRIEVAL_EXHAUSTED'}]
    assert len(sent) == 1


def test_second_acquisition_without_new_page_does_not_pay_for_third_decision(client, project, tmp_path):
    db, run, gateway, loop, sent = _setup(client, project, tmp_path,
        lambda body: _decision(body, 'bravo' if body['round'] == 1 else 'alpha'))
    try:
        proof = _preview(loop, run)
        result = _ask(loop, run, proof)
        replay = _ask(loop, run, proof)
    finally:
        gateway.close()
    assert result['status'] == replay['status'] == 'CANNOT_ANSWER'
    assert result['reason_code'] == replay['reason_code'] == 'NO_NEW_EVIDENCE'
    assert result['supplement_round_count'] == 2
    assert len(sent) == db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 2
    assert replay['model_call_count'] == 0


def test_no_new_preserves_conflicts_discovered_by_last_local_selection(client, project, tmp_path, monkeypatch):
    import app.reference_projection_stage as module
    actual_select = module.select_pages
    def select(*args, **kwargs):
        selected = actual_select(*args, **kwargs)
        if args[2] == 'alpha' and kwargs.get('scope_question'):
            return replace(selected, source_conflicts=('SYNTHETIC_CONFLICT',))
        return selected
    monkeypatch.setattr(module, 'select_pages', select)
    db, run, gateway, loop, sent = _setup(client, project, tmp_path,
        lambda body: _decision(body, 'bravo' if body['round'] == 1 else 'alpha'))
    try:
        result = _ask(loop, run, _preview(loop, run))
    finally:
        gateway.close()
    assert result['status'] == 'CANNOT_ANSWER' and result['reason_code'] == 'NO_NEW_EVIDENCE'
    assert result['source_scope']['conflicts'] == ['SYNTHETIC_CONFLICT']
    assert len(sent) == 2


@pytest.mark.parametrize('status,reason,gap', [
    ('NEED_USER_INPUT', 'MISSING_PROJECT_FILE', 'PROJECT_FILE'),
    ('CANNOT_ANSWER', 'UNSUPPORTED_TASK', 'UNSUPPORTED_TASK'),
])
def test_nonreview_terminal_is_not_mislabelled_review(client, project, tmp_path, status, reason, gap):
    def respond(body):
        value = _decision(body)
        value.update(status=status, reason_code=reason, selections=[],
                     missing_facts=[{'part_ref': 'P1', 'gap_code': gap}])
        return value
    _db, run, gateway, loop, sent = _setup(client, project, tmp_path, respond)
    try:
        result = _ask(loop, run, _preview(loop, run))
    finally:
        gateway.close()
    assert result['status'] == status and result['answer_basis'] == 'PROJECTION_LOOP_TERMINAL'
    assert result['review_packet'] is None and len(sent) == 1


@pytest.mark.parametrize('terminal_round', [2, 3])
def test_later_contract_failure_replays_complete_chain_without_http(client, project, tmp_path, terminal_round):
    def respond(body):
        return {} if body['round'] == terminal_round else _decision(body, {1: 'bravo', 2: 'charlie'}[body['round']])
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, respond)
    try:
        proof = _preview(loop, run)
        for _ in range(2):
            with pytest.raises(InvalidModelOutput) as failure:
                _ask(loop, run, proof)
            chain = failure.value.failure_execution
            assert chain['failure_execution_version'] == 'projection-failure-execution-1'
            assert len(chain['execution_receipts']) == terminal_round
            assert chain['supplement_round_count'] == terminal_round - 1
    finally:
        gateway.close()
    assert len(sent) == db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == terminal_round


def test_restart_after_settlement_reuses_first_call(client, project, tmp_path, monkeypatch):
    import app.reference_projection_loop as module
    db, run, gateway, loop, sent = _setup(client, project, tmp_path,
        lambda body: _decision(body, 'bravo' if body['round'] == 1 else None))
    original = module.append_projection_stage
    try:
        proof = _preview(loop, run)
        def interrupted(*args, **kwargs):
            raise RuntimeError('synthetic interruption after durable settlement')
        monkeypatch.setattr(module, 'append_projection_stage', interrupted)
        with pytest.raises(RuntimeError, match='synthetic interruption'):
            _ask(loop, run, proof)
        assert len(sent) == 1
        monkeypatch.setattr(module, 'append_projection_stage', original)
        result = _ask(ProjectEvidenceLoop(db, gateway, client.app.state.uploads), run, proof)
    finally:
        gateway.close()
    assert result['status'] == 'REVIEW_REQUIRED' and result['model_call_count'] == 1
    assert result['execution_receipts'][0]['cached'] is True
    assert len(sent) == db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 2


@pytest.mark.parametrize('kind', ['missing', 'legacy', 'profile', 'initial', 'first_message'])
def test_invalid_preview_stops_before_any_http_or_reservation(client, project, tmp_path, kind):
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, _decision)
    proof = _preview(loop, run)
    if kind == 'missing': proof = None
    elif kind == 'legacy': proof['proof_version'] = 'reference-preview-proof-1'
    elif kind == 'profile': proof['profile_id'] = 'PRO'
    elif kind == 'initial': proof['initial_projection_input_sha256'] = '0' * 64
    else: proof['first_profile_neutral_input_sha256'] = '0' * 64
    try:
        with pytest.raises(DomainError, match='PREVIEW_STALE'):
            _ask(loop, run, proof)
    finally:
        gateway.close()
    assert sent == [] and db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0


def test_chain_identity_ignores_only_execution_cached_marker(client, project, tmp_path):
    _db, run, gateway, loop, sent = _setup(client, project, tmp_path, _decision)
    try:
        result = _ask(loop, run, _preview(loop, run))
    finally:
        gateway.close()
    chain = [{'receipt': result['execution_receipts'][0], 'decision': _decision(sent[0])}]
    changed = copy.deepcopy(chain)
    changed[0]['receipt']['cached'] = True
    assert chain_sha256(chain) == chain_sha256(changed)
    changed[0]['receipt'] = dict(reversed(list(changed[0]['receipt'].items())))
    changed[0]['decision'] = dict(reversed(list(changed[0]['decision'].items())))
    assert chain_sha256(chain) == chain_sha256(changed)
    changed[0]['receipt']['model_call_id'] = 'CALL-' + 'a' * 32
    assert chain_sha256(chain) != chain_sha256(changed)


def test_third_need_is_settled_failure_not_a_fourth_call(client, project, tmp_path):
    db, run, gateway, loop, sent = _setup(client, project, tmp_path,
        lambda body: _decision(body, {1: 'bravo', 2: 'charlie', 3: 'delta'}[body['round']]))
    try:
        with pytest.raises(InvalidModelOutput):
            _ask(loop, run, _preview(loop, run))
    finally:
        gateway.close()
    assert len(sent) == db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 3


def test_two_requests_per_round_can_acquire_four_new_pages(client, project, tmp_path):
    def respond(body):
        queries = {1: ['bravo', 'charlie'], 2: ['delta', 'echo']}.get(body['round'])
        value = _decision(body, queries[0] if queries else None)
        if queries:
            value['requests'] = [{'tool': 'SEARCH_TEXT', 'query': query} for query in queries]
        return value
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, respond,
        ['alpha initial', 'bravo bracket', 'charlie connector', 'delta wall', 'echo footing'])
    try:
        result = _ask(loop, run, _preview(loop, run))
    finally:
        gateway.close()
    assert result['status'] == 'REVIEW_REQUIRED'
    assert result['accepted_supplement_request_count'] == 4
    assert result['supplement_round_count'] == 2 and len(sent) == 3
    assert [len(item['projection_context']['rows']) for item in sent] == [1, 3, 5]


def test_model_disabled_does_not_claim_review_or_write_calls(client, project):
    db, run, _ = _run_with_pages(client, project, [('notes.pdf', ['alpha initial'])])
    loop = ProjectEvidenceLoop(db, None, client.app.state.uploads)
    result = _ask(loop, run, _preview(loop, run))
    assert result['status'] == 'MODEL_DISABLED' and result['answer_basis'] == 'PROJECTION_LOOP_TERMINAL'
    assert result['model_called'] is False and result['model_call_count'] == 0
    assert db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0
