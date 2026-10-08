"""No provider, database, private fixture or production selector is used here."""
from copy import deepcopy

import pytest
from jsonschema import ValidationError

from app.reference_extractive import (
    candidate_input, compile_extractive_candidate, ExtractiveContractError,
)


def source(text, *, evidence_id='A', x=10, y=10):
    # Exact source offsets with deterministic native-word geometry.
    mapping=[];at=0
    for line_no,line in enumerate(text.splitlines()):
        left=x
        for word in line.split():
            start=text.index(word,at);end=start+len(word)
            mapping.append({'start':start,'end':end,
                            'bbox':[left,y+line_no*12,left+len(word)*5,y+line_no*12+10]})
            left+=len(word)*5+4;at=end
    return {'evidence_id':evidence_id,'document_id':'DOC',
            'locator':{'page_number':1},'extraction_method':'TEXT_LAYER',
            'raw_text':text,'text_map':mapping}


def bundle(rows, parts=None):
    parts=parts or [{'part_ref':'P1','text':'The requested source excerpts.'}]
    manifest,model_input=candidate_input(rows,parts)
    return manifest,model_input,{
        'contract_version':'reference-extractive-candidate-1',
        'manifest_sha256':manifest['manifest_sha256'],
        'facts':[], 'calculations':[],
    },parts


def test_cross_object_rewrite_cannot_be_represented():
    rows=[source('Use 2 anchors at bracket A.'),
          source('Seal bracket B only if exposed.',evidence_id='B',x=400)]
    manifest,view,data,parts=bundle(rows)
    assert len(view['blocks'])==2
    data['facts']=[{'block_ref':b['block_ref'],'part_refs':['P1']} for b in view['blocks']]
    result=compile_extractive_candidate(data,rows,'How should bracket B be fixed?',parts,manifest)
    assert result['status']=='REVIEW_REQUIRED'
    assert result['answer']['status']=='PARTIAL'
    assert [c['text'] for c in result['answer']['claims']]==[b['text'] for b in view['blocks']]
    assert 'all conditions' not in result['answer']['answer']
    assert not result['verification']['object_condition_relations_verified']
    assert not result['verification']['answer_completeness_verified']
    data['facts'][0]['text']='Use 2 anchors at bracket B in all conditions.'
    with pytest.raises(ValidationError):
        compile_extractive_candidate(data,rows,'How should bracket B be fixed?',parts,manifest)


def test_condition_and_quantifier_copied_whole_not_paraphrased():
    rows=[source('(2) PANELS, TYP.'),
          source('(E) PANELS\nREPAIR IF OCCURS',evidence_id='B',x=400)]
    manifest,view,data,parts=bundle(rows)
    selected=next(b for b in view['blocks'] if 'IF OCCURS' in b['text'])
    data['facts']=[{'block_ref':selected['block_ref'],'part_refs':['P1']}]
    result=compile_extractive_candidate(data,rows,'State the repair note.',parts,manifest)
    assert result['answer']['claims'][0]['text']==selected['text']
    assert 'IF OCCURS' in result['answer']['answer']
    assert '(2)' not in result['answer']['answer']
    for citation in result['answer']['claims'][0]['citations']:
        assert citation['quote'] in rows[1]['raw_text']


@pytest.mark.parametrize('mutation,code',[
    ('empty','extractive_empty'),('unknown_block','extractive_block_unknown'),
    ('duplicate_block','extractive_block_duplicate'),
    ('unknown_part','extractive_part_unknown'),('missing_part','extractive_part_missing'),
    ('wrong_manifest','extractive_manifest_mismatch'),
])
def test_fail_closed_contract_cases(mutation,code):
    rows=[source('Use 2 anchors at bracket A.')]
    manifest,view,data,parts=bundle(rows)
    data['facts']=[{'block_ref':view['blocks'][0]['block_ref'],'part_refs':['P1']}]
    if mutation=='empty':data['facts']=[]
    elif mutation=='unknown_block':data['facts'][0]['block_ref']='B999'
    elif mutation=='duplicate_block':data['facts']*=2
    elif mutation=='unknown_part':data['facts'][0]['part_refs']=['P999']
    elif mutation=='missing_part':parts.append({'part_ref':'P2','text':'A second part.'})
    elif mutation=='wrong_manifest':data['manifest_sha256']='0'*64
    with pytest.raises(ExtractiveContractError) as exc:
        compile_extractive_candidate(data,rows,'What is the source note?',parts,manifest)
    assert exc.value.validator==code
    assert 'bracket' not in str(exc.value)


def test_stale_block_aliases_and_manifest_tamper_rejected():
    rows=[source('Use 2 anchors at bracket A.'),
          source('Seal bracket B only if exposed.',evidence_id='B',x=400)]
    manifest,view,data,parts=bundle(rows)
    data['facts']=[{'block_ref':view['blocks'][0]['block_ref'],'part_refs':['P1']}]
    with pytest.raises(ValueError):
        compile_extractive_candidate(data,list(reversed(rows)),'Show notes.',parts,manifest)
    tampered=deepcopy(manifest);tampered['blocks'][0]['text']='Use 2 anchors at bracket B.'
    with pytest.raises(ValueError):
        compile_extractive_candidate(data,rows,'Show notes.',parts,tampered)


