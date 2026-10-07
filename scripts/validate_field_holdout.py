"""Read-only structural checks for an unexecuted field-QA candidate set.

This is not a factual rubric review, scored benchmark, or approval to call a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

CATEGORIES = {'PROJECT_FACT', 'RELATIONSHIP', 'ARITHMETIC', 'GENERAL_KNOWLEDGE', 'BOUNDARY'}


def validate_candidate(data: dict, root: Path) -> dict:
    if data.get('state') != 'CANDIDATE_NOT_PRODUCT_KNOWLEDGE':
        raise ValueError('Only candidate sets are supported.')
    for field in ('approved_for_scored_evaluation', 'used_for_prompt_or_rule_tuning'):
        if data.get(field) is not False:
            raise ValueError(f'{field} must explicitly be false for this candidate check.')
    if data.get('human_adjudication') is not None or data.get('live_tests') != 'NONE' or data.get('paid_calls') != 0:
        raise ValueError('Candidate must not claim human approval or prior live execution.')
    questions = data.get('questions', [])
    sources = data.get('sources', [])
    if len(questions) != 20:
        raise ValueError('Expected exactly 20 candidate questions.')
    source_ids = [source.get('id') for source in sources]
    if any(not isinstance(value, str) or not value for value in source_ids) or len(set(source_ids)) != len(source_ids):
        raise ValueError('Source IDs must be nonempty and unique.')
    source_map = {source['id']: source for source in sources}
    ids = [question.get('id') for question in questions]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError('Question IDs must be nonempty and unique.')
    counts = Counter(question.get('category') for question in questions)
    if not set(counts).issubset(CATEGORIES) or dict(counts) != data.get('distribution'):
        raise ValueError('Category distribution does not match the question set.')
    fixture_root = (root / 'reports' / 'fixtures').resolve()
    attachments = []
    for question in questions:
        if not isinstance(question.get('question'), str) or not question['question'].strip():
            raise ValueError('Each question requires text.')
        facts = question.get('expected_facts')
        if not isinstance(facts, list) or not facts or any(not isinstance(fact, str) or not fact.strip() for fact in facts):
            raise ValueError('Each question requires nonempty expected facts.')
        if question.get('human_adjudication') is not None:
            raise ValueError('Assistant expectations cannot create human adjudication.')
        refs = question.get('source_refs')
        if not isinstance(refs, list) or any(ref not in source_map for ref in refs):
            raise ValueError('Unknown or missing source references.')
        if question['category'] != 'BOUNDARY' and not refs:
            raise ValueError('Answerable fact questions require sources.')
        for reference in question.get('attachment_refs', []):
            if reference not in refs:
                raise ValueError('Attachment must also be an evidence source reference.')
            source = source_map[reference]
            if (source.get('kind') != 'SYNTHETIC_BENCHMARK_ATTACHMENT'
                    or source.get('input_channel') != 'UNTRUSTED_EVIDENCE_ATTACHMENT'
                    or question.get('input_channel') != 'UNTRUSTED_EVIDENCE_ATTACHMENT'
                    or source.get('requires_receipt_proof') is not True
                    or not question.get('required_execution_proof')):
                raise ValueError('Attachment requires an untrusted evidence channel and execution proof.')
            path = (root / source.get('path', '')).resolve()
            if not path.is_relative_to(fixture_root) or not path.is_file():
                raise ValueError('Attachment must be a real file within reports/fixtures.')
            content = path.read_bytes()
            if not content.strip():
                raise ValueError('Attachment is empty.')
            digest = hashlib.sha256(content).hexdigest()
            if (source.get('sha256') != digest or type(source.get('bytes')) is not int
                    or source['bytes'] != len(content)):
                raise ValueError('Attachment bytes/hash do not match the candidate source commitment.')
            attachments.append({'question_id': question['id'], 'source_id': reference,
                                'sha256': digest, 'bytes': len(content)})
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {'validation': 'STRUCTURE_ONLY_PASS', 'question_count': len(questions),
            'distribution': dict(counts), 'candidate_content_sha256': hashlib.sha256(canonical).hexdigest(),
            'attachments': attachments, 'model_calls': 0, 'source_fact_reverification': False,
            'human_approved': False, 'scored_evaluation_ready': False,
            'document_channel_injection_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding='utf-8'))
    result = validate_candidate(data, Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
