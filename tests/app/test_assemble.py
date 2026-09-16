from app.assemble import (
    SUPPORT_NOTE_MAX_LENGTH,
    SUPPORT_NOTE_TRUNCATION,
    bounded_support_note,
    inspection,
    material,
    _inspection_atom_allowed,
    _material_atom_allowed,
    apply_deterministic_quality,
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


def test_deterministic_quality_removes_false_items_and_misaligned_properties():
    valid = {
        'candidate_key':'R1','category':'MATERIAL','subject':'Copper Water Pipe','action':'Provide',
        'object':'Copper Water Pipe','condition':None,'exception':None,'parent_requirement_key':None,
        'option_group_key':None,'option_relation':'NONE','evidence_ids':['EV-1'],
        'context_evidence_ids':[],'needs_context':False,'properties':[
            {'name':'diameter','value':'2','unit':'inches','evidence_ids':['EV-1']},
            {'name':'diameter','value':'2','unit':'inches','evidence_ids':['EV-1']},
            {'name':'material_name','value':'Copper Water Pipe','unit':None,'evidence_ids':['EV-1']},
            {'name':'pressure','value':'150','unit':'psi','evidence_ids':['EV-2']},
        ],
    }
    bad_material={**valid,'candidate_key':'R2','subject':'Contractor','object':'18 gauge','properties':[]}
    bad_test={**valid,'candidate_key':'R3','category':'TEST','subject':'Contractor','action':'Submit',
              'object':'a schematic wiring diagram for each controlled system','properties':[]}
    data={'disposition':'CANDIDATES','requirements':[valid,bad_material,bad_test],'reason':'Source extraction.'}

    checked,flags=apply_deterministic_quality(data)

    assert checked['disposition']=='TRUNCATED' and len(checked['requirements'])==1
    assert checked['requirements'][0]['properties']==[
        {'name':'diameter','value':'2','unit':'inches','evidence_ids':['EV-1']}
    ]
    assert flags==['DUPLICATE_PROPERTY','MATERIAL_NAME','PROPERTY_ROLE','PROPERTY_SCOPE','QA_ACTIVITY']
    assert 'Deterministic quality checks' in checked['reason']
    validate_schema('extraction-result',checked)


def test_quality_downgrades_child_when_its_invalid_parent_is_removed():
    parent={'candidate_key':'R1','category':'MATERIAL','subject':'Contractor','action':'Provide',
            'object':'18 gauge','properties':[],'condition':None,'exception':None,
            'parent_requirement_key':None,'option_group_key':None,'option_relation':'NONE',
            'evidence_ids':['EV-1'],'context_evidence_ids':[],'needs_context':False}
    child={**parent,'candidate_key':'R2','category':'OTHER_REQUIREMENT','subject':'Contractor',
           'object':'Coordinate installation','parent_requirement_key':'R1'}

    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[parent,child],'reason':'Source extraction.'})

    assert len(checked['requirements'])==1 and checked['requirements'][0]['candidate_key']=='R2'
    assert checked['requirements'][0]['parent_requirement_key'] is None
    assert checked['requirements'][0]['needs_context'] is True and 'PARENT_SCOPE' in flags
    validate_schema('extraction-result',checked)
