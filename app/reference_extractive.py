"""Offline extractive contract core; deliberately absent from runtime dispatch.

This verifies source-copy integrity and arithmetic, NOT relevance, object/leader
relationships, field safety, or answer completeness. Every compiled candidate is
review-required, even if its source references and part mappings are valid.
"""
from __future__ import annotations

import re
from decimal import Context, Inexact, localcontext

from app.project_qa_v2 import (
    _NUMBER, _calculation_value, _decimal_text, validate_answer_v2,
)
from app.reference_source_blocks import (
    MAX_PUBLIC_CITATIONS, MAX_PUBLIC_QUOTE_CHARS,
    authenticate_source_blocks, build_source_blocks, model_source_blocks,
)
from contracts.runtime_rules import validate_schema

CONTRACT_VERSION='reference-extractive-candidate-1'
_REVIEW='OBJECT_CONDITION_RELEVANCE_AND_COMPLETENESS_REQUIRE_REVIEW'
_CODES=frozenset({
    'extractive_empty','extractive_block_unknown','extractive_block_duplicate',
    'extractive_part_unknown','extractive_part_missing','extractive_parts_invalid',
    'extractive_manifest_mismatch','extractive_number_unknown',
    'extractive_citation_limit','extractive_output_limit',
    'extractive_operand_duplicate','extractive_arithmetic_inexact',
})


class ExtractiveContractError(ValueError):
    """Closed diagnostics contain neither provider text nor project content."""
    def __init__(self,code:str):
        if code not in _CODES:raise ValueError('Unknown extractive diagnostic code')
        super().__init__(code)
        self.validator=code


def _parts(required_parts:list[dict])->list[str]:
    if (not isinstance(required_parts,list) or not 1<=len(required_parts)<=12
            or any(not isinstance(p,dict) or set(p)!={'part_ref','text'}
                   or not isinstance(p['part_ref'],str)
                   or not re.fullmatch(r'P[1-9][0-9]*',p['part_ref'])
                   or not isinstance(p['text'],str) or not p['text'].strip()
                   for p in required_parts)):
        raise ExtractiveContractError('extractive_parts_invalid')
    refs=[p['part_ref'] for p in required_parts]
    if len(set(refs))!=len(refs):raise ExtractiveContractError('extractive_parts_invalid')
    return refs


def _numbers(block:dict)->list[dict]:
    """Bind each numeric occurrence to its precise parent-source offsets.

    N references are scoped to a B reference. Identical values at different
    source positions intentionally have different N references. The provider
    chooses an occurrence; it cannot supply or replace its numeric value.
    """
    values=[]
    for span in block['spans']:
        for match in _NUMBER.finditer(span['text']):
            source_text=match.group(0)
            values.append({
                'number_ref':f'N{len(values)+1}',
                'value':source_text.replace(',','').lstrip('+'),
                'source_text':source_text,
                'evidence_id':span['evidence_id'],
                'start':span['start']+match.start(),'end':span['start']+match.end(),
            })
    return values


def candidate_input(rows:list[dict],required_parts:list[dict])->tuple[dict,dict]:
    """Build an offline selection view. No model, ledger or DB operation occurs."""
    _parts(required_parts)
    manifest=build_source_blocks(rows)
    view=model_source_blocks(manifest)
    indexed={b['block_ref']:b for b in manifest['blocks'] if b['status']=='BOUND'}
    for block in view['blocks']:
        block['numbers']=[{key:item[key] for key in ('number_ref','value','source_text')}
                          for item in _numbers(indexed[block['block_ref']])]
    view.update(contract_version=CONTRACT_VERSION,
                required_parts=[dict(p) for p in required_parts],
                unusable_source_count=sum(b['status']!='BOUND' for b in manifest['blocks']))
    return manifest,view


def _citations(block:dict)->list[dict]:
    # Never widen a quote to an entire E row to bypass the public citation cap.
    citations=[]
    for span in block['spans']:
        citation={'type':'TEXT','evidence_id':span['evidence_id'],'quote':span['text']}
        if not span['text'] or len(span['text'])>MAX_PUBLIC_QUOTE_CHARS:
            raise ExtractiveContractError('extractive_citation_limit')
        if citation not in citations:citations.append(citation)
    if not 1<=len(citations)<=MAX_PUBLIC_CITATIONS:
        raise ExtractiveContractError('extractive_citation_limit')
    return citations


