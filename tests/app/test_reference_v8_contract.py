"""Frozen pre-v9 selection, input, receipt and cache contracts; MockTransport only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from app.db import dumps
from app.evidence_loop import ProjectEvidenceLoop
from app.gateway import Gateway
from app.main import create_app
from app.page_selector import select_pages
from app.reference_results import ReferenceResultStore
from app.reference_text_profiles import profile
from app.settings import Settings
from tests.app.test_page_selector import _raw_run, _spatial_text

BASELINE = Path(__file__).resolve().parents[1] / 'fixtures/reference_v8_input_contract.json'
V8 = 'literal-page-selector-8'


def digest(value):
    raw = value if isinstance(value, str) else dumps(value)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def probe(profile_id):
    counts = {}
    def stable_uid(prefix):
        counts[prefix] = counts.get(prefix, 0) + 1
        return prefix + '-' + f'{counts[prefix]:032x}'

    received = []
    def handler(request):
        received.append(json.loads(request.content))
        decision = {'status': 'CANNOT_ANSWER', 'reason_code': 'UNSUPPORTED_TASK',
                    'missing_facts': ['The synthetic fixture does not authorize an engineering answer.'],
                    'requests': [], 'answer': {'claims': [], 'calculations': [], 'coverage': []}}
        return httpx.Response(200, json={
            'id': 'synthetic-v8-compatibility',
            'choices': [{'finish_reason': 'stop', 'message': {'content': dumps(decision)}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 30, 'total_tokens': 130}})

    with tempfile.TemporaryDirectory(prefix='cirp-v8-contract-') as directory, \
            patch('app.db.uid', stable_uid), patch('app.runner.uid', stable_uid), \
            patch('app.uploads.uid', stable_uid):
        app = create_app(Settings(Path(directory), start_worker=False))
        with TestClient(app, headers={'X-CIRP-Client': 'browser'}) as client:
            project = client.post('/api/projects', json={'name': 'Synthetic frozen v8'}).json()
            left, left_map = _spatial_text([
                (10, [('Panel', 10, 36), ('A', 40, 48), ('511', 54, 74)]),
                (30, [('FIRST', 10, 38), ('NOTE', 42, 68)])])
            right, right_map = _spatial_text([
                (10, [('Panel', 300, 326), ('B', 330, 338), ('511', 344, 364)]),
                (30, [('OTHER', 300, 328), ('NOTE', 332, 358)])])
            fragments = [
                {'page': 1, 'sheet': 'A5.01', 'text': left, 'text_map': left_map},
                {'page': 1, 'sheet': 'A5.01', 'text': right, 'text_map': right_map},
                {'page': 2, 'sheet': 'A5.01', 'text': '蓝😀 complete continuation. ' * 2800},
                {'page': 3, 'sheet': 'A9.99', 'text': 'Unrelated source must not enter the question.'}]
            db, run, _ = _raw_run(client, project, fragments, name='synthetic-v8-reference.pdf')
            question = 'On Sheet A5.01, which information applies to Panel A and Panel B?'
            selected = select_pages(db, run, question, selector_version=V8)
            assert selected.byte_count > 64_000
            assert len(selected.selected_pages) == 2
            assert 'layout_lines' in selected.evidence_rows[0]
            settings = Settings(Path(directory) / 'gateway', provider='deepseek',
                                api_key='synthetic-key', live_enabled=True,
                                cheap_model='deepseek-flash', vision_enabled=False)
            gateway = Gateway(settings, db, httpx.Client(transport=httpx.MockTransport(handler)))
            try:
                loop = ProjectEvidenceLoop(db, gateway)
                route = profile(profile_id) if profile_id else None
                first = loop.ask(run, question, selector_version=V8, route=route)
                second = loop.ask(run, question, selector_version=V8, route=route)
                assert len(received) == 1 and second['model_call_count'] == 0
                assert second['execution_receipts'][0]['cached'] is True
                model = route['text_model'] if route else settings.cheap_model
                saved = ReferenceResultStore(db).save(run, question, first, 'deepseek', model)
                assert saved['result']['execution_receipts'] == first['execution_receipts']
                receipt = first['execution_receipts'][0]
                assert receipt['receipt_version'] == 'reference-model-input-receipt-2'
                return {
                    'selection_public': selected.public(),
                    'selection_rows_sha256': digest(list(selected.evidence_rows)),
                    'payload_sha256': digest(received[0]),
                    'system_text_sha256': digest(received[0]['messages'][0]['content']),
                    'user_text_sha256': digest(received[0]['messages'][1]['content']),
                    'receipt': receipt,
                    'execution_profile': first['execution_profile'],
                    'fresh_calls': len(received), 'replay_calls': second['model_call_count']}
            finally:
                gateway.close()



@pytest.mark.parametrize('profile_id', [None, 'FLASH_NONE', 'FLASH_LOW', 'PRO'])
def test_frozen_v8_selection_input_receipt_and_cache_identity(profile_id):
    expected = json.loads(BASELINE.read_text(encoding='utf-8'))
    assert probe(profile_id) == expected[profile_id or 'LEGACY']
