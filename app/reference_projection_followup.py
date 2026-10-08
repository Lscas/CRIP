"""Closed follow-up proof for saved PDF-projection results.

Building this proof deliberately performs the expensive, source-backed check once.
Validation is then a source-free check of the saved result, proof and case ledger;
it must not imply that a missing PDF can be reconstructed from this projection.
"""
from __future__ import annotations

import hashlib
import json
import re

from app.db import DomainError, dumps
from app.reference_projection_identity import verify_projection_result_identity
from app.reference_results import ReferenceResultStore, PROJECTION_RESULT_KIND
from app.reference_projection_protocol import PROJECTION_PROTOCOL_V2


VERSION = 'reference-case-projection-followup-proof-1'
_HASH = re.compile(r'^[0-9a-f]{64}$')
_RESULT_KIND = PROJECTION_RESULT_KIND
_DOC_KEYS = {'document_id', 'document_sha256', 'text_input_rounds', 'visual_input_rounds',
             'first_input_round', 'text_input_count', 'visual_input_count', 'citation_count', 'receipts'}
_RECEIPT_KEYS = {'call_id', 'round', 'request_hash', 'question_hash', 'provider', 'model',
                 'projection_sources', 'projection_rows', 'projection_text_bytes'}
_ROW_KEYS = {'row_ref', 'source_row_ref', 'document_id', 'pdf_sha256', 'manifest_sha256',
             'page_number', 'row_text_sha256', 'direction', 'char_ids', 'projection_version',
             'row_text_bytes'}


def _fail(message: str) -> None:
    raise DomainError(message, 409)


def _value(row, key: str):
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return None


def _id(row, *names: str) -> str:
    for name in names:
        value = _value(row, name)
        if isinstance(value, str) and value:
            return value
    _fail('Projection follow-up identity is inconsistent.')


def _canonical_hash(proof: dict) -> str:
    return hashlib.sha256(dumps({key: value for key, value in proof.items()
                                 if key != 'proof_sha256'}).encode('utf-8')).hexdigest()


def _sources(receipt: dict, document_id: str) -> list[dict]:
    values = receipt.get('projection_sources')
    if not isinstance(values, list):
        _fail('Projection follow-up receipt sources are malformed.')
    matching = [value for value in values if isinstance(value, dict)
                and value.get('document_id') == document_id]
    for source in matching:
        pages = source.get('page_numbers')
        if (set(source) != {'document_id', 'pdf_sha256', 'page_numbers', 'manifest_sha256'}
                or not isinstance(pages, list) or not pages
                or any(type(page) is not int or page < 1 for page in pages)
                or len(pages) != len(set(pages))
                or not all(isinstance(source[key], str) and _HASH.fullmatch(source[key])
                           for key in ('pdf_sha256', 'manifest_sha256'))):
            _fail('Projection follow-up receipt sources are malformed.')
    return matching


def _descriptor(row: dict) -> dict:
    binding = row.get('binding')
    if not isinstance(binding, dict):
        _fail('Projection follow-up stage rows are malformed.')
    try:
        text = row['text']
        value = {
            'row_ref': row['row_ref'], 'source_row_ref': binding['source_row_ref'],
            'document_id': binding['document_id'], 'pdf_sha256': binding['pdf_sha256'],
            'manifest_sha256': binding['manifest_sha256'], 'page_number': binding['page_number'],
            'row_text_sha256': binding['row_text_sha256'], 'direction': binding['direction'],
            'char_ids': binding['char_ids'], 'projection_version': binding['projection_version'],
            'row_text_bytes': len(text.encode('utf-8')),
        }
    except (KeyError, TypeError, AttributeError) as exc:
        raise DomainError('Projection follow-up stage rows are malformed.', 409) from exc
    if (not isinstance(text, str) or not text.strip() or set(value) != _ROW_KEYS
            or not all(isinstance(value[key], str) and value[key]
                       for key in ('row_ref', 'source_row_ref', 'document_id', 'projection_version'))
            or not all(isinstance(value[key], str) and _HASH.fullmatch(value[key])
                       for key in ('pdf_sha256', 'manifest_sha256', 'row_text_sha256'))
            or type(value['page_number']) is not int or value['page_number'] < 1
            or type(value['row_text_bytes']) is not int or value['row_text_bytes'] < 1
            or not isinstance(value['char_ids'], list) or not isinstance(value['direction'], dict)):
        _fail('Projection follow-up stage rows are malformed.')
    return value


