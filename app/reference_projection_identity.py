"""Source-free durable identity for projection outcomes.

This verifier is deliberately narrower than projection execution authentication:
it does not read PDFs, uploads, rows, geometry, manifests, or review packet
text.  It is for human-case registration/read paths only; answer, scoring, and
adjudication consumers must continue using full execution authentication.
"""
from __future__ import annotations

import hashlib
import json
import re

from app.db import DomainError, dumps, paid_task_key
from app.evidence_loop import _answer_parts
from app.reference_input_commitment import VERSION as COMMITMENT_VERSION
from app.reference_input_commitment import require_ledger_profile, sha256 as commitment_sha256
from app.reference_projection_decision import ProjectionDecisionError, validate_projection_decision_structure_for
from app.reference_projection_execution import RESULT_KIND, _canonical, _canonical_profile, _check_record, _protocol_for_execution
from app.reference_projection_loop_receipt import chain_sha256, request_history
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1
from contracts.runtime_rules import validate_schema
from jsonschema import ValidationError


IDENTITY_VERSION = 'projection-human-review-identity-1'
_RESULT_ID = re.compile(r'^QAR-[0-9a-f]{32}$')
_KEY_DOMAIN = 'projection-result-key-1'


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _semantic(record: dict) -> dict:
    value = json.loads(_canonical(record))
    value['execution_receipts'] = [
        {key: item for key, item in receipt.items() if key != 'cached'}
        for receipt in value.get('execution_receipts', [])]
    return value


def _row(db, result_id: object) -> dict:
    if not isinstance(result_id, str) or not _RESULT_ID.fullmatch(result_id):
        raise DomainError('Projection identity result id is malformed.', 409)
    row = db.one('SELECT * FROM reference_results WHERE id=?', (result_id,), False)
    if row is None or row.get('result_kind') != RESULT_KIND or not isinstance(row.get('result_json'), str):
        raise DomainError('Projection identity result is not persisted.', 409)
    if hashlib.sha256(row['result_json'].encode('utf-8')).hexdigest() != row.get('result_hash'):
        raise DomainError('Projection identity raw hash is inconsistent.', 409)
    try:
        record = json.loads(row['result_json'])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DomainError('Projection identity record is malformed.', 409) from exc
    if not isinstance(record, dict) or _canonical(record) != row['result_json']:
        raise DomainError('Projection identity record is malformed.', 409)
    return row


def _run(db, row: dict) -> dict:
    run = db.one('SELECT * FROM runs WHERE id=?', (row['run_id'],), False)
    if (run is None or run.get('project_id') != row.get('project_id')
            or run.get('snapshot_id') != row.get('snapshot_id')
            or run.get('status') not in {'PARTIAL', 'COMPLETED'}):
        raise DomainError('Projection identity run is inconsistent.', 409)
    try:
        capabilities = json.loads(run['capabilities'])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DomainError('Projection identity run capabilities are malformed.', 409) from exc
    if not isinstance(capabilities, dict) or capabilities.get('analysis_mode') != 'REFERENCE_QA':
        raise DomainError('Projection identity requires a reference run.', 409)
    return run


def _protocol(record: dict):
    receipts = record.get('execution_receipts')
    proof = record.get('preview_proof')
    first = receipts[0] if isinstance(receipts, list) and receipts else {}
    return _protocol_for_execution(
        execution_version=record.get('projection_execution_version'), loop_version=record.get('loop_version'),
        receipt_version=first.get('receipt_version') if isinstance(first, dict) else None,
        proof_version=proof.get('proof_version') if isinstance(proof, dict) else None)


def _ledger(db, run: dict, route: dict, receipt: dict, protocol, required_parts, conflicts, history, *, terminal: bool):
    task = paid_task_key(f'answer-v3-r{receipt["round"] - 1}:{receipt["request_hash"]}', 0)
    call = db.one('''SELECT project_id,run_id,task_key,request_hash,model,state,actual_units,response,error,
                            reference_input_commitment_version,reference_input_commitment_sha256
                     FROM model_calls WHERE id=?''', (receipt['model_call_id'],), False)
    if (call is None or call['project_id'] != run['project_id'] or call['run_id'] != run['id']
            or call['task_key'] != task or call['request_hash'] != receipt['request_hash']
            or call['model'] != receipt['model'] or call['state'] != 'SETTLED'
            or call['actual_units'] is None or call['response'] is None or call['error'] is not None):
        raise DomainError('Projection identity receipt ledger is inconsistent.', 409)
    require_ledger_profile(call, route['profile_id'])
    if (call['reference_input_commitment_version'] != COMMITMENT_VERSION
            or call['reference_input_commitment_sha256'] != commitment_sha256(route, receipt)):
        raise DomainError('Projection identity receipt commitment is inconsistent.', 409)
    try:
        payload = json.loads(call['response'])
        validate_schema(protocol.schema_name, payload)
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise DomainError('Projection identity settled response is malformed.', 409) from exc
    if (payload.get('contract_version') != protocol.decision_contract_version
            or payload.get('projection_input_sha256') != receipt['projection_input_sha256']):
        raise DomainError('Projection identity settled response is not bound to its receipt.', 409)
    remaining = 4 - receipt['round']
    try:
        return validate_projection_decision_structure_for(
            protocol, payload, projection_input_sha256=receipt['projection_input_sha256'],
            required_parts=required_parts, source_conflicts=conflicts,
            request_history=history, remaining_decisions=remaining)
    except ProjectionDecisionError as exc:
        raise DomainError('Projection identity settled response semantics are malformed.', 409) from exc