def compile_extractive_candidate(data:dict,rows:list[dict],question:str,
                                 required_parts:list[dict],manifest:dict|None=None)->dict:
    """Select whole excerpts and calculate locally; never create a verified answer.

    The manifest protects B/N alias identity against stale or modified sources.
    This is not a provider execution receipt or production replay authority.
    """
    validate_schema('reference-extractive-candidate',data)
    expected=_parts(required_parts)
    if manifest is None:manifest=build_source_blocks(rows)
    authenticate_source_blocks(manifest,rows)
    if data['manifest_sha256']!=manifest['manifest_sha256']:
        raise ExtractiveContractError('extractive_manifest_mismatch')
    indexed={b['block_ref']:b for b in manifest['blocks'] if b['status']=='BOUND'}
    if not data['facts'] and not data['calculations']:
        raise ExtractiveContractError('extractive_empty')
    if len(data['facts'])+len(data['calculations'])>12:
        raise ExtractiveContractError('extractive_output_limit')
    coverage={p:{'part_ref':p,'claim_indexes':[],'calculation_indexes':[]} for p in expected}
    def block_for(reference):
        block=indexed.get(reference)
        if block is None:raise ExtractiveContractError('extractive_block_unknown')
        return block
    def bind(parts,claim_index,calculation_index=None):
        for part in parts:
            if part not in coverage:raise ExtractiveContractError('extractive_part_unknown')
            coverage[part]['claim_indexes'].append(claim_index)
            if calculation_index is not None:
                coverage[part]['calculation_indexes'].append(calculation_index)

    claims=[];calculations=[];operand_sources=[];seen=set()
    for fact in data['facts']:
        reference=fact['block_ref']
        if reference in seen:raise ExtractiveContractError('extractive_block_duplicate')
        seen.add(reference);block=block_for(reference)
        bind(fact['part_refs'],len(claims))
        claims.append({'text':block['text'],'citations':_citations(block)})

    # Fixed decimal context prevents callers' ambient precision changing output.
    # Public output bounds still fail closed: no rounded/truncated source values.
    with localcontext(Context(prec=80)) as context:
        context.traps[Inexact]=True
        for item in data['calculations']:
            operands=[];citations=[];bindings=[];occurrences=set()
            for operand in item['operands']:
                occurrence=(operand['block_ref'],operand['number_ref'])
                if occurrence in occurrences:
                    raise ExtractiveContractError('extractive_operand_duplicate')
                occurrences.add(occurrence)
                block=block_for(operand['block_ref'])
                numbers={n['number_ref']:n for n in _numbers(block)}
                number=numbers.get(operand['number_ref'])
                if number is None:raise ExtractiveContractError('extractive_number_unknown')
                operands.append(number['value'])
                bindings.append({'block_ref':block['block_ref'],**number})
                for citation in _citations(block):
                    if citation not in citations:citations.append(citation)
            if len(citations)>MAX_PUBLIC_CITATIONS:
                raise ExtractiveContractError('extractive_citation_limit')
            try:result=_decimal_text(_calculation_value(item['operator'],operands))
            except Inexact as exc:
                raise ExtractiveContractError('extractive_arithmetic_inexact') from exc
            calculation={'operator':item['operator'],'operands':operands,
                         'result':result,'citations':citations}
            if item['operator']=='PERCENT_OF':
                expression=f'{operands[0]} × {operands[1]}%'
            else:
                symbol={'ADD':'+','SUBTRACT':'-','MULTIPLY':'×','DIVIDE':'÷'}[item['operator']]
                expression=f' {symbol} '.join(operands)
            bind(item['part_refs'],len(claims),len(calculations))
            calculations.append(calculation);operand_sources.append(bindings)
            claims.append({'text':f'{expression} = {result}.','citations':citations})
        if any(not p['claim_indexes'] for p in coverage.values()):
            raise ExtractiveContractError('extractive_part_missing')
        answer={'status':'PARTIAL','answer':' '.join(c['text'] for c in claims),
                'claims':claims,'calculations':calculations,'missing':[_REVIEW]}
        validate_answer_v2(answer,rows,question)
    return {
        'contract_version':CONTRACT_VERSION,'status':'REVIEW_REQUIRED',
        'manifest_sha256':manifest['manifest_sha256'],'answer':answer,
        'coverage':list(coverage.values()),'operand_sources':operand_sources,
        'verification':{
            'source_excerpt_integrity':True,'local_arithmetic_verified':True,
            'part_mapping_complete':True,'object_condition_relations_verified':False,
            'answer_completeness_verified':False,'text_direction_verified':False,
        },
    }
