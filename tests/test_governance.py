"""规格包治理的结构测试，非远端Git配置证明。"""
import copy
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

def test_paid_api_disabled():
    assert not load_json('config/model_routing.json')['live_api_enabled'];assert not load_json('config/provider_price_snapshot.json')['confirmed_for_user_endpoint']

def test_qa_not_inferred_from_assembly():assert all(not r['qa_requirements'] for r in load_json('config/assembly_rules.json')['rules'])
def test_yaml_templates_parse():
    for p in (ROOT/'.github').rglob('*.yml'):assert isinstance(yaml.safe_load(p.read_text(encoding='utf-8')),dict)

def test_change_without_record_fails():assert check_diff(['config/model_routing.json'],[],{'FR-API-003'})
def test_product_implementation_cannot_claim_spec_only():
    r=copy.deepcopy(load_json('changes/CR-0002.json'));assert check_diff(['app/main.py'],[r],set(r['requirement_ids']))

def test_implementation_requires_test_diff_and_trace():
    r=copy.deepcopy(load_json('changes/CR-0002.json'));r['change_type']='implementation';r['affected_paths']=['app/**'];assert len(check_diff(['app/main.py'],[r],set(r['requirement_ids'])))>=2

def test_unknown_requirement_fails():
    r=copy.deepcopy(load_json('changes/CR-0002.json'));assert check_diff(['config/model_routing.json'],[r],set())

def test_valid_spec_change_covers_paths():
    r=load_json('changes/CR-0002.json');assert check_diff(['config/model_routing.json','docs/PRODUCT_SPEC.md','tests/test_policies.py'],[r],set(r['requirement_ids']))==[]
