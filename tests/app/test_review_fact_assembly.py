"""Regression coverage for fact-preserving material assembly."""

from app.assemble import _inspection_atom_allowed, _material_atom_allowed, envelopes, material
import pytest
from contracts.runtime_rules import validate_schema


def _evidence(evidence_id, revision_date):
    return {
        'evidence_id': evidence_id, 'document_id': 'DOC-' + evidence_id,
        'tenant_id': 'local', 'project_id': 'P', 'input_snapshot_id': 'S',
        'internal_revision_date': revision_date, 'raw_text': 'Synthetic pump evidence.',
        'locator': {'section': 'SECTION 22 11 16'},
    }


def _atom(candidate_key, evidence_id, quantity, pressure, *, scope=None):
    properties = [
        {'name': 'quantity', 'value': str(quantity), 'unit': 'EA', 'evidence_ids': [evidence_id]},
        {'name': 'pressure', 'value': str(pressure), 'unit': 'psi', 'evidence_ids': [evidence_id]},
    ]
    if scope is not None:
        properties.append({'name': 'building', 'value': scope, 'unit': None, 'evidence_ids': [evidence_id]})
    return {
        'candidate_key': candidate_key, 'category': 'MATERIAL', 'subject': 'P-1',
        'action': 'Provide', 'object': 'Pump', 'condition': None, 'exception': None,
        'parent_requirement_key': None, 'option_group_key': None, 'option_relation': 'NONE',
        'evidence_ids': [evidence_id], 'context_evidence_ids': [], 'needs_context': False,
        'properties': properties,
    }


def _assembled(*entries):
    evidence = {item['evidence_id']: item for item, _ in entries}
    run = {'id': 'R', 'project_id': 'P', 'snapshot_id': 'S', 'provider': 'mock', 'model': 'mock'}
    return list(envelopes(run, evidence, [(item, {'requirements': [atom]}) for item, atom in entries], []))


def test_model_number_and_number_of_poles_remain_design_properties():
    for name, value in [('model_number', '12345'), ('Number of poles', '3')]:
        atom = _atom('A', 'EV-1', 1, 100)
        atom['properties'] = [{'name': name, 'value': value, 'unit': None, 'evidence_ids': ['EV-1']}]
        candidate = material(atom, _evidence('EV-1', '2026-01-01'))
        assert candidate['quantity'] is None
        assert [(prop['name'], prop['value']) for prop in candidate['design_properties']] == [(name, value)]


def test_newer_quantity_replaces_older_quantity_for_same_explicit_scope():
    old = _evidence('EV-old', '2026-01-01')
    new = _evidence('EV-new', '2026-02-01')
    record = _assembled((old, _atom('OLD', 'EV-old', 2, 100, scope='Building A')),
                        (new, _atom('NEW', 'EV-new', 5, 200, scope='Building A')))[0]['candidate']
    assert record['quantity']['value'] == 5
    assert record['quantity']['evidence_ids'] == ['EV-new']
    assert [(prop['name'], prop['value']) for prop in record['design_properties']] == [
        ('pressure', '200'), ('building', 'Building A')]


def test_unknown_scope_does_not_merge_tagged_material_across_evidence():
    old = _evidence('EV-old', '2026-01-01')
    new = _evidence('EV-new', '2026-02-01')
    rows = _assembled((old, _atom('OLD', 'EV-old', 2, 100)),
                      (new, _atom('NEW', 'EV-new', 5, 200)))
    assert len(rows) == 2


def test_equal_date_quantity_disagreement_is_not_silently_selected():
    left = _evidence('EV-left', '2026-02-01')
    right = _evidence('EV-right', '2026-02-01')
    record = _assembled((left, _atom('LEFT', 'EV-left', 2, 100, scope='Building A')),
                        (right, _atom('RIGHT', 'EV-right', 5, 100, scope='Building A')))[0]['candidate']
    assert record['requirement_status'] == 'NOT_SPECIFIED'
    assert record['quantity'] is None
    quantities = [(prop['value'], prop['evidence_ids']) for prop in record['design_properties']
                  if prop['name'] == 'quantity']
    assert quantities == [('2', ['EV-left']), ('5', ['EV-right'])]


