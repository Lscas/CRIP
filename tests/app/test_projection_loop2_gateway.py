"""DEV-171 loop2 gateway isolation: synthetic PDFs and MockTransport only."""
from __future__ import annotations

import json

import httpx
import pytest

from app.db import DomainError
from app.gateway import InvalidModelOutput
from app.reference_projection_loop import ProjectProjectionLoop
from app.reference_projection_loop_receipt import chain_sha256
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1, PROJECTION_PROTOCOL_V2
from app.reference_projection_stage import append_projection_stage
from app.reference_text_profiles import profile
from .test_gateway_projection import _gateway
from .test_reference_projection_input import _pdf_with_unsupported_middle_char, _run_with_pages


QUESTION = 'alpha?'


def _setup(client, project, tmp_path, responder, pages=None, entries=None):
    db, run, _ = _run_with_pages(client, project, entries or [('synthetic.pdf', pages or [
        'alpha initial note', 'bravo supplemental note'])])
    sent = []
    def handler(request):
        body = json.loads(request.content)
        envelope = json.loads(body['messages'][1]['content'])
        sent.append(envelope)
        return httpx.Response(200, json={'id': f'loop2-{len(sent)}', 'choices': [
            {'finish_reason': 'stop', 'message': {'content': json.dumps(responder(envelope))}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 8}})
    gateway = _gateway(tmp_path, db, handler)
    return db, run, gateway, ProjectProjectionLoop(
        db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V2), sent


def _need(envelope, query):
    return {'contract_version': envelope['contract_version'],
            'projection_input_sha256': envelope['projection_input_sha256'],
            'status': 'NEED_EVIDENCE', 'reason_code': 'MISSING_SOURCE_TEXT',
            'missing_facts': [{'part_ref': 'P1', 'gap_code': 'SOURCE_TEXT'}],
            'requests': [{'tool': 'SEARCH_TEXT', 'query': query}], 'selections': []}


def _insufficient(envelope):
    return {'contract_version': 'project-projection-decision-2',
            'projection_input_sha256': envelope['projection_input_sha256'],
            'status': 'CANNOT_ANSWER', 'reason_code': 'INSUFFICIENT_EVIDENCE',
            'missing_facts': [{'part_ref': 'P1', 'gap_code': 'SOURCE_TEXT'}],
            'requests': [], 'selections': []}


def _ask(loop, run, proof):
    return loop.ask(run, QUESTION, profile('FLASH_NONE'), preview_proof=proof)


def test_loop2_provider_no_new_is_settled_error_and_replay_makes_zero_http(client, project, tmp_path):
    def invalid(envelope):
        value = _need(envelope, 'alpha')
        value.update(status='CANNOT_ANSWER', reason_code='NO_NEW_EVIDENCE', requests=[],
                     missing_facts=[{'part_ref': 'P1', 'gap_code': 'LOCAL_RETRIEVAL_EXHAUSTED'}])
        return value
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, invalid)
    try:
        proof = loop.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        with pytest.raises(InvalidModelOutput):
            _ask(loop, run, proof)
        with pytest.raises(InvalidModelOutput):
            _ask(loop, run, proof)
    finally:
        gateway.close()
    assert len(sent) == 1
    assert db.one("SELECT state FROM model_calls")['state'] == 'SETTLED_ERROR'


def test_loop2_need_then_zero_append_stops_after_one_http(client, project, tmp_path):
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, lambda envelope: _need(envelope, 'alpha'))
    try:
        proof = loop.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        result = _ask(loop, run, proof)
    finally:
        gateway.close()
    assert result['status'] == 'CANNOT_ANSWER'
    assert result['reason_code'] == 'NO_NEW_EVIDENCE'
    assert result['answer_basis'] == 'PROJECTION_LOCAL_RETRIEVAL_EXHAUSTED'
    assert result['model_call_count'] == len(sent) == 1
    assert result['execution_receipts'][0]['receipt_version'] == 'reference-model-input-receipt-6'


def test_loop2_positive_append_then_zero_append_uses_two_http(client, project, tmp_path):
    db, run, gateway, loop, sent = _setup(
        client, project, tmp_path, lambda envelope: _need(envelope, 'bravo' if envelope['round'] == 1 else 'alpha'))
    try:
        proof = loop.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        result = _ask(loop, run, proof)
    finally:
        gateway.close()
    assert result['status'] == 'CANNOT_ANSWER' and result['reason_code'] == 'NO_NEW_EVIDENCE'
    assert result['model_call_count'] == len(sent) == 2
    assert [item['receipt_version'] for item in result['execution_receipts']] == [
        'reference-model-input-receipt-6', 'reference-model-input-receipt-6']


def test_loop2_only_allows_insufficient_evidence_on_final_round(client, project, tmp_path):
    def responder(envelope):
        if envelope['round'] < 3:
            return _need(envelope, 'bravo' if envelope['round'] == 1 else 'charlie')
        return _insufficient(envelope)
    _db, run, gateway, loop, sent = _setup(
        client, project, tmp_path, responder,
        ['alpha initial note', 'bravo supplemental note', 'charlie supplemental note'])
    try:
        proof = loop.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        result = _ask(loop, run, proof)
    finally:
        gateway.close()
    assert result['status'] == 'CANNOT_ANSWER' and result['reason_code'] == 'INSUFFICIENT_EVIDENCE'
    assert len(sent) == 3


