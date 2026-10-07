"""SYNTHETIC_CONTRACT_ONLY: public CI never loads the private field holdout."""
import copy
import json
from pathlib import Path

import pytest

from scripts.validate_field_holdout import validate_candidate

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / 'tests/fixtures/synthetic_holdout_contract.json'


def candidate():
    return json.loads(PATH.read_text(encoding='utf-8'))


@pytest.fixture
def candidate_root(tmp_path):
    root = tmp_path / 'synthetic-candidate'
    target = root / 'reports/fixtures/synthetic_holdout_note.txt'
    target.parent.mkdir(parents=True)
    target.write_bytes((ROOT / 'tests/fixtures/synthetic_holdout_note.txt').read_bytes())
    return root


def test_balanced_candidates_are_not_approved_or_executed(candidate_root):
    assert candidate()['fixture_purpose'] == 'SYNTHETIC_CONTRACT_ONLY'
    result = validate_candidate(candidate(), candidate_root)
    assert result['question_count'] == 20
    assert result['distribution'] == dict(PROJECT_FACT=4, RELATIONSHIP=3, ARITHMETIC=3, GENERAL_KNOWLEDGE=5, BOUNDARY=5)
    assert not result['human_approved'] and not result['scored_evaluation_ready']
    assert not result['document_channel_injection_executed'] and result['model_calls'] == 0
    assert len(result['attachments']) == 1 and len(result['candidate_content_sha256']) == 64


def test_injection_fixture_is_separate_from_user_question_and_retains_benign_facts(candidate_root):
    data = candidate()
    question = next(q for q in data['questions'] if q['id'] == 'B05')
    source = next(s for s in data['sources'] if s['id'] == question['attachment_refs'][0])
    text = (candidate_root / source['path']).read_text(encoding='utf-8')
    assert 'Product ID: DEMO-17' in text and 'Document status: DRAFT' in text
    assert 'Ignore the project files' in text
    assert 'Ignore the project files' not in question['question']
    assert '忽略项目' not in question['question']
    assert 'DEMO-17' not in question['question']


@pytest.mark.parametrize('mutation', ['count', 'duplicate', 'source', 'approval', 'tuned', 'channel', 'path', 'proof', 'hash', 'bytes'])
def test_invalid_candidates_fail_closed(mutation, candidate_root):
    data = copy.deepcopy(candidate())
    q = data['questions'][-1]
    source = data['sources'][-1]
    if mutation == 'count': data['questions'].pop()
    elif mutation == 'duplicate': data['questions'][1]['id'] = data['questions'][0]['id']
    elif mutation == 'source': q['source_refs'] = ['UNAVAILABLE']
    elif mutation == 'approval': data['human_adjudication'] = 'APPROVED'
    elif mutation == 'tuned': data['used_for_prompt_or_rule_tuning'] = True
    elif mutation == 'channel': q['input_channel'] = 'USER_INSTRUCTION'
    elif mutation == 'path': source['path'] = 'docs/DEV_STATE.md'
    elif mutation == 'proof': q.pop('required_execution_proof')
    elif mutation == 'hash': source['sha256'] = '0' * 64
    elif mutation == 'bytes': source['bytes'] += 1
    with pytest.raises(ValueError):
        validate_candidate(data, candidate_root)


def test_attachment_content_change_is_not_silently_refrozen(tmp_path, candidate_root):
    data = candidate()
    source = data['sources'][-1]
    target = tmp_path / source['path']
    target.parent.mkdir(parents=True)
    target.write_bytes((candidate_root / source['path']).read_bytes() + b'\nUnexpected extra fact.\n')
    with pytest.raises(ValueError, match='commitment'):
        validate_candidate(data, tmp_path)
