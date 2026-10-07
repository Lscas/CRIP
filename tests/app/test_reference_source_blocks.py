import copy

import pytest

from app.reference_source_blocks import (
    MAX_PUBLIC_CITATIONS, MAX_PUBLIC_QUOTE_CHARS, SourceBlockError,
    authenticate_source_blocks, build_source_blocks, model_source_blocks,
)


def _row(evidence_id, text, mapping, *, page=1, document_id='DOC'):
    return {
        'evidence_id': evidence_id, 'document_id': document_id, 'raw_text': text,
        'extraction_method': 'TEXT_LAYER', 'locator': {'page_number': page},
        'text_map': mapping,
    }


def _map(text, positions):
    """Map whitespace-delimited tokens at supplied (left, top) origins."""
    value=[]; at=0
    for token, left, top in positions:
        start=text.index(token, at); end=start+len(token); at=end
        value.append({'start': start, 'end': end,
                      'bbox': [left, top, left + max(8, len(token) * 8), top + 10]})
    return value


def test_blocks_keep_exact_parent_offsets_and_interleaved_rows_deterministically():
    alpha='Panel A: condition one\n'
    beta='Panel B: condition two\n'
    rows=[
        _row('E-A', alpha, _map(alpha, [('Panel', 10, 10), ('A:', 58, 10), ('condition', 90, 10), ('one', 170, 10)])),
        _row('E-B', beta, _map(beta, [('Panel', 300, 10), ('B:', 348, 10), ('condition', 380, 10), ('two', 460, 10)])),
    ]
    manifest=build_source_blocks(rows)
    assert manifest['version'] == 'source-excerpt-blocks-1'
    assert [block['block_ref'] for block in manifest['blocks']] == ['B1', 'B2']
    assert [block['text'] for block in manifest['blocks']] == [alpha, beta]
    assert manifest['blocks'][0]['spans'] == [
        {'evidence_id': 'E-A', 'start': 0, 'end': len(alpha), 'text': alpha,
         'bbox': [10.0, 10.0, 194.0, 20.0]}]
    assert manifest['blocks'][1]['spans'] == [
        {'evidence_id': 'E-B', 'start': 0, 'end': len(beta), 'text': beta,
         'bbox': [300.0, 10.0, 484.0, 20.0]}]
    authenticate_source_blocks(manifest, rows)
    view=model_source_blocks(manifest)
    assert view['blocks'] == [
        {'block_ref': 'B1', 'document_id': 'DOC', 'page_number': 1, 'text': alpha},
        {'block_ref': 'B2', 'document_id': 'DOC', 'page_number': 1, 'text': beta},
    ]
    assert 'E-A' not in repr(view) and 'start' not in repr(view)


def test_same_number_different_conditions_remains_two_nonsemantic_blocks():
    left='Panel A requires 511 volts.\n'
    right='Panel B requires 511 amps.\n'
    rows=[
        _row('E1', left, _map(left, [('Panel', 10, 10), ('A', 58, 10), ('requires', 74, 10), ('511', 146, 10), ('volts.', 178, 10)])),
        _row('E2', right, _map(right, [('Panel', 300, 10), ('B', 348, 10), ('requires', 364, 10), ('511', 436, 10), ('amps.', 468, 10)])),
    ]
    manifest=build_source_blocks(rows)
    assert [block['text'] for block in manifest['blocks']] == [left, right]
    assert all('object' not in block and 'relationship' not in block for block in manifest['blocks'])
    assert any('not engineering objects' in item for item in manifest['limitations'])


@pytest.mark.parametrize('mutate', ['source_text', 'text_map', 'row_order'])
def test_authentication_rebuild_rejects_source_map_and_order_tampering(mutate):
    text='Door condition alpha\n'
    rows=[_row('E1', text, _map(text, [('Door', 10, 10), ('condition', 50, 10), ('alpha', 130, 10)]))]
    manifest=build_source_blocks(rows)
    changed=copy.deepcopy(rows)
    if mutate == 'source_text': changed[0]['raw_text']='Door condition beta\n'
    elif mutate == 'text_map': changed[0]['text_map'][1]['bbox'][0]=51
    else:
        second='Other condition beta\n'
        changed.append(_row('E2', second, _map(second, [('Other', 10, 40), ('condition', 58, 40), ('beta', 138, 40)])))
        manifest=build_source_blocks(changed)
        changed=list(reversed(changed))
    with pytest.raises(SourceBlockError):
        authenticate_source_blocks(manifest, changed)