def test_missing_revision_date_or_newer_omission_leaves_quantity_unresolved():
    dated = _evidence('EV-dated', '2026-01-01')
    unknown = _evidence('EV-unknown', None)
    record = _assembled((dated, _atom('DATED', 'EV-dated', 2, 100, scope='Building A')),
                        (unknown, _atom('UNKNOWN', 'EV-unknown', 5, 100, scope='Building A')))[0]['candidate']
    assert record['requirement_status'] == 'NOT_SPECIFIED'
    assert record['quantity'] is None
    assert [(prop['value'], prop['evidence_ids']) for prop in record['design_properties']
            if prop['name'] == 'quantity'] == [('2', ['EV-dated']), ('5', ['EV-unknown'])]


def test_newer_evidence_without_quantity_does_not_retain_old_quantity():
    old = _evidence('EV-old', '2026-01-01')
    new = _evidence('EV-new', '2026-02-01')
    no_quantity = _atom('NEW', 'EV-new', 5, 100, scope='Building A')
    no_quantity['properties'] = [prop for prop in no_quantity['properties'] if prop['name'] != 'quantity']
    record = _assembled((old, _atom('OLD', 'EV-old', 2, 100, scope='Building A')),
                        (new, no_quantity))[0]['candidate']
    assert record['requirement_status'] == 'NOT_SPECIFIED'
    assert record['quantity'] is None
    assert [(prop['value'], prop['evidence_ids']) for prop in record['design_properties']
            if prop['name'] == 'quantity'] == [('2', ['EV-old'])]


def test_same_quantity_value_merges_its_evidence_ids():
    old = _evidence('EV-old', '2026-01-01')
    new = _evidence('EV-new', '2026-02-01')
    record = _assembled((old, _atom('OLD', 'EV-old', 2, 100, scope='Building A')),
                        (new, _atom('NEW', 'EV-new', 2, 100, scope='Building A')))[0]['candidate']
    assert record['quantity']['value'] == 2
    assert record['quantity']['evidence_ids'] == ['EV-new', 'EV-old']


def test_temporary_explicit_quantity_is_retained_as_property_not_invalid_quantity():
    atom = _atom('T', 'EV-T', 2, 100)
    atom['object'] = 'temporary formwork'
    candidate = material(atom, _evidence('EV-T', '2026-01-01'))
    assert candidate['material_kind'] == 'TEMPORARY'
    assert candidate['quantity'] is None
    assert any(prop['name'] == 'quantity' and prop['value'] == '2' for prop in candidate['design_properties'])
    validate_schema('material-item', candidate)


def test_material_primary_object_filter_keeps_physical_manual_and_spare_parts_items():
    for name in ('Manual isolation valve', 'Manual Transfer Switch', 'Owner-furnished flow meter',
                 'Spare parts kit for pump'):
        assert _material_atom_allowed({
            'subject': 'Owner' if name.startswith('Owner-') else name,
            'object': name, 'action': 'Provide', 'properties': [],
        })


@pytest.mark.parametrize('name', ['Spare parts', 'Manual Transfer Switch', 'Manual isolation valve'])
def test_material_primary_object_filter_keeps_physical_manual_or_spare_items(name):
    assert _material_atom_allowed({'subject': name, 'object': 'Provide ' + name,
                                   'action': 'Provide', 'properties': []})


@pytest.mark.parametrize('name', ['Operation and maintenance manual for pump', 'Shop drawings for pump'])
def test_material_primary_object_filter_rejects_clear_document_objects(name):
    assert not _material_atom_allowed({'subject': name, 'object': 'Submit ' + name,
                                       'action': 'Submit', 'properties': []})


@pytest.mark.parametrize('field', ['floor', 'level', 'system'])
def test_floor_or_system_alone_is_not_a_shared_installation_scope(field):
    entries=[]
    for number in (1, 2):
        eid=f'EV-{number}'
        atom=_atom(f'A-{number}',eid,number,100)
        atom['properties'].append({'name':field,'value':'1','unit':None,'evidence_ids':[eid]})
        entries.append((_evidence(eid,'2026-01-01'),atom))
    assert len(_assembled(*entries))==2