def _matching_rows(stage, sources: list[dict]) -> list[dict]:
    output = []
    for row in stage.context.get('rows', []):
        try:
            descriptor = _descriptor(row)
        except DomainError:
            # The authenticated stage may carry unusable/blank rows for other
            # documents.  They do not establish attachment input.
            continue
        if any(descriptor['document_id'] == source['document_id']
               and descriptor['pdf_sha256'] == source['pdf_sha256']
               and descriptor['manifest_sha256'] == source['manifest_sha256']
               and descriptor['page_number'] in source['page_numbers'] for source in sources):
            output.append(descriptor)
    output.sort(key=lambda value: (value['page_number'], value['row_ref']))
    if len({value['row_ref'] for value in output}) != len(output):
        _fail('Projection follow-up stage rows are duplicated.')
    return output


def _receipt_detail(receipt: dict, document_id: str, stage) -> dict | None:
    if receipt.get('receipt_version') != PROJECTION_PROTOCOL_V2.receipt_version:
        _fail('Projection follow-up requires receipt 6.')
    sources = _sources(receipt, document_id)
    if not sources:
        return None
    rows = _matching_rows(stage, sources)
    if not rows:
        return None
    detail = {
        'call_id': receipt.get('model_call_id'), 'round': receipt.get('round'),
        'request_hash': receipt.get('request_hash'), 'question_hash': receipt.get('question_hash'),
        'provider': receipt.get('provider'), 'model': receipt.get('model'),
        'projection_sources': sources, 'projection_rows': rows,
        'projection_text_bytes': sum(row['row_text_bytes'] for row in rows),
    }
    if (set(detail) != _RECEIPT_KEYS or type(detail['round']) is not int or detail['round'] < 1
            or not all(isinstance(detail[key], str) and detail[key]
                       for key in ('call_id', 'provider', 'model'))
            or not all(isinstance(detail[key], str) and _HASH.fullmatch(detail[key])
                       for key in ('request_hash', 'question_hash'))):
        _fail('Projection follow-up receipt identity is malformed.')
    return detail