def _chain(db, run: dict, route: dict, record: dict, protocol) -> None:
    receipts = record['execution_receipts']; proof = record['preview_proof']
    if type(record['decision_count']) is not int or record['decision_count'] != len(receipts) or not 1 <= len(receipts) <= 3:
        raise DomainError('Projection identity decision count is inconsistent.', 409)
    required = _answer_parts(record['question']); conflicts = record['source_scope']['conflicts']
    chain = []; ids = set(); hashes = set(); payloads = []
    for index, receipt in enumerate(receipts, 1):
        if (receipt.get('round') != index or receipt.get('receipt_version') != protocol.receipt_version
                or receipt.get('loop_version') != protocol.loop_version
                or receipt.get('selector_version') != protocol.selector_version
                or receipt.get('context_policy') != protocol.context_policy
                or receipt.get('initial_projection_input_sha256') != proof.get('initial_projection_input_sha256')
                or receipt.get('initial_projection_context_sha256') != proof.get('initial_projection_context_sha256')
                or receipt.get('initial_ordered_projection_manifest_sha256') != proof.get('initial_ordered_projection_manifest_sha256')
                or receipt.get('prompt_contract_hash') != proof.get('prompt_contract_hash')
                or receipt.get('provider') != route['provider'] or receipt.get('model') != route['text_model']
                or receipt.get('profile_id') != route['profile_id'] or receipt.get('profile_version') != route['profile_version']
                or receipt.get('api_protocol') != route['api_protocol'] or receipt.get('inference_mode') != route['inference_mode']
                or receipt.get('structured_output_mode') != route['structured_output_mode']
                or receipt.get('max_output_tokens') != route['max_output_tokens']
                or receipt.get('question_hash') != hashlib.sha256(' '.join(record['question'].split()).encode('utf-8')).hexdigest()
                or receipt.get('decision_contract_version') != protocol.decision_contract_version
                or receipt.get('prior_chain_sha256') != chain_sha256(chain, protocol=protocol)
                or receipt.get('request_history_sha256') != hashlib.sha256(dumps(request_history(chain, protocol=protocol)).encode()).hexdigest()):
            raise DomainError('Projection identity receipt envelope is inconsistent.', 409)
        if index == 1 and receipt.get('profile_neutral_input_sha256') != proof.get('first_profile_neutral_input_sha256'):
            raise DomainError('Projection identity first receipt/proof input is inconsistent.', 409)
        if receipt.get('model_call_id') in ids or receipt.get('request_hash') in hashes:
            raise DomainError('Projection identity receipt chain is not unique.', 409)
        ids.add(receipt['model_call_id']); hashes.add(receipt['request_hash'])
        # A persisted final scope can contain diagnostics discovered only while
        # appending after a NEED_EVIDENCE response.  Identity cannot reconstruct
        # every intermediate source stage, so provider response structure is
        # checked without injecting final-scope conflicts into any round.
        payload = _ledger(db, run, route, receipt, protocol, required, (),
                          request_history(chain, protocol=protocol), terminal=index == len(receipts))
        payloads.append(payload)
        if index < len(receipts):
            if payload['status'] != 'NEED_EVIDENCE':
                raise DomainError('Projection identity prior receipt is not an evidence request.', 409)
            chain.append({'receipt': receipt, 'decision': payload})
    need = [item for item in payloads if item['status'] == 'NEED_EVIDENCE']
    accepted = sum(len(item['requests']) for item in need)
    if (record['supplement_round_count'] != len(need) or record['accepted_supplement_request_count'] != accepted
            or not 0 <= record['supplement_round_count'] <= 2
            or not record['supplement_round_count'] <= accepted <= 2 * record['supplement_round_count']
            or record['projection_row_count'] != receipts[-1].get('projection_row_count')):
        raise DomainError('Projection identity counters are inconsistent.', 409)
    terminal = payloads[-1]
    if record['status'] == 'REVIEW_REQUIRED':
        packet = record['review_packet']
        selected = [] if not isinstance(packet, dict) else [{'row_ref': item.get('row_ref'), 'part_refs': item.get('part_refs')} for item in packet.get('source_bindings', [])]
        if (terminal['status'] != 'REVIEW_REQUIRED' or terminal['reason_code'] != record['reason_code']
                or terminal['missing_facts'] or terminal['requests'] or record['missing'] != [] or packet is None
                or packet.get('projection_input_sha256') != receipts[-1]['projection_input_sha256']
                or packet.get('projection_context_sha256') != receipts[-1]['projection_context_sha256']
                or _canonical(selected) != _canonical(terminal['selections'])):
            raise DomainError('Projection identity review terminal is inconsistent.', 409)
    elif record['review_packet'] is not None:
        raise DomainError('Projection identity non-review terminal has a review packet.', 409)
    elif record['reason_code'] == 'NO_NEW_EVIDENCE':
        missing = [{'part_ref': part_ref, 'gap_code': 'LOCAL_RETRIEVAL_EXHAUSTED'}
                   for part_ref in dict.fromkeys(item['part_ref'] for item in terminal['missing_facts'])]
        if terminal['status'] != 'NEED_EVIDENCE' or record['missing'] != missing:
            raise DomainError('Projection identity local no-new terminal is inconsistent.', 409)
    elif (terminal['status'] != record['status'] or terminal['reason_code'] != record['reason_code']
          or terminal['missing_facts'] != record['missing']):
        raise DomainError('Projection identity terminal receipt is inconsistent.', 409)