def test_ambiguous_building_scope_does_not_authorize_merge():
    entries=[]
    for number in (1,2):
        eid=f'EV-{number}';atom=_atom(f'A-{number}',eid,number,100,scope='Building A')
        atom['properties'].append({'name':'building','value':'Building B','unit':None,'evidence_ids':[eid]})
        entries.append((_evidence(eid,'2026-01-01'),atom))
    assert len(_assembled(*entries))==2


def test_conflicting_quantity_fields_do_not_depend_on_property_order():
    atom=_atom('A','EV-1',2,100,scope='Building A')
    atom['properties'].append({'name':'total_count','value':'5','unit':'EA','evidence_ids':['EV-1']})
    for properties in (atom['properties'],list(reversed(atom['properties']))):
        candidate=material({**atom,'properties':properties},_evidence('EV-1','2026-01-01'))
        assert candidate['quantity'] is None and candidate['requirement_status']=='NOT_SPECIFIED'
        assert {(p['name'],p['value']) for p in candidate['design_properties']
                if p['name'] in {'quantity','total_count'}}=={('quantity','2'),('total_count','5')}


@pytest.mark.parametrize('unknown',['TBD','2 to 5','not stated'])
def test_non_numeric_quantity_field_is_not_overridden_by_numeric_count(unknown):
    atom=_atom('A','EV-1',unknown,100,scope='Building A')
    atom['properties'].append({'name':'count','value':'5','unit':'EA','evidence_ids':['EV-1']})
    for properties in (atom['properties'],list(reversed(atom['properties']))):
        candidate=material({**atom,'properties':properties},_evidence('EV-1','2026-01-01'))
        assert candidate['quantity'] is None and candidate['requirement_status']=='NOT_SPECIFIED'
        assert {(p['name'],p['value']) for p in candidate['design_properties']
                if p['name'] in {'quantity','count'}}=={('quantity',unknown),('count','5')}


def test_latest_quantity_cohort_merges_all_citations_and_is_order_independent():
    entries=[(_evidence('EV-old','2026-01-01'),_atom('old','EV-old',2,100,scope='Building A')),
             (_evidence('EV-new-a','2026-02-01'),_atom('new-a','EV-new-a',5,200,scope='Building A')),
             (_evidence('EV-new-b','2026-02-01'),_atom('new-b','EV-new-b',5,200,scope='Building A'))]
    forward=_assembled(*entries)[0]['candidate']
    backward=_assembled(*reversed(entries))[0]['candidate']
    assert forward==backward
    assert set(forward['quantity']['evidence_ids'])=={'EV-new-a','EV-new-b'}


def test_same_tag_in_distinct_buildings_stays_separate():
    left=_atom('A','EV-A',2,100,scope='Building A')
    right=_atom('B','EV-B',5,200,scope='Building B')
    rows=_assembled((_evidence('EV-A','2026-01-01'),left),(_evidence('EV-B','2026-02-01'),right))
    assert len(rows)==2
    assert len({r['candidate']['candidate_key'] for r in rows})==2


def test_normalized_tagged_names_cannot_create_duplicate_record_keys():
    left=_atom('A','EV-A',2,100,scope='Building A')
    right=_atom('B','EV-B',2,200,scope='Building A')
    left['object']='minimum pressure';right['object']='maximum pressure'
    left['properties'][1]['name']='minimum_pressure'
    right['properties'][1]['name']='maximum_pressure'
    rows=_assembled((_evidence('EV-A','2026-01-01'),left),(_evidence('EV-B','2026-01-01'),right))
    assert len(rows)==1
    assert {'minimum_pressure','maximum_pressure'} <= {p['name'] for p in rows[0]['candidate']['design_properties']}


@pytest.mark.parametrize('requirement', ['Leakage test at no additional cost',
                                       'Hydrostatic test using approved product data'])
def test_assembly_keeps_real_qa_despite_administrative_modifiers(requirement):
    assert _inspection_atom_allowed({'category':'TEST','subject':'Water pipe','action':'Test',
                                     'object':requirement,'properties':[]})