@pytest.mark.parametrize('round_number', [1, 2])
def test_loop2_early_insufficient_is_settled_error_and_never_retried(client, project, tmp_path, round_number):
    def responder(envelope):
        if envelope['round'] == round_number:
            return _insufficient(envelope)
        return _need(envelope, 'bravo')
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, responder)
    try:
        proof = loop.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        with pytest.raises(InvalidModelOutput):
            _ask(loop, run, proof)
        with pytest.raises(InvalidModelOutput):
            _ask(loop, run, proof)
    finally:
        gateway.close()
    assert len(sent) == round_number
    assert db.one("SELECT COUNT(*) AS n FROM model_calls WHERE state='SETTLED_ERROR'")['n'] == 1


def test_loop2_incomplete_pdf_final_insufficient_is_settled_error_and_recovery_makes_no_http(
        client, project, tmp_path):
    """Source projection remains authenticated; the third-round terminal is never published."""
    def responder(envelope):
        if envelope['round'] == 1:
            return _need(envelope, 'bravo')
        if envelope['round'] == 2:
            return _need(envelope, 'charlie')
        return _insufficient(envelope)
    entries = [
        ('alpha-geometry.pdf', ['alpha ABCDE'], _pdf_with_unsupported_middle_char()),
        ('bravo.pdf', ['bravo supplemental note']),
        ('charlie.pdf', ['charlie supplemental note']),
    ]
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, responder, entries=entries)
    try:
        proof = loop.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        with pytest.raises(InvalidModelOutput):
            _ask(loop, run, proof)
        with pytest.raises(InvalidModelOutput):
            _ask(loop, run, proof)
    finally:
        gateway.close()
    assert len(sent) == 3
    assert sent[2]['projection_context']['projection_complete'] is False
    assert db.one("SELECT COUNT(*) AS n FROM model_calls WHERE state='SETTLED_ERROR'")['n'] == 1


def test_loop2_refuses_loop1_proof_or_receipt_before_http(client, project, tmp_path):
    db, run, gateway, loop, sent = _setup(client, project, tmp_path, lambda envelope: _need(envelope, 'alpha'))
    try:
        v2_proof = loop.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        v1_loop = ProjectProjectionLoop(db, gateway, client.app.state.uploads, protocol=PROJECTION_PROTOCOL_V1)
        v1_proof = v1_loop.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        with pytest.raises(DomainError):
            _ask(loop, run, v1_proof)
        assert sent == []
        result = _ask(loop, run, v2_proof)
        chain = [{'receipt': result['execution_receipts'][0], 'decision': _need(sent[0], 'alpha')}]
        with pytest.raises(DomainError):
            chain_sha256(chain, protocol=PROJECTION_PROTOCOL_V1)
    finally:
        gateway.close()
    assert len(sent) == 1


def test_loop1_also_rejects_proof3_before_reservation(client, project, tmp_path):
    _db, run, gateway, loop, sent = _setup(client, project, tmp_path, lambda envelope: _need(envelope, 'alpha'))
    try:
        proof2 = loop.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        loop1 = ProjectProjectionLoop(db=loop.db, gateway=gateway, uploads=loop.uploads,
                                      protocol=PROJECTION_PROTOCOL_V1)
        proof1 = loop1.preview(run, QUESTION, profile('FLASH_NONE'))['preview_proof']
        with pytest.raises(DomainError):
            loop1.ask(run, QUESTION, profile('FLASH_NONE'), preview_proof=proof2)
        assert sent == []
        assert loop1.ask(run, QUESTION, profile('FLASH_NONE'), preview_proof=proof1)['model_call_count'] == 1
    finally:
        gateway.close()
    assert len(sent) == 1


def test_gateway_refuses_receipt5_chain_under_loop2_before_second_http(client, project, tmp_path):
    db, run, gateway, loop2, sent = _setup(client, project, tmp_path, lambda envelope: _need(envelope, 'bravo'))
    loop1 = ProjectProjectionLoop(db, gateway, loop2.uploads, protocol=PROJECTION_PROTOCOL_V1)
    route = profile('FLASH_NONE')
    try:
        stage0, _ = loop1._initial(run, QUESTION)
        proof1 = loop1.preview(run, QUESTION, route)['preview_proof']
        first = gateway._projection_loop_decision_v10(run, QUESTION, stage0, route,
                                                       prior_chain=[], preview_proof=proof1)
        stage1 = append_projection_stage(stage0, first.data['requests'], db, loop1.uploads)
        proof2 = loop2.preview(run, QUESTION, route)['preview_proof']
        with pytest.raises(DomainError):
            gateway._projection_loop_decision_v10_loop2(
                run, QUESTION, stage1, route,
                prior_chain=[{'receipt': first.execution_receipt, 'decision': first.data}],
                preview_proof=proof2)
    finally:
        gateway.close()
    assert len(sent) == 1
