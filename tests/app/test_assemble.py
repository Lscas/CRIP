from app.assemble import (
    SUPPORT_NOTE_MAX_LENGTH,
    SUPPORT_NOTE_TRUNCATION,
    bounded_support_note,
    envelopes,
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


def test_material_inherits_structured_spec_section_from_evidence_locator():
    atom = {
        'condition': None, 'exception': None, 'candidate_key': 'R2A',
        'evidence_ids': ['EV-test'], 'subject': 'Copper Water Pipe',
        'category': 'MATERIAL', 'object': 'Copper Water Pipe', 'action': 'Provide',
        'properties': [],
    }
    evidence = {'evidence_id': 'EV-test', 'raw_text': 'Provide copper water pipe.',
                'locator': {'section': 'SECTION 22 11 16 > PART 2 - PRODUCTS'}}

    assert material(atom,evidence)['csi_sections']==['22 11 16']


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


def test_quality_rejects_property_citation_from_unrelated_document_context():
    atom={'candidate_key':'R4','category':'MATERIAL','subject':'Copper Pipe','action':'Provide',
          'object':'Copper Pipe','condition':None,'exception':None,'parent_requirement_key':None,
          'option_group_key':None,'option_relation':'NONE','evidence_ids':['EV-1'],
          'context_evidence_ids':['EV-2'],'needs_context':False,'properties':[
              {'name':'diameter','value':'2','unit':'inches','evidence_ids':['EV-2']}]}
    evidence=[
        {'evidence_id':'EV-1','document_id':'DOC-1','locator':{'page_number':1,'section':'SECTION 22 11 16'}},
        {'evidence_id':'EV-2','document_id':'DOC-2','locator':{'page_number':1,'section':'SECTION 22 11 16'}},
    ]

    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},evidence)

    assert checked['requirements'][0]['properties']==[]
    assert 'PROPERTY_RELATION' in flags

    evidence[1]={'evidence_id':'EV-2','document_id':'DOC-1',
                 'locator':{'page_number':1,'section':'SECTION 23 05 00'}}
    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},evidence)
    assert checked['requirements'][0]['properties']==[] and 'PROPERTY_RELATION' in flags


def test_rfi_question_cannot_become_material_but_explicit_response_can():
    atom={'candidate_key':'R5','category':'MATERIAL','subject':'Copper Pipe','action':'Provide',
          'object':'Copper Pipe','condition':None,'exception':None,'parent_requirement_key':None,
          'option_group_key':None,'option_relation':'NONE','evidence_ids':['EV-Q'],
          'context_evidence_ids':[],'needs_context':False,'properties':[]}
    question={'evidence_id':'EV-Q','document_id':'DOC-RFI','raw_text':'May PVC pipe be used?',
              'locator':{'section':'RFI 042 > QUESTION'}}
    response={'evidence_id':'EV-R','document_id':'DOC-RFI','raw_text':'Provide Type L copper pipe.',
              'locator':{'section':'RFI 042 > RESPONSE'}}

    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},[question,response])
    assert checked['requirements']==[] and flags==['RFI_QUESTION_SOURCE']

    atom['evidence_ids']=['EV-R']
    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},[question,response])
    assert len(checked['requirements'])==1 and flags==[]
    candidate=material(atom,response)
    assert candidate['requirement_status']=='CONDITIONAL'
    assert candidate['condition']=='RFI response source; contractual effect requires review.'


def test_submittal_status_is_review_visible_and_rejected_source_is_not_current_candidate():
    atom={'candidate_key':'R6','category':'MATERIAL','subject':'Air Handling Unit','action':'Provide',
          'object':'Air Handling Unit','condition':None,'exception':None,'parent_requirement_key':None,
          'option_group_key':None,'option_relation':'NONE','evidence_ids':['EV-S'],
          'context_evidence_ids':[],'needs_context':False,'properties':[]}
    pending={'evidence_id':'EV-S','document_id':'DOC-S','raw_text':'AHU-1 product data',
             'locator':{'section':'SUBMITTAL 23-09-23 > STATUS: PENDING'}}
    candidate=material(atom,pending)
    assert candidate['requirement_status']=='CONDITIONAL'
    assert candidate['condition']=='Submittal status: PENDING.'

    rejected={**pending,'locator':{'section':'SUBMITTAL 23-09-23 > STATUS: REJECTED'}}
    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},[rejected])
    assert checked['requirements']==[] and flags==['REJECTED_SUBMITTAL_SOURCE']


def test_email_headers_cannot_become_material_without_body_evidence():
    atom={'candidate_key':'R7','category':'MATERIAL','subject':'Copper Pipe','action':'Provide',
          'object':'Copper Pipe','condition':None,'exception':None,'parent_requirement_key':None,
          'option_group_key':None,'option_relation':'NONE','evidence_ids':['EV-H'],
          'context_evidence_ids':[],'needs_context':False,'properties':[]}
    header={'evidence_id':'EV-H','document_id':'DOC-E','raw_text':'Subject: RFI 101 - copper pipe',
            'locator':{'section':'EMAIL > HEADERS > RFI 101 > QUESTION'}}

    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},[header])

    assert checked['requirements']==[] and flags==['EMAIL_HEADER_SOURCE']