def build_projection_followup_proof(db, uploads, *, case_row, run_row, result_row,
                                    supplemental_document_ids) -> dict:
    """Build a proof only after authenticating the immutable source-backed result."""
    if (not isinstance(supplemental_document_ids, list) or not supplemental_document_ids
            or len(supplemental_document_ids) != len(set(supplemental_document_ids))
            or any(not isinstance(value, str) or not value for value in supplemental_document_ids)):
        _fail('Projection follow-up documents are invalid.')
    case_id = _id(case_row, 'case_id', 'id'); run_id = _id(run_row, 'run_id', 'id')
    result_id = _id(result_row, 'result_id', 'id')
    if (_value(result_row, 'run_id') != run_id or _value(result_row, 'snapshot_id') != _value(run_row, 'snapshot_id')
            or _value(result_row, 'project_id') != _value(case_row, 'project_id')):
        _fail('Projection follow-up result binding is inconsistent.')
    # This is intentionally the one full authentication; it reads local source
    # bytes and rejects unavailable PDFs before a new case link is persisted.
    record, stage = ReferenceResultStore(db, uploads)._authenticate_projection_row_stage(dict(result_row))
    identity = verify_projection_result_identity(db, result_id)
    if (identity['result_id'] != result_id or identity['run_id'] != run_id
            or identity['snapshot_id'] != _value(run_row, 'snapshot_id')
            or identity['question_key'] != _value(result_row, 'question_key')
            or identity['result_hash'] != _value(result_row, 'result_hash')):
        _fail('Projection follow-up result identity is inconsistent.')
    receipts = record.get('execution_receipts')
    if not isinstance(receipts, list) or not receipts:
        _fail('Projection follow-up receipts are malformed.')
    documents = []
    for document_id in sorted(supplemental_document_ids):
        document = db.one('SELECT sha256 FROM documents WHERE id=? AND project_id=?',
                          (document_id, _value(case_row, 'project_id')), False)
        if document is None or not isinstance(document.get('sha256'), str) or not _HASH.fullmatch(document['sha256']):
            _fail('Projection follow-up document is invalid.')
        details = [detail for receipt in receipts
                   if (detail := _receipt_detail(receipt, document_id, stage)) is not None]
        rounds = [detail['round'] for detail in details]
        if not details or len(rounds) != len(set(rounds)):
            _fail('Projection follow-up attachment was not sent as non-empty projection input.')
        documents.append({
            'document_id': document_id, 'document_sha256': document['sha256'],
            'text_input_rounds': sorted(rounds), 'visual_input_rounds': [],
            'first_input_round': min(rounds), 'text_input_count': len(rounds),
            'visual_input_count': 0, 'citation_count': 0, 'receipts': details,
        })
    models_used = []
    execution_route = []
    for receipt in receipts:
        model = receipt.get('model')
        if not isinstance(model, str) or not model:
            _fail('Projection follow-up receipt identity is malformed.')
        if model not in models_used:
            models_used.append(model)
        execution_route.append({'round': receipt['round'], 'call_id': receipt['model_call_id'],
                                'model': model, 'input_mode': 'TEXT',
                                'request_hash': receipt['request_hash']})
    proof = {
        'proof_version': VERSION, 'case_id': case_id, 'run_id': run_id,
        'snapshot_id': _value(run_row, 'snapshot_id'), 'question_key': identity['question_key'],
        'result_id': result_id, 'result_kind': identity['result_kind'],
        'result_status': identity['status'], 'result_hash': identity['result_hash'],
        'documents': documents, 'model': _value(result_row, 'model'),
        'terminal_model': receipts[-1]['model'], 'models_used': models_used,
        'execution_route': execution_route, 'proof_sha256': '',
    }
    if (proof['result_kind'] != _RESULT_KIND or not isinstance(proof['snapshot_id'], str)
            or not isinstance(proof['model'], str) or not proof['model']):
        _fail('Projection follow-up proof binding is inconsistent.')
    proof['proof_sha256'] = _canonical_hash(proof)
    return proof


class _ConnectionFacade:
    """The identity verifier needs Database.one, not a source-capable Database."""
    def __init__(self, connection): self.connection = connection
    def one(self, query, values=(), required=True):
        row = self.connection.execute(query, values).fetchone()
        if row is None:
            if required: _fail('Projection follow-up identity row is missing.')
            return None
        return dict(row)


def _proof(followup_row) -> dict:
    value = _value(followup_row, 'proof')
    if value is None:
        value = _value(followup_row, 'proof_json')
        if isinstance(value, str):
            try: value = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise DomainError('Projection follow-up proof is malformed.', 409) from exc
    if not isinstance(value, dict): _fail('Projection follow-up proof is malformed.')
    return value


def _validate_detail(detail: dict, document: dict) -> None:
    if not isinstance(detail, dict) or set(detail) != _RECEIPT_KEYS:
        _fail('Projection follow-up proof receipt is malformed.')
    if (type(detail['round']) is not int or detail['round'] < 1
            or not all(isinstance(detail[key], str) and detail[key] for key in ('call_id', 'provider', 'model'))
            or not all(isinstance(detail[key], str) and _HASH.fullmatch(detail[key])
                       for key in ('request_hash', 'question_hash'))
            or not isinstance(detail['projection_sources'], list) or not isinstance(detail['projection_rows'], list)
            or not detail['projection_rows'] or type(detail['projection_text_bytes']) is not int
            or detail['projection_text_bytes'] < 1):
        _fail('Projection follow-up proof receipt is malformed.')
    source_keys = set()
    for source in detail['projection_sources']:
        if (not isinstance(source, dict) or set(source) != {'document_id', 'pdf_sha256', 'page_numbers', 'manifest_sha256'}
                or source.get('document_id') != document['document_id']
                or not isinstance(source.get('page_numbers'), list) or not source['page_numbers']
                or not all(isinstance(source.get(key), str) and _HASH.fullmatch(source[key])
                           for key in ('pdf_sha256', 'manifest_sha256'))
                or any(type(page) is not int or page < 1 for page in source['page_numbers'])
                or len(source['page_numbers']) != len(set(source['page_numbers']))):
            _fail('Projection follow-up proof sources are malformed.')
        source_keys.add((source['pdf_sha256'], source['manifest_sha256'], tuple(source['page_numbers'])))
    byte_sum = 0; refs = set()
    for row in detail['projection_rows']:
        if (not isinstance(row, dict) or set(row) != _ROW_KEYS or row.get('document_id') != document['document_id']
                or not all(isinstance(row.get(key), str) and row[key] for key in ('row_ref', 'source_row_ref', 'projection_version'))
                or not all(isinstance(row.get(key), str) and _HASH.fullmatch(row[key])
                           for key in ('pdf_sha256', 'manifest_sha256', 'row_text_sha256'))
                or type(row.get('page_number')) is not int or row['page_number'] < 1
                or not isinstance(row.get('char_ids'), list) or not isinstance(row.get('direction'), dict)
                or type(row.get('row_text_bytes')) is not int
                or row['row_text_bytes'] < 1):
            _fail('Projection follow-up proof rows are malformed.')
        if row['row_ref'] in refs or not any(row['pdf_sha256'] == pdf and row['manifest_sha256'] == manifest
                                              and row['page_number'] in pages
                                              for pdf, manifest, pages in source_keys):
            _fail('Projection follow-up proof rows are not closed under their receipt source.')
        refs.add(row['row_ref']); byte_sum += row['row_text_bytes']
    if byte_sum != detail['projection_text_bytes']:
        _fail('Projection follow-up proof text-byte accounting is inconsistent.')


