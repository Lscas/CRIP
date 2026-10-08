"""Synthetic adversarial bounds for the offline extractive candidate core."""
from copy import deepcopy
from decimal import localcontext

import pytest
from jsonschema import ValidationError

from app.evidence_loop import validate_evidence_decision
from app.reference_extractive import (
    ExtractiveContractError, candidate_input, compile_extractive_candidate,
)
from app.reference_source_blocks import MAX_PUBLIC_QUOTE_CHARS
from .test_reference_extractive import bundle, source


def _fact_data(rows, parts=None):
    manifest, view, data, parts = bundle(rows, parts)
    data['facts'] = [{'block_ref': view['blocks'][0]['block_ref'], 'part_refs': ['P1']}]
    return manifest, view, data, parts


@pytest.mark.parametrize('mutation', [
    lambda data: data['facts'][0].update(text='provider free text'),
    lambda data: data['facts'][0].update(value='provider selected value'),
    lambda data: data.update(result='provider stated result'),
    lambda data: data.update(extra_property=True),
])
def test_schema_rejects_provider_free_text_values_results_and_extra_properties(mutation):
    rows = [source('Anchor note applies.')]
    manifest, _, data, parts = _fact_data(rows)
    mutation(data)

    with pytest.raises(ValidationError):
        compile_extractive_candidate(data, rows, 'Show the note.', parts, manifest)


@pytest.mark.parametrize(('part_refs', 'expected'), [
    ([], ValidationError),
    (['P1', 'P1'], ValidationError),
    (['P999'], ExtractiveContractError),
])
def test_empty_duplicate_and_unknown_part_refs_fail_closed(part_refs, expected):
    rows = [source('Anchor note applies.')]
    manifest, _, data, parts = _fact_data(rows)
    data['facts'][0]['part_refs'] = part_refs

    with pytest.raises(expected) as raised:
        compile_extractive_candidate(data, rows, 'Show the note.', parts, manifest)
    if expected is ExtractiveContractError:
        assert raised.value.validator == 'extractive_part_unknown'


def test_unbound_source_has_no_alias_and_cannot_create_a_fact():
    row = source('Unbound source must never become an excerpt.')
    row['text_map'] = []
    manifest, view, data, parts = bundle([row])
    assert view['blocks'] == []
    data['facts'] = [{'block_ref': 'B1', 'part_refs': ['P1']}]

    with pytest.raises(ExtractiveContractError) as raised:
        compile_extractive_candidate(data, [row], 'Show the source.', parts, manifest)
    assert raised.value.validator == 'extractive_block_unknown'


def test_more_than_four_public_citations_rejects_without_widening_source_quotes():
    rows = [source(f'Line {index}.', evidence_id=f'E{index}', y=10 + index * 12)
            for index in range(5)]
    manifest, view, data, parts = _fact_data(rows)
    assert len(manifest['blocks'][0]['spans']) == 5
    before_rows = deepcopy(rows)

    with pytest.raises(ExtractiveContractError) as raised:
        compile_extractive_candidate(data, rows, 'Show the source.', parts, manifest)

    assert raised.value.validator == 'extractive_citation_limit'
    assert rows == before_rows
    assert all(len(row['raw_text']) < MAX_PUBLIC_QUOTE_CHARS for row in rows)


def test_oversize_quote_and_combined_claim_limit_fail_without_truncation():
    long_text = 'Q' * (MAX_PUBLIC_QUOTE_CHARS + 1)
    rows = [source(long_text)]
    manifest, _, data, parts = _fact_data(rows)
    before = deepcopy(rows)
    with pytest.raises(ExtractiveContractError) as quote_error:
        compile_extractive_candidate(data, rows, 'Show the source.', parts, manifest)
    assert quote_error.value.validator == 'extractive_citation_limit'
    assert rows == before and len(rows[0]['raw_text']) == MAX_PUBLIC_QUOTE_CHARS + 1

    rows = [source(f'Value {index + 1}.', evidence_id=f'E{index}', y=10 + index * 60)
            for index in range(12)]
    manifest, view, data, parts = bundle(rows)
    data['facts'] = [{'block_ref': block['block_ref'], 'part_refs': ['P1']}
                     for block in view['blocks']]
    data['calculations'] = [{
        'part_refs': ['P1'], 'operator': 'ADD', 'operands': [
            {'block_ref': view['blocks'][0]['block_ref'], 'number_ref': 'N1'},
            {'block_ref': view['blocks'][1]['block_ref'], 'number_ref': 'N1'},
        ],
    }]
    before_data = deepcopy(data)
    with pytest.raises(ExtractiveContractError) as output_error:
        compile_extractive_candidate(data, rows, 'Calculate the total.', parts, manifest)
    assert output_error.value.validator == 'extractive_output_limit'
    assert data == before_data