def test_email_quoted_history_cannot_become_current_requirement_or_property():
    atom={'candidate_key':'R8','category':'MATERIAL','subject':'Copper Pipe','action':'Provide',
          'object':'Copper Pipe','condition':None,'exception':None,'parent_requirement_key':None,
          'option_group_key':None,'option_relation':'NONE','evidence_ids':['EV-C'],
          'context_evidence_ids':['EV-Q'],'needs_context':False,'properties':[
              {'name':'diameter','value':'2','unit':'inches','evidence_ids':['EV-Q']}]}
    current={'evidence_id':'EV-C','document_id':'DOC-E','raw_text':'Provide copper pipe.',
             'locator':{'section':'EMAIL > BODY > RFI 8 > RESPONSE'}}
    quoted={'evidence_id':'EV-Q','document_id':'DOC-E','raw_text':'Use 2 inch copper pipe.',
            'locator':{'section':'EMAIL > QUOTED HISTORY > RFI 8 > RESPONSE'}}

    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},[current,quoted])
    assert checked['requirements'][0]['properties']==[]
    assert flags==['EMAIL_QUOTED_HISTORY_PROPERTY']

    atom['evidence_ids']=['EV-Q'];atom['properties']=[]
    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},[current,quoted])
    assert checked['requirements']==[] and flags==['EMAIL_QUOTED_HISTORY_SOURCE']


def test_mixed_workflow_evidence_keeps_only_valid_direct_support():
    atom={'candidate_key':'R9','category':'MATERIAL','subject':'Copper Pipe','action':'Provide',
          'object':'Copper Pipe','condition':None,'exception':None,'parent_requirement_key':None,
          'option_group_key':None,'option_relation':'NONE','evidence_ids':['EV-SPEC','EV-REJECTED'],
          'context_evidence_ids':[],'needs_context':False,'properties':[
              {'name':'diameter','value':'2','unit':'inches','evidence_ids':['EV-SPEC','EV-QUOTED']}]}
    spec={'evidence_id':'EV-SPEC','document_id':'DOC-SPEC','raw_text':'Provide 2 inch copper pipe.',
          'locator':{'section':'SECTION 22 11 16 > PART 2 - PRODUCTS'}}
    rejected={'evidence_id':'EV-REJECTED','document_id':'DOC-S','raw_text':'Copper pipe product data.',
              'locator':{'section':'SUBMITTAL 22-01 > STATUS: REJECTED'}}
    quoted={'evidence_id':'EV-QUOTED','document_id':'DOC-SPEC','raw_text':'Earlier message: use copper pipe.',
            'locator':{'section':'EMAIL > QUOTED HISTORY'}}

    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},
        [spec,rejected,quoted])

    kept=checked['requirements'][0]
    assert kept['evidence_ids']==['EV-SPEC']
    assert kept['properties'][0]['evidence_ids']==['EV-SPEC']
    assert flags==['EMAIL_QUOTED_HISTORY_PROPERTY','REJECTED_SUBMITTAL_SOURCE']
    assert material(kept,spec,[spec])['requirement_status']=='CONFIRMED'
    validate_schema('extraction-result',checked)


def test_mixed_rfi_question_and_response_keeps_only_response():
    atom={'candidate_key':'R10','category':'MATERIAL','subject':'Copper Pipe','action':'Provide',
          'object':'Copper Pipe','condition':None,'exception':None,'parent_requirement_key':None,
          'option_group_key':None,'option_relation':'NONE','evidence_ids':['EV-Q','EV-R'],
          'context_evidence_ids':[],'needs_context':False,'properties':[]}
    question={'evidence_id':'EV-Q','document_id':'DOC-RFI','raw_text':'May PVC pipe be used?',
              'locator':{'section':'RFI 042 > QUESTION'}}
    response={'evidence_id':'EV-R','document_id':'DOC-RFI','raw_text':'Provide Type L copper pipe.',
              'locator':{'section':'RFI 042 > RESPONSE'}}

    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},[question,response])

    assert checked['requirements'][0]['evidence_ids']==['EV-R']
    assert flags==['RFI_QUESTION_SOURCE']
    candidate=material(checked['requirements'][0],response,[response])
    assert candidate['requirement_status']=='CONDITIONAL'
    assert candidate['condition']=='RFI response source; contractual effect requires review.'


def test_any_remaining_workflow_source_makes_candidate_conditional():
    atom={'candidate_key':'R11','category':'MATERIAL','subject':'Air Handling Unit','action':'Provide',
          'object':'Air Handling Unit','condition':None,'exception':None,'parent_requirement_key':None,
          'option_group_key':None,'option_relation':'NONE','evidence_ids':['EV-SPEC','EV-S'],
          'context_evidence_ids':[],'needs_context':False,'properties':[]}
    spec={'evidence_id':'EV-SPEC','document_id':'DOC-SPEC','raw_text':'Provide air handling unit.',
          'locator':{'section':'SECTION 23 73 00 > PART 2 - PRODUCTS'}}
    pending={'evidence_id':'EV-S','document_id':'DOC-S','raw_text':'AHU-1 product data.',
             'locator':{'section':'SUBMITTAL 23-09-23 > STATUS: PENDING'}}
    for source in (spec,pending):
        source.update(tenant_id='local',project_id='P1',input_snapshot_id='S1',internal_revision_date=None)

    checked,flags=apply_deterministic_quality(
        {'disposition':'CANDIDATES','requirements':[atom],'reason':'Source extraction.'},[spec,pending])
    records={source['evidence_id']:source for source in (spec,pending)}
    run={'id':'RUN-1','project_id':'P1','snapshot_id':'S1','provider':'mock','model':'mock-no-network'}
    candidate=list(envelopes(run,records,[(spec,checked)],[]))[0]['candidate']

    assert flags==[]
    assert candidate['requirement_status']=='CONDITIONAL'
    assert candidate['condition']=='Submittal status: PENDING.'
    assert candidate['csi_sections']==['23 73 00']