def validate_projection_followup_proof(db, connection, *, case_row, followup_row, result_row) -> dict:
    """Validate saved linkage without accessing uploads or PDF bytes."""
    proof = _proof(followup_row)
    expected_keys = {'proof_version', 'case_id', 'run_id', 'snapshot_id', 'question_key', 'result_id',
                     'result_kind', 'result_status', 'result_hash', 'documents', 'model', 'terminal_model',
                     'models_used', 'execution_route', 'proof_sha256'}
    if set(proof) != expected_keys or proof.get('proof_version') != VERSION:
        _fail('Projection follow-up proof shape is unsupported.')
    if not isinstance(proof.get('proof_sha256'), str) or not _HASH.fullmatch(proof['proof_sha256']) or proof['proof_sha256'] != _canonical_hash(proof):
        _fail('Projection follow-up proof hash is inconsistent.')
    case_id = _id(case_row, 'case_id', 'id'); followup_id = _id(followup_row, 'followup_id', 'id')
    result_id = _id(result_row, 'result_id', 'id')
    identity = verify_projection_result_identity(_ConnectionFacade(connection), result_id)
    if (proof['case_id'] != case_id or proof['run_id'] != _value(followup_row, 'run_id')
            or proof['snapshot_id'] != _value(followup_row, 'snapshot_id')
            or proof['result_id'] != result_id or proof['result_kind'] != _RESULT_KIND
            or proof['result_status'] != _value(followup_row, 'result_status')
            or proof['result_hash'] != identity['result_hash'] or proof['question_key'] != identity['question_key']
            or identity['run_id'] != proof['run_id'] or identity['snapshot_id'] != proof['snapshot_id']
            or identity['result_kind'] != proof['result_kind'] or identity['status'] != proof['result_status']
            or _value(result_row, 'result_hash') != proof['result_hash']
            or _value(result_row, 'question_key') != proof['question_key']):
        _fail('Projection follow-up proof binding is inconsistent.')
    if (_value(case_row, 'project_id') != _value(result_row, 'project_id')
            or _value(case_row, 'question_key') != proof['question_key']
            or _value(result_row, 'run_id') != proof['run_id']
            or _value(result_row, 'snapshot_id') != proof['snapshot_id']):
        _fail('Projection follow-up case binding is inconsistent.')
    try:
        record = json.loads(_value(result_row, 'result_json'))
        saved_receipts = record['execution_receipts']
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise DomainError('Projection follow-up saved receipt is malformed.', 409) from exc
    if not isinstance(saved_receipts, list) or not saved_receipts:
        _fail('Projection follow-up saved receipt is malformed.')
    expected_route = []
    for receipt in saved_receipts:
        if not isinstance(receipt, dict): _fail('Projection follow-up saved receipt is malformed.')
        expected_route.append({'round': receipt.get('round'), 'call_id': receipt.get('model_call_id'),
                               'model': receipt.get('model'), 'input_mode': 'TEXT',
                               'request_hash': receipt.get('request_hash')})
    if (proof['model'] != _value(result_row, 'model') or proof['terminal_model'] != saved_receipts[-1].get('model')
            or proof['models_used'] != list(dict.fromkeys(receipt.get('model') for receipt in saved_receipts))
            or proof['execution_route'] != expected_route):
        _fail('Projection follow-up execution route is inconsistent.')
    if (not isinstance(proof['documents'], list) or not proof['documents']
            or not isinstance(proof['models_used'], list) or not proof['models_used']
            or not isinstance(proof['execution_route'], list) or not proof['execution_route']
            or not all(isinstance(proof[key], str) and proof[key] for key in ('model', 'terminal_model'))):
        _fail('Projection follow-up proof is malformed.')
    document_ids = set()
    for document in proof['documents']:
        if not isinstance(document, dict) or set(document) != _DOC_KEYS:
            _fail('Projection follow-up proof document is malformed.')
        if (not isinstance(document['document_id'], str) or document['document_id'] in document_ids
                or not isinstance(document['document_sha256'], str) or not _HASH.fullmatch(document['document_sha256'])
                or document['visual_input_rounds'] != [] or document['visual_input_count'] != 0
                or document['citation_count'] != 0 or not isinstance(document['receipts'], list) or not document['receipts']):
            _fail('Projection follow-up proof document is malformed.')
        if (type(document.get('first_input_round')) is not int
                or type(document.get('text_input_count')) is not int
                or type(document.get('visual_input_count')) is not int
                or type(document.get('citation_count')) is not int
                or not isinstance(document.get('text_input_rounds'), list)
                or not isinstance(document.get('visual_input_rounds'), list)
                or any(type(round_number) is not int or round_number < 1
                       for round_number in document['text_input_rounds'])):
            _fail('Projection follow-up proof document accounting is malformed.')
        document_ids.add(document['document_id'])
        current_document = connection.execute(
            'SELECT sha256 FROM documents WHERE id=? AND project_id=?',
            (document['document_id'], _value(case_row, 'project_id'))).fetchone()
        if current_document is None or current_document['sha256'] != document['document_sha256']:
            _fail('Projection follow-up proof document binding is inconsistent.')
        rounds = []
        for detail in document['receipts']:
            _validate_detail(detail, document); rounds.append(detail['round'])
            matching = [receipt for receipt in saved_receipts if receipt.get('round') == detail['round']]
            if len(matching) != 1:
                _fail('Projection follow-up proof receipt is absent from the saved result.')
            receipt = matching[0]
            if any(detail[key] != receipt.get(source_key) for key, source_key in (
                    ('call_id', 'model_call_id'), ('request_hash', 'request_hash'),
                    ('question_hash', 'question_hash'), ('provider', 'provider'), ('model', 'model'))):
                _fail('Projection follow-up proof receipt identity is inconsistent.')
            expected_sources = [source for source in receipt.get('projection_sources', [])
                                if isinstance(source, dict) and source.get('document_id') == document['document_id']]
            if (detail['projection_sources'] != expected_sources
                    or any(source.get('pdf_sha256') != document['document_sha256']
                           for source in detail['projection_sources'])):
                _fail('Projection follow-up proof receipt sources are inconsistent.')
        if (len(rounds) != len(set(rounds)) or document['text_input_rounds'] != sorted(rounds)
                or document['text_input_count'] != len(rounds)
                or document['first_input_round'] != min(rounds)):
            _fail('Projection follow-up proof document accounting is inconsistent.')
    # This event check prevents swapping proof_json plus its hash after linking.
    events = connection.execute('SELECT after_json FROM reference_case_events WHERE case_id=? AND action=?',
                                (case_id, 'UPDATED')).fetchall()
    matches = 0; associations = 0
    for event in events:
        try: after = json.loads(event['after_json'])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError): continue
        if isinstance(after, dict) and after.get('followup_id') == followup_id:
            associations += 1
            if after.get('proof_sha256') == proof['proof_sha256']: matches += 1
    if matches != 1 or associations != 1:
        _fail('Projection follow-up proof has no unique ledger event binding.')
    return proof