def _divide(rows, denominator):
    manifest, view, data, parts = bundle(rows)
    data['calculations'] = [{
        'part_refs': ['P1'], 'operator': 'DIVIDE', 'operands': [
            {'block_ref': view['blocks'][0]['block_ref'], 'number_ref': 'N1'},
            {'block_ref': view['blocks'][1]['block_ref'], 'number_ref': 'N1'},
        ],
    }]
    assert view['blocks'][1]['numbers'][0]['value'] == denominator
    return manifest, data, parts


def test_numeric_occurrences_are_offset_bound_and_decimal_context_is_stable():
    rows = [source('A: 10. B: 10.'), source('Divisor: 8.', evidence_id='B', x=400)]
    manifest, view, _, _ = bundle(rows)
    numbers = view['blocks'][0]['numbers']
    assert [number['value'] for number in numbers] == ['10', '10']
    # The candidate input exposes identical values as different aliases; compiled
    # bindings retain their distinct parent-source offsets.
    data = {
        'contract_version': 'reference-extractive-candidate-1',
        'manifest_sha256': manifest['manifest_sha256'], 'facts': [],
        'calculations': [{'part_refs': ['P1'], 'operator': 'ADD', 'operands': [
            {'block_ref': view['blocks'][0]['block_ref'], 'number_ref': 'N1'},
            {'block_ref': view['blocks'][0]['block_ref'], 'number_ref': 'N2'},
        ]}],
    }
    result = compile_extractive_candidate(
        data, rows, 'Calculate the total.', [{'part_ref': 'P1', 'text': 'Total.'}], manifest)
    first, second = result['operand_sources'][0]
    assert first['value'] == second['value'] == '10' and first['start'] != second['start']

    terminating = [source('Dividend: 1.'), source('Divisor: 8.', evidence_id='B', x=400)]
    manifest, data, parts = _divide(terminating, '8')
    with localcontext() as context:
        context.prec = 5
        context.Emin = -1
        context.Emax = 1
        low_precision = compile_extractive_candidate(data, terminating, 'Calculate the quotient.', parts, manifest)
    with localcontext() as context:
        context.prec = 70
        high_precision = compile_extractive_candidate(data, terminating, 'Calculate the quotient.', parts, manifest)
    assert low_precision['answer']['calculations'][0]['result'] == '0.125'
    assert high_precision['answer']['calculations'] == low_precision['answer']['calculations']

    nonterminating = [source('Dividend: 1.'), source('Divisor: 3.', evidence_id='B', x=400)]
    manifest, data, parts = _divide(nonterminating, '3')
    with pytest.raises(ExtractiveContractError) as raised:
        compile_extractive_candidate(data, nonterminating, 'Calculate the quotient.', parts, manifest)
    assert raised.value.validator == 'extractive_arithmetic_inexact'


def test_old_v9_can_accept_cross_object_rewrite_but_extractive_candidate_cannot_encode_it():
    rows = [source('Use 2 anchors at A2.'),
            source('Seal bracket B only if exposed.', evidence_id='B', x=400)]
    legacy = {
        'status': 'ANSWER', 'reason_code': 'ENOUGH_EVIDENCE',
        'missing_facts': [], 'requests': [],
        'answer': {
            'claims': [{
                'text': 'Use 2 anchors at bracket B in all conditions.',
                'citations': [
                    {'type': 'TEXT', 'evidence_ref': 'E1'},
                    {'type': 'TEXT', 'evidence_ref': 'E2'},
                ],
            }],
            'calculations': [],
            'coverage': [{'part_ref': 'P1', 'claim_indexes': [0], 'calculation_indexes': []}],
        },
    }
    accepted = validate_evidence_decision(legacy, rows, 'How should bracket B be fixed?')
    assert accepted['answer']['claims'][0]['text'] == legacy['answer']['claims'][0]['text']

    manifest, view, data, parts = bundle(rows)
    data['facts'] = [{'block_ref': block['block_ref'], 'part_refs': ['P1']} for block in view['blocks']]
    candidate = compile_extractive_candidate(data, rows, 'How should bracket B be fixed?', parts, manifest)
    assert accepted['answer']['answer'] not in candidate['answer']['answer']
    assert candidate['status'] == 'REVIEW_REQUIRED'

    data['facts'][0]['text'] = accepted['answer']['claims'][0]['text']
    with pytest.raises(ValidationError):
        compile_extractive_candidate(data, rows, 'How should bracket B be fixed?', parts, manifest)
