"""Receipt5 exact-envelope and legacy-schema isolation checks."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError

from app.db import DomainError, dumps
from app.reference_projection_loop_receipt import (
    authenticate_projection_loop_receipt, build_loop_user_text, build_projection_loop_receipt,
)
from app.reference_text_profiles import profile
from contracts.runtime_rules import validate_schema
from .test_reference_projection_receipt import receipt_args


@pytest.fixture
def loop_args(receipt_args):
    args = dict(receipt_args)
    base = args.pop('bundle')
    stage = SimpleNamespace(**vars(base), initial_bundle=base, round_index=0)
    args.pop('initial_projection_context_sha256')
    args.update(stage=stage, prior_chain=[])
    args['user_text'] = build_loop_user_text(stage, [])
    args['upper'] = len(args['system_text'].encode()) + len(args['user_text'].encode()) + 256
    return args


@pytest.mark.parametrize('profile_id', ['FLASH_NONE', 'FLASH_LOW', 'PRO'])
def test_receipt5_is_complete_private_and_does_not_match_receipt4(loop_args, profile_id):
    args = loop_args
    args.update(route=profile(profile_id), limit=profile(profile_id)['max_output_tokens'])
    receipt = build_projection_loop_receipt(**args)
    validate_schema('reference-model-input-receipt', receipt)
    authenticate_projection_loop_receipt(receipt, args['stage'], prior_chain=[],
        system_text=args['system_text'], user_text=args['user_text'], route=args['route'])
    assert 'PRIVATE SOURCE' not in dumps(receipt) and 'Which note?' not in dumps(receipt)
    assert receipt['initial_projection_input_sha256'] == args['stage'].initial_bundle.projection_input_sha256
    with pytest.raises(ValidationError):
        validate_schema('reference-projection-input-receipt', receipt)


@pytest.mark.parametrize('field', ['loop_version', 'round', 'remaining_model_decisions',
    'projection_context', 'request_history', 'prior_chain_sha256', 'question', 'required_answer_parts'])
def test_receipt5_rejects_changed_serialized_envelope(loop_args, field):
    args = loop_args
    body = json.loads(args['user_text']); body[field] = None
    args['user_text'] = dumps(body)
    args['upper'] = len(args['system_text'].encode()) + len(args['user_text'].encode()) + 256
    with pytest.raises(DomainError, match='exact sent input'):
        build_projection_loop_receipt(**args)


@pytest.mark.parametrize('change', ['extra', 'duplicate', 'whitespace', 'scalar'])
def test_receipt5_rejects_noncanonical_and_uncommitted_fields(loop_args, change):
    args = loop_args
    if change == 'extra':
        body = json.loads(args['user_text']); body['answer_hint'] = 'grading key'
        args['user_text'] = dumps(body)
    elif change == 'duplicate': args['user_text'] = args['user_text'][:-1] + ',"round":1}'
    elif change == 'whitespace': args['user_text'] += ' '
    else: args['user_text'] = 'null'
    args['upper'] = len(args['system_text'].encode()) + len(args['user_text'].encode()) + 256
    with pytest.raises(DomainError): build_projection_loop_receipt(**args)


@pytest.mark.parametrize('field', ['initial_projection_input_sha256', 'initial_projection_context_sha256',
    'initial_ordered_projection_manifest_sha256', 'prior_chain_sha256', 'request_history_sha256',
    'profile_neutral_input_sha256', 'projection_input_sha256'])
def test_receipt5_recomputes_each_identity(loop_args, field):
    args = loop_args
    receipt = build_projection_loop_receipt(**args); receipt[field] = '0' * 64
    with pytest.raises(DomainError):
        authenticate_projection_loop_receipt(receipt, args['stage'], prior_chain=[],
            system_text=args['system_text'], user_text=args['user_text'], route=args['route'])


def test_receipt5_cannot_impersonate_legacy_versions(loop_args):
    receipt = build_projection_loop_receipt(**loop_args)
    for version in (1, 2, 3, 4):
        changed = deepcopy(receipt)
        changed['receipt_version'] = f'reference-model-input-receipt-{version}'
        with pytest.raises(ValidationError): validate_schema('reference-model-input-receipt', changed)
