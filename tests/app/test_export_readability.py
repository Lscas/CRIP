import pytest

from app import exporter
from scripts import build_export_translation_map as translation_map
from scripts import validate_readable_export as validator


def test_translation_filter_rejects_collapsed_and_number_losing_values():
    generic = 'This is one long generic translation that is clearly unrelated to two distinct engineering source phrases.'
    values = {
        '贯穿孔数量: 6 个': generic,
        '质量保证声明': generic,
        '起重机报告': generic,
        '承包商': 'Contractor',
        '合同方': 'Contractor',
        '保修期限: 5 年': 'Warranty period: five years',
    }

    filtered = exporter._filter_translations(values)

    assert '贯穿孔数量: 6 个' not in filtered
    assert '质量保证声明' not in filtered
    assert filtered['承包商'] == 'Contractor'
    assert filtered['合同方'] == 'Contractor'
    assert filtered['保修期限: 5 年'] == 'Warranty period: five years'


def test_translation_filter_rejects_unrelated_visual_page_disclaimer():
    source = '贯穿防火封堵系统位于空气静压箱内'
    generic = ('This input contains a visual description of a drawing page, and does not include '
               'any independent extractable requirements.')

    assert source not in exporter._filter_translations({source: generic})
    assert not translation_map.translation_is_safe(source, generic)


def test_translation_script_detects_collisions_and_preserves_numeric_meaning():
    generic = 'This is one long generic translation that is clearly unrelated to two distinct engineering source phrases.'
    assert translation_map.translation_is_safe('贯穿孔数量: 6 个', 'Through-hole quantity: six')
    assert not translation_map.translation_is_safe('贯穿孔数量: 6 个', 'Through-hole quantity')
    assert translation_map.suspicious_collisions({'甲': generic, '乙': generic, '丙': generic})


def test_visible_text_removes_internal_references_and_format_codes():
    value = ('Document ID DOC-2d86289745244047bf1f9508bbb28268; '
             '(EV-34dd88a7f5f8d01e06f9d7f2) temperature_range_min: 75(cid:176)F\ufffd')

    result = exporter._sanitize_visible_text(value, humanize_codes=True)

    assert 'DOC-' not in result
    assert 'EV-' not in result
    assert '(cid:' not in result
    assert '\ufffd' not in result
    assert 'temperature range min' in result
    assert '75°F' in result


def test_visible_text_maps_verified_pdf_private_use_engineering_symbols():
    result = exporter._sanitize_visible_text('90\uf0b0F; optical fiber 8.3/3 \uf06dm')

    assert result == '90°F; optical fiber 8.3/3 μm'
    assert not validator.PRIVATE_USE.search(result)


def test_validator_recognizes_unmapped_private_use_glyphs():
    assert validator.PRIVATE_USE.search('unmapped \ue123 glyph')
    assert not validator.PRIVATE_USE.search('90°F and 8.3/3 μm')


def test_mixed_visual_evidence_keeps_english_source_passage():
    text = 'Important visible text: Concrete Anchors: ACI 318-05 and ACI-355.2. 视觉说明需要人工复核。'

    result = exporter._evidence_text('Visual model observation', text)

    assert 'Concrete Anchors: ACI 318-05 and ACI-355.2.' in result
    assert not exporter._CJK.search(result)


def test_visual_evidence_removes_non_english_punctuation_empty_fragments_and_raw_codes():
    text = ('Important visible text: Sheet E104. Visual observation:, 、 、 / 。 '
            'Visual limitation:,,scale_text null。 Visual observation: panel A、panel B。')

    result = exporter._evidence_text('Visual model observation', text)

    assert '，' not in result and '。' not in result and '、' not in result
    assert 'scale_text' not in result and 'scale text' not in result and 'null' not in result
    assert 'Visual observation: panel A, panel B' in result
    assert 'Visual observation: ,' not in result


def test_visible_sheet_identifier_is_not_damaged_and_uppercase_codes_are_humanized():
    text = ('Visual page type: SPEC_PAGE. Visible Sheet identifier: E104. '
            'Status NEEDS_CONTEXT; rule ONE_OF; position FRAGMENT_START; Sheet_id E104.')

    result = exporter._evidence_text('Visual model observation', text)

    assert 'Visible Sheet identifier: E104' in result
    assert 'Specification page' in result
    assert 'Needs context' in result and 'One of' in result and 'Fragment start' in result
    assert not validator.MACHINE_CODE.search(result)


