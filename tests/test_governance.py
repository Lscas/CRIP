"""规格包治理的结构测试，非远端Git配置证明。"""
import copy
import pytest
import yaml
from contracts.runtime_rules import ROOT,load_json
from scripts.check_spec_sync import check
from scripts.check_changes import check_diff
from scripts.render_requirements import render

def test_repository_spec_structure():assert check()==[]
def test_rendered_requirements_are_current():assert (ROOT/'docs/REQUIREMENTS.md').read_text(encoding='utf-8')==render(load_json('spec/requirements.json'))
def test_all_legacy_ids_preserved():
    old={r['id'] for r in load_json('baseline/requirements.v0.1.0.json')['requirements']};new={r['id'] for r in load_json('spec/requirements.json')['requirements']};assert old<=new

def test_no_false_implementation_claims():
    req=load_json('spec/requirements.json')
    assert req['application_status']=='prototype_bootstrap'
    assert next(r for r in req['requirements'] if r['id']=='PRD-SCOPE-001')['status']=='planned'
    for entry in load_json('spec/traceability.json')['entries']:
        if entry['implementation_status']=='implemented':
            assert entry['verification_report'] and entry['planned_product_tests']
            assert all((ROOT/p).exists() for p in entry['implemented_product_paths'])

def test_prompts_are_real_text_files():
    for p in load_json('prompts/manifest.json')['prompts']:
        assert len((ROOT/p['path']).read_text(encoding='utf-8'))>50;assert (ROOT/p['schema']).is_file()

def test_projection_protocol_prompts_have_independent_manifest_identities():
    from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1,PROJECTION_PROTOCOL_V2
    entries=load_json('prompts/manifest.json')['prompts']
    for prompt_id,protocol in (
            ('project-projection-loop',PROJECTION_PROTOCOL_V1),
            ('project-projection-loop-v2',PROJECTION_PROTOCOL_V2)):
        matches=[entry for entry in entries if entry['id']==prompt_id]
        assert len(matches)==1
        entry=matches[0]
        assert entry['version']==protocol.decision_contract_version
        assert entry['path']==protocol.prompt_path
        assert entry['schema']==f'spec/schemas/{protocol.schema_name}.schema.json'
        assert entry['role']=='cheap'
        assert entry['status'].startswith('internal_')
        assert 'not_public_or_live_evaluated' in entry['status']

def test_reviewer_facing_model_text_is_requested_in_english():
    for path in ('prompts/common.md','prompts/joint-extraction/system.md','prompts/drawing-crop/system.md',
                 'prompts/claim-verification/system.md','prompts/evidence-verification/system.md'):
        text=(ROOT/path).read_text(encoding='utf-8')
        assert 'English' in text or '英文' in text

def test_default_routing_is_mock_and_budget_configs_are_removed():
    assert not load_json('config/model_routing.json')['live_api_enabled']
    assert not (ROOT/'config/budget_policy.json').exists()
    assert not (ROOT/'config/provider_price_snapshot.json').exists()

def test_qa_not_inferred_from_assembly():assert all(not r['qa_requirements'] for r in load_json('config/assembly_rules.json')['rules'])
def test_yaml_templates_parse():
    for p in (ROOT/'.github').rglob('*.yml'):assert isinstance(yaml.safe_load(p.read_text(encoding='utf-8')),dict)

def test_change_without_record_fails():assert check_diff(['config/model_routing.json'],[],{'FR-API-003'})
def test_product_implementation_cannot_claim_spec_only():
    r=copy.deepcopy(load_json('changes/CR-0002.json'));r['affected_paths']=['app/**'];assert check_diff(['app/main.py'],[r],set(r['requirement_ids']))

@pytest.mark.parametrize('change_type',['implementation','bugfix'])
def test_mixed_spec_and_implementation_records_can_cover_the_same_diff(change_type):
    spec=copy.deepcopy(load_json('changes/CR-0002.json'));spec['affected_paths']=['docs/**']
    implementation=copy.deepcopy(load_json('changes/CR-0002.json'));implementation['change_type']=change_type;implementation['affected_paths']=['app/**','tests/**','spec/**']
    paths=['app/main.py','docs/PRODUCT_SPEC.md','tests/test_policies.py','spec/traceability.json']
    assert check_diff(paths,[spec,implementation],set(spec['requirement_ids']))==[]

def test_spec_only_record_that_does_not_cover_product_code_leaves_it_uncovered():
    r=copy.deepcopy(load_json('changes/CR-0002.json'));r['affected_paths']=['docs/**']
    assert '变更清单未覆盖: app/main.py' in check_diff(['app/main.py'],[r],set(r['requirement_ids']))

def test_spec_only_record_cannot_cover_product_code_with_glob():
    r=copy.deepcopy(load_json('changes/CR-0002.json'));r['affected_paths']=['**']
    assert '产品代码改动不能伪报spec_only' in check_diff(['app/main.py'],[r],set(r['requirement_ids']))

def test_spec_only_record_cannot_share_explicit_app_path_with_implementation():
    spec=copy.deepcopy(load_json('changes/CR-0002.json'));spec['affected_paths']=['app/**']
    implementation=copy.deepcopy(spec);implementation['change_type']='implementation';implementation['affected_paths']=['app/**','tests/**','spec/**','docs/**']
    paths=['app/main.py','docs/PRODUCT_SPEC.md','tests/test_policies.py','spec/traceability.json']
    assert '产品代码改动不能伪报spec_only' in check_diff(paths,[spec,implementation],set(spec['requirement_ids']))

@pytest.mark.parametrize('product_path',['app/main.py','web/app.js'])
@pytest.mark.parametrize('change_type',['implementation','bugfix'])
def test_implementation_record_does_not_mask_false_spec_only_claim(product_path,change_type):
    spec=copy.deepcopy(load_json('changes/CR-0002.json'));spec['affected_paths']=['**']
    implementation=copy.deepcopy(spec);implementation['change_type']=change_type
    paths=[product_path,'docs/PRODUCT_SPEC.md','tests/test_policies.py','spec/traceability.json']
    assert '产品代码改动不能伪报spec_only' in check_diff(paths,[spec,implementation],set(spec['requirement_ids']))

def test_implementation_requires_test_diff_and_trace():
    r=copy.deepcopy(load_json('changes/CR-0002.json'));r['change_type']='implementation';r['affected_paths']=['app/**'];assert len(check_diff(['app/main.py'],[r],set(r['requirement_ids'])))>=2

def test_unknown_requirement_fails():
    r=copy.deepcopy(load_json('changes/CR-0002.json'));assert check_diff(['config/model_routing.json'],[r],set())

def test_valid_spec_change_covers_paths():
    r=load_json('changes/CR-0002.json');assert check_diff(['config/model_routing.json','docs/PRODUCT_SPEC.md','tests/test_policies.py'],[r],set(r['requirement_ids']))==[]
