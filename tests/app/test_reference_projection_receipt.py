"""Receipt content commitments; source re-extraction is covered by Gateway tests."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError

from app.db import DomainError, dumps
from app.reference_projection_decision import CONTRACT_VERSION, prompt_contract
from app.reference_projection_receipt import build_projection_receipt, authenticate_projection_receipt
from app.reference_text_profiles import profile
from contracts.runtime_rules import validate_schema


@pytest.fixture
def receipt_args():
    system, _schema, prompt_hash = prompt_contract()
    context = {'projection_complete': True, 'source_conflicts': [], 'rows': [
        {'row_ref': 'X1', 'text': 'PRIVATE SOURCE IF EXPOSED ONLY', 'selectable': True}]}
    parts = [{'part_ref': 'P1', 'text': 'Which note?'}]
    bundle = SimpleNamespace(question='Which note?', required_parts=parts, context=context,
        projection_input_sha256='a' * 64, projection_context_sha256='b' * 64,
        ordered_projection_manifest_sha256='c' * 64, projection_sources=[{
            'document_id': 'DOC-' + 'd' * 32, 'pdf_sha256': 'd' * 64,
            'page_numbers': [1], 'manifest_sha256': 'e' * 64}])
    content = {'contract_version': CONTRACT_VERSION,
               'projection_input_sha256': bundle.projection_input_sha256,
               'round': 1, 'question': bundle.question, 'remaining_model_decisions': 1,
               'required_answer_parts': parts, 'projection_context': context,
               'effective_source_conflicts': [], 'request_history': []}
    user_text = dumps(content)
    return dict(call_id='CALL-' + 'f' * 32, round_index=0, request_hash='f' * 64,
                question=bundle.question, bundle=bundle, route=profile('FLASH_NONE'),
                system_text=system, user_text=user_text,
                upper=len(system.encode('utf-8')) + len(user_text.encode('utf-8')) + 256,
                limit=2600, cached=False, prompt_contract_hash=prompt_hash,
                initial_projection_context_sha256=bundle.projection_context_sha256)


@pytest.mark.parametrize('profile_id', ['FLASH_NONE', 'FLASH_LOW', 'PRO'])
def test_canonical_receipt_contains_identity_not_source_or_prompt(receipt_args, profile_id):
    args = receipt_args
    args.update(route=profile(profile_id), limit=profile(profile_id)['max_output_tokens'])
    receipt = build_projection_receipt(**args)
    validate_schema('reference-model-input-receipt', receipt)
    authenticate_projection_receipt(receipt, args['bundle'], system_text=args['system_text'],
                                    user_text=args['user_text'], route=args['route'])
    encoded = dumps(receipt)
    assert 'PRIVATE SOURCE' not in encoded and 'Which note?' not in encoded
    assert receipt['projection_row_count'] == 1
    assert receipt['evidence_inputs'] == [] and receipt['evidence_count'] == 0
    assert receipt['profile_neutral_input_sha256'] == hashlib.sha256(dumps([
        'reference-profile-neutral-input-1', args['system_text'], args['user_text'], []
    ]).encode('utf-8')).hexdigest()


@pytest.mark.parametrize('field', ['question', 'required_answer_parts', 'projection_context',
                                  'projection_input_sha256', 'round', 'contract_version',
                                  'remaining_model_decisions', 'effective_source_conflicts', 'request_history'])
def test_receipt_must_describe_actual_serialized_input(receipt_args, field):
    import json
    args = receipt_args; content = json.loads(args['user_text']); content[field] = None
    args['user_text'] = dumps(content)
    args['upper'] = len(args['system_text'].encode('utf-8')) + len(args['user_text'].encode('utf-8')) + 256
    with pytest.raises(DomainError, match='sent message'):
        build_projection_receipt(**args)


@pytest.mark.parametrize('field,value', [
    ('question', 'Other question'), ('prompt_contract_hash', '0' * 64),
    ('initial_projection_context_sha256', '0' * 64), ('limit', 100),
])
def test_receipt_rejects_mismatched_dispatch_fields(receipt_args, field, value):
    receipt_args[field] = value
    with pytest.raises(DomainError):
        build_projection_receipt(**receipt_args)


@pytest.mark.parametrize('field,value', [('round_index', True), ('round_index', -1),
                                       ('round_index', 1), ('round_index', 2),
                                       ('round_index', 3), ('cached', 1), ('upper', 1)])
def test_receipt_checks_dispatch_accounting_before_reservation(receipt_args, field, value):
    receipt_args[field] = value
    with pytest.raises(DomainError, match='dispatch accounting'):
        build_projection_receipt(**receipt_args)


@pytest.mark.parametrize('change', ['extra_hint', 'duplicate_key', 'noncanonical', 'scalar'])
def test_exact_envelope_excludes_uncommitted_hints_and_duplicate_json_keys(receipt_args, change):
    import json
    args = receipt_args
    if change == 'extra_hint':
        content = json.loads(args['user_text']); content['answer_hint'] = 'private grading key'
        args['user_text'] = dumps(content)
    elif change == 'duplicate_key':
        args['user_text'] = args['user_text'][:-1] + ',"request_history":[]}'
    elif change == 'noncanonical': args['user_text'] = ' ' + args['user_text']
    else: args['user_text'] = 'null'
    args['upper'] = len(args['system_text'].encode('utf-8')) + len(args['user_text'].encode('utf-8')) + 256
    with pytest.raises(DomainError, match='sent message'):
        build_projection_receipt(**args)


@pytest.mark.parametrize('field,value', [
    ('projection_context_sha256', '0' * 64), ('projection_input_sha256', '0' * 64),
    ('ordered_projection_manifest_sha256', '0' * 64), ('profile_neutral_input_sha256', '0' * 64),
    ('source_text_bytes', 0), ('projection_row_count', 9), ('system_text_bytes', 1),
    ('user_text_bytes', 1), ('request_upper_bound_bytes', 1),
    ('profile_id', 'PRO'), ('inference_mode', 'thinking-low'), ('max_output_tokens', 8000),
])
def test_receipt_reauthentication_checks_derived_fields(receipt_args, field, value):
    args = receipt_args; receipt = build_projection_receipt(**args); receipt[field] = value
    with pytest.raises(DomainError):
        authenticate_projection_receipt(receipt, args['bundle'], system_text=args['system_text'],
                                        user_text=args['user_text'], route=args['route'])


@pytest.mark.parametrize('field,value', [
    ('source_text_included', True), ('prompt_content_included', True),
    ('chain_of_thought_included', True), ('source_text_clipped', True),
    ('evidence_inputs', [{'evidence_id': 'X1', 'text_sha256': 'a' * 64}]),
    ('selector_version', 'literal-page-selector-9'), ('source_text', 'leaked'),
])
def test_shared_schema_cannot_confuse_projection_with_legacy_or_leak_text(receipt_args, field, value):
    receipt = build_projection_receipt(**receipt_args); receipt[field] = value
    with pytest.raises(ValidationError):
        validate_schema('reference-model-input-receipt', receipt)


def test_projection_metadata_cannot_be_masqueraded_as_legacy_receipt(receipt_args):
    receipt = build_projection_receipt(**receipt_args)
    for version in (1, 2, 3):
        invalid = deepcopy(receipt); invalid['receipt_version'] = f'reference-model-input-receipt-{version}'
        with pytest.raises(ValidationError):
            validate_schema('reference-model-input-receipt', invalid)