def test_visual_conflict_has_distinct_recorded_values_and_source_passage():
    readable = {'conflicts': [{'statements': [
        {'value': 'ACI 318-05', 'source_type': 'Visual model observation',
         'evidence_text': 'Concrete Anchors: ACI 318-05 and ACI-355.2.'},
        {'value': 'ACI-355.2', 'source_type': 'Visual model observation',
         'evidence_text': 'Concrete Anchors: ACI 318-05 and ACI-355.2.'},
    ]}]}

    exporter._finalize_conflicts(readable)
    first, second = readable['conflicts'][0]['statements']

    assert first['evidence_text'] != second['evidence_text']
    assert 'Recorded visual value: ACI 318-05.' in first['evidence_text']
    assert 'Recorded visual value: ACI-355.2.' in second['evidence_text']
    assert 'Source passage:' in first['evidence_text']


def test_conflict_finalization_removes_exact_duplicate_statements_only():
    repeated = {'value': '1/2', 'source': 'Drawing.pdf', 'location': 'Page 12',
                'revision_date': '', 'source_type': 'Source text',
                'evidence_text': 'Scale: 1/2'}
    readable = {'conflicts': [{'statements': [
        dict(repeated), dict(repeated),
        {**repeated, 'value': '3/4', 'evidence_text': 'Scale: 3/4'},
        {**repeated, 'source': 'Specification.pdf'},
    ]}]}

    exporter._finalize_conflicts(readable)

    statements = readable['conflicts'][0]['statements']
    assert len(statements) == 3
    assert [statement['value'] for statement in statements] == ['1/2', '3/4', '1/2']
    assert statements[0]['source'] != statements[2]['source']


def test_english_export_fails_closed_on_uncached_chinese(monkeypatch):
    monkeypatch.setattr(exporter, '_load_translations', lambda: {})

    with pytest.raises(ValueError, match='English export cache is incomplete'):
        exporter._translate_display({'name': '质量保证声明'})


def test_repeated_pdf_page_diagnostics_are_condensed():
    repeated = ';'.join(f'PDF第{page}页含图像/线条/大幅面;已排入视觉分析。' for page in range(1, 12))
    record = {
        'candidate': {
            'subject': 'Project Specifications',
            'missing_field_or_document': repeated,
            'blocking_reason': 'Review is required.',
            'recommended_action': repeated,
        },
        'review': {'status': 'PENDING'},
        'meta': {},
    }

    item = exporter._missing_item({'verifications': {}}, record, {})

    assert item['what_is_missing'].startswith('11 PDF pages were flagged for visual review')
    assert 'page span 1-11' in item['what_is_missing']
    assert 'compare local OCR text' in item['recommended_action']
    assert not exporter._CJK.search(item['what_is_missing'] + item['recommended_action'])


def test_machine_property_names_are_humanized():
    result = exporter._properties({'design_properties': [
        {'name': 'temperature_range_min', 'value': '75', 'unit': '°F'},
    ]})

    assert result == 'Temperature Range Min: 75 °F'
    assert not validator.MACHINE_CODE.search(result)


def test_known_quantity_unit_overrides_legacy_translation_cache():
    candidate = {'design_properties': [
        {'name': '贯穿孔数量', 'value': '6', 'unit': '个'},
    ]}

    assert exporter._quantity(candidate) == (6, 'EA', 'Stated in document')
    assert exporter._translate_display({'unit': 'EA'})['unit'] == 'EA'


@pytest.mark.parametrize(('name', 'quantity', 'expected', 'minimum'), [
    ('one (1) 1-inch conduit as spare', 1, '1-inch conduit as spare', False),
    ('two battery strings', 2, 'battery strings', False),
    ('(2) 1-3/4-inch x 9-1/2-inch LVL', 2, '1-3/4-inch x 9-1/2-inch LVL', False),
    ('at least two portable fire extinguishers', 2, 'portable fire extinguishers', True),
    ('single NEMA 6-30R receptacle', 1, 'NEMA 6-30R receptacle', False),
    ('CELL PLATE WITH SLEEVE ANCHORS (4) AT 5 FT O.C.', 4,
     'CELL PLATE WITH SLEEVE ANCHORS AT 5 FT O.C.', False),
])
def test_material_name_does_not_repeat_separate_quantity(name, quantity, expected, minimum):
    assert exporter._separate_name_quantity(name, quantity) == (expected, minimum)


def test_validator_finds_embedded_internal_references():
    assert validator.INTERNAL_ID.search('Fragment EV-34dd88a7f5f8d01e06f9d7f2 requires review.')
    assert not validator.INTERNAL_ID.search('[REC-TAMP]: NEMA receptacle')


def test_xlsx_evidence_cells_use_concise_numbered_excerpts():
    items = [
        {'source': 'Project-Specifications.pdf', 'location': f'Page {page}', 'text': 'A' * 400}
        for page in range(1, 6)
    ]

    sources, texts = exporter._evidence_cells(items)

    assert sources.count('\n') == 2
    assert len(texts.splitlines()[0]) < 330
    assert '2 additional evidence entries are available in the JSON export and application.' in texts