def verify_projection_result_identity(db, result_id: str) -> dict:
    """Return only durable machine identity; this is not source authentication."""
    row = _row(db, result_id); record = json.loads(row['result_json']); run = _run(db, row)
    route = _canonical_profile(record.get('execution_profile')); protocol = _protocol(record)
    _check_record(record, run, row.get('question'), route, protocol)
    if (record['source_scope']['snapshot_id'] != run['snapshot_id']
            or (record['source_scope']['conflicts'] and
                (record['status'], record['reason_code']) not in {
                    ('CANNOT_ANSWER', 'CONFLICTING_SOURCES'),
                    ('CANNOT_ANSWER', 'NO_NEW_EVIDENCE'),
                })):
        raise DomainError('Projection identity source scope is inconsistent.', 409)
    if (row.get('project_id') != run['project_id'] or row.get('snapshot_id') != run['snapshot_id']
            or row.get('question') != record['question'] or row.get('question_key') != record['question_key']
            or row.get('status') != record['status'] or row.get('answer_basis') != record['answer_basis']
            or row.get('qa_version') != '3' or row.get('provider') != route['provider']
            or row.get('model') != record['execution_receipts'][-1]['model']
            or row.get('review_status') != 'NOT_APPLICABLE' or row.get('review_version') != 0 or row.get('review_event_id') is not None):
        raise DomainError('Projection identity metadata is inconsistent.', 409)
    key = _hash([_KEY_DOMAIN, run['id'], run['snapshot_id'], record['question_key'], route['provider'], record['execution_receipts'][-1]['model'], _hash(_semantic(record))])
    owner = db.one('SELECT id FROM reference_results WHERE result_key=?', (key,), False)
    if row.get('result_key') != key or owner is None or owner.get('id') != row['id']:
        raise DomainError('Projection identity semantic key is inconsistent.', 409)
    for table, message in [('reference_result_citations', 'citations'), ('reference_result_review_events', 'review history')]:
        if db.one(f'SELECT COUNT(*) AS n FROM {table} WHERE result_id=?', (row['id'],))['n'] != 0:
            raise DomainError(f'Projection identity has {message}.', 409)
    _chain(db, run, route, record, protocol)
    return {'projection_identity_version': IDENTITY_VERSION, 'result_id': row['id'], 'result_hash': row['result_hash'],
            'result_kind': RESULT_KIND, 'project_id': run['project_id'], 'run_id': run['id'], 'snapshot_id': run['snapshot_id'],
            'question': record['question'], 'question_key': record['question_key'], 'status': record['status'],
            'reason_code': record['reason_code'], 'answer_basis': record['answer_basis'],
            'projection_execution_version': protocol.execution_version, 'loop_version': protocol.loop_version,
            'receipt_version': protocol.receipt_version, 'proof_version': protocol.proof_version,
            'decision_count': record['decision_count'], 'supplement_round_count': record['supplement_round_count'],
            'accepted_supplement_request_count': record['accepted_supplement_request_count'], 'projection_row_count': record['projection_row_count']}
