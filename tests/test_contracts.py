"""合成契约测试，不是施工精度评测。"""
import copy
import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from contracts import runtime_rules
from contracts.runtime_rules import load_json, validate_schema, validate_candidate, EvidenceScope, wrap_candidate

def scope():
    evidence=load_json('examples/evidence.json')
    return EvidenceScope('T1','P1','SN1',{e['evidence_id']:e for e in evidence})
def sample(name='material'):
    return copy.deepcopy(load_json(f'examples/{name}.json'))

@pytest.mark.parametrize('case',load_json('examples/fixture_manifest.json')['cases'])
def test_positive_examples(case):
    validate_schema(case['schema'],load_json(case['file']))

@pytest.mark.parametrize('name,sc',[('material','material-item'),('material-options','material-item'),('inspection','inspection-item'),('conflict','conflict-item'),('missing','missing-information-item')])
def test_business_positive(name,sc):
    validate_candidate(sc,sample(name),scope())

@pytest.mark.parametrize('name,sc',[('material','material-item'),('inspection','inspection-item')])
def test_confirmed_without_evidence_rejected(name,sc):
    x=sample(name);x['evidence_ids']=[]
    with pytest.raises(ValidationError):validate_schema(sc,x)

@pytest.mark.parametrize('key,value',[('review_status','ACCEPTED'),('analysis_run_id','FAKE'),('model_id','fake'),('meta',{})])
def test_model_cannot_write_server_fields(key,value):
    x=sample();x[key]=value
    with pytest.raises(ValidationError):validate_schema('material-item',x)

def test_conditional_without_condition_rejected():
    x=sample();x['requirement_status']='CONDITIONAL'
    with pytest.raises(ValidationError):validate_schema('material-item',x)

def test_inferred_requires_rule():
    x=sample();x['requirement_status']='INFERRED_TO_VERIFY';x['evidence_ids']=[]
    with pytest.raises(ValidationError):validate_schema('material-item',x)
    x['inference_rule_id']='ASM-CONCRETE-PAD-001';validate_schema('material-item',x)

def test_fabricated_evidence_rejected():
    x=sample();x['evidence_ids']=['EV-fake']
    with pytest.raises(ValueError):validate_candidate('material-item',x,scope())

@pytest.mark.parametrize('field',['tenant_id','project_id','input_snapshot_id'])
def test_cross_scope_evidence_rejected(field):
    s=scope();records=copy.deepcopy(s.records);records['EV-2'][field]='OTHER'
    with pytest.raises(ValueError):validate_candidate('material-item',sample(),EvidenceScope('T1','P1','SN1',records))

def test_options_not_automatically_selected():
    x=sample('material-options');assert len(x['options'])==3 and x['selected_option_ids']==[]
    validate_candidate('material-item',x,scope())

def test_one_of_cannot_select_multiple():
    x=sample('material-options');x['selected_option_ids']=['A','B']
    with pytest.raises(ValidationError):validate_schema('material-item',x)

def test_selected_id_must_exist():
    x=sample('material-options');x['selected_option_ids']=['Z']
    with pytest.raises(ValueError):validate_candidate('material-item',x,scope())

def test_selected_requires_document_selection():
    x=sample('material-options');x['selected_option_ids']=['B']
    with pytest.raises(ValueError):validate_candidate('material-item',x,scope())
    x['options'][1]['role']='DOCUMENT_SELECTED';validate_candidate('material-item',x,scope())

def test_option_fields_need_field_sources():
    x=sample('material-options');x['options'][0]['field_evidence']={}
    with pytest.raises(ValueError):validate_candidate('material-item',x,scope())

def test_quantity_explicit_needs_no_scale():
    x=sample('material-quantity');assert x['quantity']['calibration'] is None
    validate_candidate('material-item',x,scope())

@pytest.mark.parametrize('method',['VECTOR_TAKEOFF','CAD_MEASUREMENT','IMAGE_CALIBRATED'])
def test_geometric_quantity_needs_calibration_and_formula(method):
    x=sample('material-quantity');x['quantity']['method']=method
    with pytest.raises(ValidationError):validate_schema('material-item',x)
    x['quantity']['formula']='COUNT_VERIFIED_INSTANCES'
    x['quantity']['operands']=[{'name':'已核验实体','value':1,'unit':'EA','evidence_ids':['EV-2']}]
    x['quantity']['calibration']={'view_id':'V1','ratio':10,'paper_unit':'mm','design_unit':'mm','verified':True,'evidence_ids':['EV-2']}
    validate_schema('material-item',x)

def test_procurement_quantity_not_allowed():
    x=sample('material-quantity');x['quantity']['basis']='WITH_WASTE'
    with pytest.raises(ValidationError):validate_schema('material-item',x)

def test_temporary_quantity_not_requested():
    x=sample('material-quantity');x['material_kind']='TEMPORARY'
    with pytest.raises(ValidationError):validate_schema('material-item',x)