def test_reviewer_material_name_uses_entity_and_keeps_attribute_out_of_name():
    candidate = {
        'name': 'insulation thickness 1 inch',
        'entity_ids': ['Refrigerant Liquid Pipe Insulation'],
        'location': None,
        'design_properties': [{'name': 'pipe_size', 'value': 'Up to 1', 'unit': 'inch'}],
    }

    name, location = exporter._reviewer_material_name(candidate, {})

    assert name == 'Refrigerant Liquid Pipe Insulation'
    assert location == ''
    assert 'Insulation Thickness: 1 inch' in exporter._properties(candidate)


def test_reviewer_material_name_repairs_water_service_schedule_heading():
    candidate = {
        'name': 'water service pipe material and size',
        'entity_ids': ['W4 at 1610 PAGE ST combined domestic/fire water service'],
        'location': None,
        'design_properties': [
            {'name': 'type', 'value': 'COMBINED DOMESTIC/FIRE WATER SERVICE', 'unit': None},
            {'name': 'material', 'value': 'COPPER', 'unit': None},
            {'name': 'size', 'value': '2', 'unit': 'inch'},
        ],
    }

    name, location = exporter._reviewer_material_name(candidate, {})

    assert name == 'Combined Domestic/Fire Water Service Pipe'
    assert location == 'W4 at 1610 PAGE ST'
    assert 'Material: Copper' in exporter._properties(candidate)
    assert 'Size: 2 inch' in exporter._properties(candidate)


def test_reviewer_rejects_administrative_diagram_as_test_report():
    candidate = {
        'qa_type': 'TEST_REPORT', 'activity': 'Contractor',
        'requirement': 'Submit a schematic wiring diagram for each controlled system',
        'report_name': 'a schematic wiring diagram for each controlled system',
    }

    name = exporter._inspection_name(candidate)
    activity = exporter._inspection_activity(candidate)

    assert not exporter._reviewer_inspection_allowed(candidate, name, activity)


def test_inspection_item_exposes_specification_and_performer():
    candidate = {
        'qa_type': 'FIELD_TEST', 'activity': 'Contractor',
        'requirement': 'Perform hydrostatic pressure test on domestic water piping',
        'report_name': None, 'performer_as_stated': 'Contractor',
        'witness_as_stated': 'Owner', 'csi_sections': ['22 11 16'],
        'timing': 'Before concealment', 'frequency': None,
        'acceptance_criteria': 'No pressure loss for 2 hours',
        'standard_reference': None, 'requirement_status': 'CONFIRMED',
        'evidence_ids': [], 'entity_ids': ['Contractor'], 'location': None,
    }
    record = {'kind': 'INSPECTION', 'candidate': candidate, 'meta': {'record_id': 'test'},
              'review': {'status': 'PENDING'}}

    item = exporter._inspection_item({'verifications': {}}, record, {})

    assert item['test_or_inspection_name'] == 'Hydrostatic pressure test on domestic water piping'
    assert item['specification_section'] == '22 11 16'
    assert item['performer'] == 'Contractor'
    assert item['witness'] == 'Owner'


def test_dimension_only_material_name_uses_the_actual_entity():
    candidate = {
        'name': '#5 bars @ 32" O.C.', 'entity_ids': ['Horizontal Reinforcement'],
        'location': None, 'design_properties': [
            {'name': 'bar_size', 'value': '#5', 'unit': None},
            {'name': 'spacing', 'value': '32', 'unit': 'in O.C.'},
        ],
    }

    name, _ = exporter._reviewer_material_name(candidate, {})

    assert name == 'Horizontal Reinforcement'
    assert 'Bar Size: #5' in exporter._properties(candidate)
    assert 'Spacing: 32 in O.C.' in exporter._properties(candidate)


def test_fallback_evidence_starts_at_the_supporting_clause():
    evidence = {
        'raw_text': ('Schedule of values and administrative material. More unrelated text. '
                     'The manufacturer shall inspect and certify equipment installation before startup. '
                     'Closeout requirements follow.'),
        'file_name': 'Project-Specifications.pdf', 'content_basis': 'TEXT_LAYER',
    }
    record = {'candidate': {'requirement': 'inspect and certify equipment installation'}}

    item = exporter._fallback_evidence(evidence, record=record)

    assert item['text'].startswith('The manufacturer shall inspect and certify equipment installation')
    assert 'Schedule of values' not in item['text']


def test_hanger_size_instruction_is_a_property_not_the_material_name():
    candidate = {
        'name': 'one size', 'entity_ids': ['Rods'], 'location': None,
        'condition': 'for double rod hangers', 'design_properties': [],
    }

    name, _ = exporter._reviewer_material_name(candidate, {})

    assert name == 'Double-Rod Hanger Rods'
    assert exporter._properties(candidate) == (
        'Rod Size Adjustment: One size smaller than the single-rod hanger requirement'
    )