@pytest.mark.parametrize('bad_row', [
    lambda: _row('E1', 'mapped text', []),
    lambda: _row('E1', 'mapped text', [{'start': 0, 'end': 6, 'bbox': [1, 1, 8, 8]}]),
    lambda: _row('E1', 'one two', [
        {'start': 0, 'end': 3, 'bbox': [1, 1, 8, 8]},
        {'start': 2, 'end': 7, 'bbox': [10, 1, 18, 8]},
    ]),
    lambda: _row('E1', 'one', [
        {'start': 0, 'end': 3, 'bbox': [1, 1, 8, 8]},
        {'start': 0, 'end': 3, 'bbox': [1, 1, 8, 8]},
    ]),
    lambda: _row('E1', 'one', [{'start': 0, 'end': 3, 'bbox': [1, 1, 8, 8], 'rotation': 90}]),
    lambda: {'evidence_id': 'E1', 'document_id': 'DOC', 'raw_text': 'one',
             'extraction_method': 'TEXT_LAYER', 'locator': {'page_number': 1}},
])
def test_unusable_geometry_and_nonblank_unmapped_text_fail_closed(bad_row):
    manifest=build_source_blocks([bad_row()])
    assert manifest['blocks'][0]['status'] == 'UNUSABLE'
    assert manifest['blocks'][0]['reason']
    assert model_source_blocks(manifest)['blocks'] == []


def test_vertical_blank_gap_creates_separate_blocks_without_clipping_text():
    text='first condition\nsecond condition\n'
    rows=[_row('E1', text, _map(text, [
        ('first', 10, 10), ('condition', 58, 10), ('second', 10, 130), ('condition', 66, 130),
    ]))]
    manifest=build_source_blocks(rows)
    assert [block['text'] for block in manifest['blocks']] == ['first condition\n', 'second condition\n']
    assert ''.join(block['text'] for block in manifest['blocks']) == text


def test_interleaved_page_columns_keep_each_lane_continuation_together():
    left='LEFT one\nLEFT two\n'
    right='RIGHT one\nRIGHT two\n'
    rows=[
        _row('E-L', left, _map(left, [
            ('LEFT', 10, 10), ('one', 50, 10), ('LEFT', 10, 50), ('two', 50, 50),
        ])),
        _row('E-R', right, _map(right, [
            ('RIGHT', 300, 30), ('one', 348, 30), ('RIGHT', 300, 70), ('two', 348, 70),
        ])),
    ]
    manifest=build_source_blocks(rows)
    assert [block['text'] for block in manifest['blocks']] == [left, right]
    assert [block['block_ref'] for block in manifest['blocks']] == ['B1', 'B2']


def test_tall_narrow_native_token_without_rotation_metadata_is_unusable():
    row=_row('E1', 'VERT', [{'start': 0, 'end': 4, 'bbox': [10, 10, 14, 60]}])
    manifest=build_source_blocks([row])
    assert manifest['blocks'][0]['status'] == 'UNUSABLE'
    assert manifest['blocks'][0]['reason'] == 'GEOMETRY_REQUIRES_DIRECTION_METADATA'


def test_left_segment_cannot_swallow_unselected_right_column_raw_text():
    text='A X B'
    row=_row('E1', text, [
        {'start': 0, 'end': 1, 'bbox': [10, 10, 18, 20]},
        {'start': 2, 'end': 3, 'bbox': [300, 10, 308, 20]},
        {'start': 4, 'end': 5, 'bbox': [30, 10, 38, 20]},
    ])
    manifest=build_source_blocks([row])
    assert manifest['blocks'] == [{
        'block_ref': None, 'document_id': 'DOC', 'page_number': 1,
        'text': text, 'spans': [], 'status': 'UNUSABLE',
        'reason': 'SEGMENT_WOULD_OMIT_NONBLANK_TEXT',
    }]


@pytest.mark.parametrize('text', ['I', '1', 'II'])
def test_short_narrow_tokens_are_not_assumed_to_have_unknown_direction(text):
    row=_row('E1', text, [{'start': 0, 'end': len(text), 'bbox': [10, 10, 12, 20]}])
    assert build_source_blocks([row])['blocks'][0]['status'] == 'BOUND'


@pytest.mark.parametrize('text', ['PAG', '2/1'])
def test_long_narrow_tokens_require_direction_metadata(text):
    row=_row('E1', text, [{'start': 0, 'end': len(text), 'bbox': [10, 10, 14, 40]}])
    block=build_source_blocks([row])['blocks'][0]
    assert block['status'] == 'UNUSABLE'
    assert block['reason'] == 'GEOMETRY_REQUIRES_DIRECTION_METADATA'


def test_three_character_narrow_boundary_is_strictly_greater_than_two_to_one():
    exact=_row('E1', 'ABC', [{'start': 0, 'end': 3, 'bbox': [10, 10, 20, 30]}])
    over=_row('E2', 'ABC', [{'start': 0, 'end': 3, 'bbox': [10, 10, 19, 30]}])
    assert build_source_blocks([exact])['blocks'][0]['status'] == 'BOUND'
    assert build_source_blocks([over])['blocks'][0]['reason'] == 'GEOMETRY_REQUIRES_DIRECTION_METADATA'


def test_public_quote_constants_are_available_to_the_downstream_compiler():
    assert MAX_PUBLIC_CITATIONS == 4 and MAX_PUBLIC_QUOTE_CHARS == 1800