def test_qa_criteria_needs_specific_evidence():
    x=sample('inspection');x['acceptance_criteria']='虚构验收值'
    with pytest.raises(ValueError):validate_candidate('inspection-item',x,scope())

def test_nonblocking_missing_not_allowed():
    x=sample('missing');x['blocks_current_task']=False
    with pytest.raises(ValidationError):validate_schema('missing-information-item',x)

def test_latest_selected_cannot_be_old():
    x=sample('conflict');x['selected_evidence_id']='EV-1'
    with pytest.raises(ValueError):validate_candidate('conflict-item',x,scope())

def test_conflict_different_scope_not_allowed():
    x=sample('conflict');x['claims'][1]['scope_key']='OTHER'
    with pytest.raises(ValueError):validate_candidate('conflict-item',x,scope())

def test_empty_extraction_must_explain():
    x=sample('extraction-empty');x['reason']=''
    with pytest.raises(ValidationError):validate_schema('extraction-result',x)

def test_server_wrapper_forces_pending():
    result=wrap_candidate('MATERIAL',sample(),sample('server-meta'),scope())
    assert result['review']['status']=='PENDING';validate_schema('record-envelope',result)

def test_accepted_record_needs_review_event_and_actor():
    x=sample('record-envelope');x['review']['status']='ACCEPTED'
    with pytest.raises(ValidationError):validate_schema('record-envelope',x)

def test_meta_requires_all_versions():
    x=sample('record-envelope');del x['meta']['analysis_run_id']
    with pytest.raises(ValidationError):validate_schema('record-envelope',x)


def test_model_cannot_forge_revision_date():
    x=sample('conflict');x['claims'][0]['internal_revision_date']='2026-03-01';x['selected_evidence_id']='EV-1'
    with pytest.raises(ValueError):validate_candidate('conflict-item',x,scope())


def test_inferred_status_does_not_allow_fabricated_design_values():
    x=sample();x['requirement_status']='INFERRED_TO_VERIFY';x['inference_rule_id']='ASM-CONCRETE-PAD-001'
    x['design_properties'][0]['evidence_ids']=[]
    with pytest.raises(ValueError):validate_candidate('material-item',x,scope())


def test_source_evidence_schemas():
    for e in load_json('examples/evidence.json'):
        validate_schema('evidence',e)


def _receipt(evidence_inputs):
    return {
        'receipt_version':'reference-model-input-receipt-1','model_call_id':'CALL-'+'a'*32,
        'round':1,'request_hash':'b'*64,'prompt_contract_hash':'c'*64,
        'question_hash':'d'*64,'provider':'mock','model':'mock-no-network',
        'api_protocol':'chat_completions','structured_output_mode':'json_object',
        'inference_mode':'disabled','cached':False,'source_text_included':False,
        'prompt_content_included':False,'chain_of_thought_included':False,
        'evidence_count':len(evidence_inputs),'evidence_inputs':evidence_inputs,
        'visual_inputs':[],'system_text_bytes':1,'user_text_bytes':1,'image_bytes':0,
        'request_upper_bound_bytes':2,'max_output_tokens':1,
    }


def test_receipt_unique_evidence_fast_path_skips_quadratic_fallback(monkeypatch):
    original=runtime_rules._BASE_UNIQUE_ITEMS;calls=[]
    def fallback(*args):
        calls.append(args[2])
        yield from original(*args)
    monkeypatch.setattr(runtime_rules,'_BASE_UNIQUE_ITEMS',fallback)
    inputs=[{'evidence_id':f'E-{index}','text_sha256':'a'*64} for index in range(8500)]
    assert list(runtime_rules._receipt_unique_items(Draft202012Validator({}),True,inputs,{}))==[]
    assert calls==[]  # The production schema separately limits v1 receipts to 256 inputs.


def test_receipt_unique_evidence_duplicate_is_rejected_and_irregular_inputs_fallback(monkeypatch):
    entry={'evidence_id':'E-1','text_sha256':'a'*64}
    with pytest.raises(ValidationError):
        validate_schema('reference-model-input-receipt',_receipt([entry,copy.deepcopy(entry)]))
    original=runtime_rules._BASE_UNIQUE_ITEMS;calls=[]
    def fallback(*args):
        calls.append(args[2])
        yield from original(*args)
    monkeypatch.setattr(runtime_rules,'_BASE_UNIQUE_ITEMS',fallback)
    validator=Draft202012Validator({})
    for instance in ([{'text_sha256':'a'*64}],['not-a-dict'],[
            {'evidence_id':'E-1','text_sha256':'a'*64},
            {'evidence_id':'E-1','text_sha256':'b'*64}]):
        expected=list(original(validator,True,instance,{}))
        actual=list(runtime_rules._receipt_unique_items(validator,True,instance,{}))
        assert [str(error) for error in actual]==[str(error) for error in expected]
    assert len(calls)==3