def test_parts_bound_directly_and_coverage_built_by_application():
    rows=[source('Note A.'),source('Note B.',evidence_id='B',x=400)]
    parts=[{'part_ref':'P1','text':'First part.'},{'part_ref':'P2','text':'Second part.'}]
    manifest,view,data,parts=bundle(rows,parts)
    data['facts']=[{'block_ref':b['block_ref'],'part_refs':[parts[i]['part_ref']]}
                   for i,b in enumerate(view['blocks'])]
    result=compile_extractive_candidate(data,rows,'Show both notes.',parts,manifest)
    assert result['coverage']==[
        {'part_ref':'P1','claim_indexes':[0],'calculation_indexes':[]},
        {'part_ref':'P2','claim_indexes':[1],'calculation_indexes':[]},
    ]
    assert result['verification']['part_mapping_complete'] is True
    assert result['verification']['answer_completeness_verified'] is False


def test_local_arithmetic_selects_exact_numeric_occurrences_no_model_values():
    rows=[source('Duration A: 589 days.'),source('Duration B: 367 days.',evidence_id='B',x=400)]
    manifest,view,data,parts=bundle(rows)
    data['calculations']=[{'part_refs':['P1'],'operator':'SUBTRACT','operands':[
        {'block_ref':b['block_ref'],'number_ref':b['numbers'][0]['number_ref']}
        for b in view['blocks']]}]
    result=compile_extractive_candidate(data,rows,'Calculate the difference.',parts,manifest)
    assert result['answer']['calculations'][0]['result']=='222'
    assert result['answer']['claims'][0]['text']=='589 - 367 = 222.'
    assert [b['value'] for b in result['operand_sources'][0]]==['589','367']
    for b in result['operand_sources'][0]:
        row=next(r for r in rows if r['evidence_id']==b['evidence_id'])
        assert row['raw_text'][b['start']:b['end']]==b['source_text']
    data['calculations'][0]['operands'][0]['value']='367'
    with pytest.raises(ValidationError):
        compile_extractive_candidate(data,rows,'Calculate the difference.',parts,manifest)


def test_same_value_occurrences_have_separate_exact_bindings():
    rows=[source('A: 10. B: 10.')]
    manifest,view,data,parts=bundle(rows)
    numbers=view['blocks'][0]['numbers']
    assert [n['number_ref'] for n in numbers]==['N1','N2']
    assert [n['value'] for n in numbers]==['10','10']
    data['calculations']=[{'part_refs':['P1'],'operator':'ADD','operands':[
        {'block_ref':view['blocks'][0]['block_ref'],'number_ref':n['number_ref']} for n in numbers]}]
    result=compile_extractive_candidate(data,rows,'Calculate the total.',parts,manifest)
    first,second=result['operand_sources'][0]
    assert first['start']!=second['start']
    assert result['answer']['calculations'][0]['result']=='20'


def test_one_source_occurrence_cannot_be_counted_twice():
    rows=[source('A: 10.')]
    manifest,view,data,parts=bundle(rows)
    operand={'block_ref':view['blocks'][0]['block_ref'],'number_ref':'N1'}
    data['calculations']=[{'part_refs':['P1'],'operator':'ADD','operands':[operand,dict(operand)]}]
    with pytest.raises((ValueError,ValidationError)):
        compile_extractive_candidate(data,rows,'Calculate the total.',parts,manifest)


@pytest.mark.parametrize('mode',['unknown_number','cross_block','not_requested','stated_result','zero_division'])
def test_arithmetic_rejections(mode):
    rows=[source('A: 10, alternate 20.'),source('B: 0.',evidence_id='B',x=400)]
    manifest,view,data,parts=bundle(rows)
    refs=[b['block_ref'] for b in view['blocks']]
    calc={'part_refs':['P1'],'operator':'ADD','operands':[
        {'block_ref':refs[0],'number_ref':'N2'},{'block_ref':refs[1],'number_ref':'N1'}]}
    data['calculations']=[calc];question='Calculate the total.'
    if mode=='unknown_number':calc['operands'][0]['number_ref']='N999'
    elif mode=='cross_block':calc['operands'][0]['block_ref']=refs[1]
    elif mode=='not_requested':question='Show the note.'
    elif mode=='stated_result':calc['result']='20'
    elif mode=='zero_division':calc['operator']='DIVIDE'
    with pytest.raises((ValueError,ValidationError)):
        compile_extractive_candidate(data,rows,question,parts,manifest)


def test_no_runtime_import_or_executable_v10_selector():
    from pathlib import Path
    from app.page_selector import SELECTOR_PAGE_LIMITS
    assert 'literal-page-selector-10' not in SELECTOR_PAGE_LIMITS
    for name in ('main.py','gateway.py','evidence_loop.py','reference_evaluation_jobs.py'):
        text=(Path(__file__).resolve().parents[2]/'app'/name).read_text(encoding='utf-8')
        assert 'reference_extractive' not in text
        assert 'reference_source_blocks' not in text
