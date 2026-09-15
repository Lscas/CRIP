from app.assemble import (
    SUPPORT_NOTE_MAX_LENGTH,
    SUPPORT_NOTE_TRUNCATION,
    bounded_support_note,
    inspection,
    material,
    _inspection_atom_allowed,
    _material_atom_allowed,
)
from contracts.runtime_rules import validate_schema


def test_generated_inspection_support_note_is_bounded_without_losing_source_extraction():
    long_value = "quality-control-detail-" * 40
    atom = {
        "condition": None,
        "exception": None,
        "candidate_key": "R1",
        "evidence_ids": ["EV-test"],
        "subject": "Quality control report",
        "properties": [{
            "name": "content_elements",
            "value": long_value,
            "unit": None,
            "evidence_ids": ["EV-test"],
        }],
        "category": "REPORT",
        "object": "daily report",
        "action": "Submit",
    }
    evidence = {"evidence_id": "EV-test", "raw_text": "Daily report requirements."}

    candidate = inspection(atom, evidence)
    candidate["support_note"] = bounded_support_note(candidate["support_note"])

    assert len(candidate["support_note"]) <= SUPPORT_NOTE_MAX_LENGTH
    assert candidate["support_note"].endswith(SUPPORT_NOTE_TRUNCATION)
    assert atom["properties"][0]["value"] == long_value
    validate_schema("inspection-item", candidate)


def test_material_uses_entity_name_and_keeps_dimension_and_quantity_fields():
    atom = {
        'condition': None, 'exception': None, 'candidate_key': 'R2',
        'evidence_ids': ['EV-test'], 'subject': 'Concrete Equipment Base',
        'category': 'MATERIAL', 'object': 'equipment', 'action': 'Provide',
        'properties': [
            {'name': 'thickness', 'value': '4', 'unit': 'inches', 'evidence_ids': ['EV-test']},
            {'name': 'extension', 'value': '3', 'unit': 'inches', 'evidence_ids': ['EV-test']},
            {'name': 'quantity', 'value': '2', 'unit': 'EA', 'evidence_ids': ['EV-test']},
            {'name': 'specification_section', 'value': '03 30 00', 'unit': None, 'evidence_ids': ['EV-test']},
        ],
    }
    evidence = {'evidence_id': 'EV-test', 'raw_text': 'Section 03 30 00 concrete equipment bases.'}

    assert _material_atom_allowed(atom)
    candidate = material(atom, evidence)

    assert candidate['name'] == 'Concrete Equipment Base'
    assert candidate['quantity']['value'] == 2
    assert candidate['quantity']['basis'] == 'DESIGN_NET'
    assert candidate['csi_sections'] == ['03 30 00']
    assert [(p['name'], p['value'], p['unit']) for p in candidate['design_properties']] == [
        ('thickness', '4', 'inches'), ('extension', '3', 'inches')
    ]


def test_non_qa_wiring_diagram_is_not_assembled_as_an_inspection():
    atom = {
        'subject': 'Contractor', 'action': 'Submit',
        'object': 'a schematic wiring diagram for each controlled system',
    }

    assert not _inspection_atom_allowed(atom)
