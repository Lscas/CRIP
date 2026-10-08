"""Source-text-free numeric evidence diagnostics for rejected QA answers."""
from __future__ import annotations

import json

_REASONS=frozenset({'CLAIM_NUMBER_UNSUPPORTED','OPERAND_UNSUPPORTED'})
_COMMON=frozenset({
    'reason','text_citation_count','image_citation_count',
    'number_seen_in_cited_source','number_seen_in_supplied_text',
})
_REQUIRED={
    'CLAIM_NUMBER_UNSUPPORTED':_COMMON|{'claim_index','numeric_index'},
    'OPERAND_UNSUPPORTED':_COMMON|{'calculation_index','operand_index'},
}
_MAX_INDEX=1_000_000
_MAX_CITATION_COUNT=1_000_000
_LEGACY_VALIDATORS={
    ('PROJECT_ANSWER','CLAIM_NUMBER_UNSUPPORTED'):'citation_scope',
    ('PROJECT_ANSWER','OPERAND_UNSUPPORTED'):'calculation',
    ('PROJECT_EVIDENCE_DECISION','CLAIM_NUMBER_UNSUPPORTED'):'numeric_support',
    ('PROJECT_EVIDENCE_DECISION','OPERAND_UNSUPPORTED'):'calculation_operand_support',
}
_DECISION_VALIDATORS=frozenset({
    'answer_part_missing','answer_part_empty_mapping',
    'answer_part_duplicate_index','answer_part_invalid_claim_index',
    'answer_part_invalid_calculation_index','answer_part_unmapped_claim',
    'answer_part_unmapped_calculation','answer_empty','answer_atomic_claim_limit',
    'citation_reference_outside','citation_source_unavailable','citation_region_outside',
})


class NumericEvidenceError(ValueError):
    """An existing numeric validation failure with safe, structured context."""
    def __init__(self,message:str,semantic_detail:dict):
        super().__init__(message)
        self.semantic_detail=semantic_detail


class DecisionContractError(ValueError):
    """A closed, source-text-free local decision-contract classification."""
    def __init__(self,message:str,validator:str):
        if validator not in _DECISION_VALIDATORS:
            raise ValueError('Decision diagnostic validator is not allowed')
        super().__init__(message)
        self.validator=validator


def valid_semantic_detail(value:object)->bool:
    """Accept only the closed, source-text-free diagnostic projection."""
    if not isinstance(value,dict):return False
    reason=value.get('reason')
    if not isinstance(reason,str) or reason not in _REASONS:return False
    if set(value)!=_REQUIRED[reason]:return False
    indexes=('claim_index','numeric_index') if reason=='CLAIM_NUMBER_UNSUPPORTED' else ('calculation_index','operand_index')
    if any(type(value[key]) is not int or not 0<=value[key]<=_MAX_INDEX for key in indexes):return False
    for key in ('text_citation_count','image_citation_count'):
        if type(value[key]) is not int or not 0<=value[key]<=_MAX_CITATION_COUNT:return False
    if not all(type(value[key]) is bool for key in
               ('number_seen_in_cited_source','number_seen_in_supplied_text')):return False
    try:
        return len(json.dumps(value,separators=(',',':'),ensure_ascii=True).encode('utf-8'))<=1024
    except (TypeError,ValueError):
        return False


def valid_numeric_diagnostic(value:object)->bool:
    """Bind the new detail to the unchanged legacy error classification."""
    if (not isinstance(value,dict)
            or set(value)!={'kind','class','exception','validator','semantic_detail'}
            or value.get('kind')!='CONTRACT_ERROR' or value.get('exception')!='ValueError'
            or not isinstance(value.get('class'),str)
            or not valid_semantic_detail(value.get('semantic_detail'))):
        return False
    expected=_LEGACY_VALIDATORS.get((value['class'],value['semantic_detail']['reason']))
    return expected is not None and value.get('validator')==expected
