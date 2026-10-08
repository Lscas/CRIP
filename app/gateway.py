"""Single model gateway: bounded JSON, durable call records, no silent retries."""
from __future__ import annotations
import hashlib
import base64
import json
import re
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
import httpx
from app.db import (Database, DomainError, MAX_PAID_TASK_CALLS, dumps,
                    paid_task_family, paid_task_key)
from app.settings import Settings, ROOT
from app.assemble import apply_deterministic_quality
from app.prompt_schema import expand_schema
from contracts.runtime_rules import EvidenceScope,evidence_references,validate_schema,cache_key

class ProviderPaused(DomainError):pass
class InvalidModelOutput(DomainError):
    def __init__(self,message:str,code:int=400,*,execution_receipt:dict|None=None,
                 failure_execution:dict|None=None):
        super().__init__(message,code);self.execution_receipt=execution_receipt
        self.failure_execution=failure_execution

_REQUEST_ID_HEADERS = ('x-request-id', 'x-goog-request-id')
_SAFE_REQUEST_ID = re.compile(r'^[A-Za-z0-9._:/=+-]{1,160}$')
_SAFE_RETRY_AFTER = re.compile(
    r'^(?:[0-9]{1,10}|[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT)$')
_SAFE_PROVIDER_CODE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
_ANSWER_CONTRACT_REASON_PATTERNS = (
    ('explicit entity','entity_scope'),
    ('calculation','calculation'),
    ('numeric','numeric_support'),
    ('measurement','measurement_support'),
    ('date','date_support'),
    ('drawing','drawing_support'),
    ('sheet','drawing_support'),
    ('paragraph','clause_support'),
    ('clause','clause_support'),
    ('email','email_support'),
    ('disposition','disposition_support'),
    ('workflow','workflow_support'),
    ('source finding','source_finding'),
    ('comparison','comparison'),
    ('citation','citation_scope'),
    ('quote','exact_quote'),
    ('原句','exact_quote'),
    ('引用','exact_quote'),
)
_EVIDENCE_DECISION_REASON_PATTERNS = (
    ('answer decision is inconsistent','answer_decision_consistency'),
    ('answer coverage is incomplete','answer_part_coverage'),
    ('coverage has an empty part mapping','answer_part_mapping'),
    ('coverage repeats an answer index','answer_part_mapping'),
    ('coverage has an invalid claim index','answer_part_mapping'),
    ('coverage has an invalid calculation index','answer_part_mapping'),
    ('answer contains unmapped answer content','answer_part_mapping'),
    ('non-answer decision cannot include an answer','terminal_answer_consistency'),
    ('non-answer decision must name missing facts','missing_facts'),
    ('need_evidence decision is inconsistent','evidence_request_consistency'),
    ('evidence request is duplicated','duplicate_request'),
    ('find_identifier query has no supported explicit identifier','identifier_request'),
    ('terminal non-answer decision cannot request evidence','terminal_request_consistency'),
    ('need_user_input decision is inconsistent','user_input_consistency'),
    ('cannot_answer decision is inconsistent','cannot_answer_consistency'),
    ('calculation operand is not in its cited source text','calculation_operand_support'),
    ('claim contains a number absent from its citations','numeric_support'),
    ('percentage calculation requires base and percentage operands','calculation_arity'),
    ('calculation result does not match local decimal arithmetic','calculation_result'),
    ('calculation operand is invalid','calculation_operand'),
    ('calculation result is invalid','calculation_result'),
)
_PROVIDER_ERROR_CODES = frozenset({
    'INVALID_ARGUMENT', 'FAILED_PRECONDITION', 'UNAUTHENTICATED', 'PERMISSION_DENIED',
    'NOT_FOUND', 'RESOURCE_EXHAUSTED', 'CANCELLED', 'INTERNAL', 'UNAVAILABLE',
    'DEADLINE_EXCEEDED', 'INVALID_REQUEST', 'AUTHENTICATION', 'MODEL_NOT_FOUND',
    'RATE_LIMIT_EXCEEDED', 'QUOTA_EXCEEDED', 'TOO_MANY_REQUESTS', 'API_ERROR',
    'SERVICE_UNAVAILABLE', 'INVALID_REQUEST_ERROR', 'AUTHENTICATION_ERROR',
    'PERMISSION_ERROR', 'RATE_LIMIT_ERROR', 'SERVER_ERROR', 'INSUFFICIENT_QUOTA',
    'CONTEXT_LENGTH_EXCEEDED','CONTEXT_WINDOW_EXCEEDED','MAX_CONTEXT_LENGTH_EXCEEDED',
})
_VISION_PAGE_TYPES = frozenset({'DRAWING','SCHEDULE','SPEC_PAGE','PHOTO','DIAGRAM','FORM','OTHER','UNKNOWN'})
_VISION_FIELDS = frozenset({'page_type','sheet_id','important_visible_text','observations',
                            'explicit_quantity_texts','scale_text','limitations','needs_review'})
_EXTRACTION_INPUT_BYTE_CAP = 32000
_VISION_INPUT_BYTE_CAP = 6000
_VERIFICATION_INPUT_BYTE_CAP = 6000
_ANSWER_INPUT_BYTE_CAP = 64000
_EXTRACTION_OUTPUT_TOKEN_CAP = 8000
_VISION_OUTPUT_TOKEN_CAP = 2000
_VERIFICATION_OUTPUT_TOKEN_CAP = 1400
_ANSWER_OUTPUT_TOKEN_CAP = 1600
_ANSWER_V2_OUTPUT_TOKEN_CAP = 2400
_EVIDENCE_DECISION_OUTPUT_TOKEN_CAP = 2600
_EVIDENCE_DECISION_THINKING_OUTPUT_TOKEN_CAP = 8000
_JSON_WRAPPER_CHAR_LIMIT = 256
_SAFE_EXTRACTION_PREFIX = re.compile(
    r'(?:Here is (?:the )?(?:requested )?JSON(?: object| result)?[:.]?'
    r'|JSON(?: result| response)?[:.]?'
    r'|以下是(?:请求的|符合要求的)?JSON(?:对象|结果)?[：:]?)',re.I)
_SAFE_EXTRACTION_SUFFIX = re.compile(r'(?:End of JSON[.]?|JSON end[.]?|以上[。.]?)',re.I)
_EXTRACTION_FIELDS = frozenset({'disposition','requirements','reason'})
_REQUIREMENT_FIELDS = frozenset({
    'candidate_key','category','subject','action','object','properties','condition','exception',
    'parent_requirement_key','option_group_key','option_relation','evidence_ids',
    'context_evidence_ids','needs_context',
})
_PROPERTY_FIELDS = frozenset({'name','value','unit','evidence_ids'})
_DISPOSITIONS = frozenset({'CANDIDATES','NO_REQUIREMENTS','NEEDS_CONTEXT','TRUNCATED'})
_REQUIREMENT_CATEGORIES = frozenset({'MATERIAL','INSPECTION','TEST','REPORT','OTHER_REQUIREMENT'})
_OPTION_RELATIONS = frozenset({'NONE','ONE_OF','MULTI_OF'})
_REPAIR_NOTE = '模型输出含保守结构修复；已保留通过契约的完整项，需对照原文审核。'
_VERIFICATION_FIELDS = frozenset({'path','status','citations','reason'})
_VERIFICATION_INPUT_ECHO_FIELDS = frozenset({'claim','label','evidence_ids','context'})
_VERIFICATION_REPAIR_REASON = '模型核验项含格式偏差；引用已保留，但结论降级为待上下文审核。'


def _evidence_source_groups(evidence:list[dict])->list[dict]:
    """Expose deterministic document/page/scope relationships without repeating source text."""
    groups={}
    for index,item in enumerate(evidence,1):
        locator=item.get('locator') if isinstance(item.get('locator'),dict) else {}
        page=locator.get('page_number')
        page=page if type(page) is int and page>=1 else None
        key=(item.get('document_id'),item.get('file_name'),page)
        group=groups.setdefault(key,{
            'group_ref':f'G{len(groups)+1}','file_name':item.get('file_name'),
            **({'page_number':page} if page is not None else {}),
            '_items':[],'_scopes':{},
        })
        ref=f'E{index}';order=item.get('source_order')
        order=order if type(order) is int and order>=0 else index
        group['_items'].append((order,index,ref))
        scope=tuple(locator.get(name) if isinstance(locator.get(name),str) else None
                    for name in ('sheet','section','paragraph'))
        if any(scope):
            entry=group['_scopes'].setdefault(scope,{'_items':[]})
            entry['_items'].append((order,index,ref))
    result=[]
    for group in groups.values():
        scopes=[]
        for (sheet,section,paragraph),entry in group.pop('_scopes').items():
            scope={'evidence_refs':[item[2] for item in sorted(entry['_items'])]}
            for name,value in (('sheet',sheet),('section',section),('paragraph',paragraph)):
                if value:scope[name]=value
            scopes.append((min(entry['_items']),scope))
        items=group.pop('_items')
        group['ordered_evidence_refs']=[item[2] for item in sorted(items)]
        if scopes:group['scopes']=[item[1] for item in sorted(scopes,key=lambda value:value[0])]
        result.append(group)
    return result

@dataclass
class ModelResult:
    data: dict
    request_id: str | None
    cached: bool = False
    mode: str = 'mock'
    execution_receipt: dict | None = None


def normalize_extraction_result(text: str) -> tuple[dict,list[str]]:
    """Discard only bounded JSON presentation wrappers from extraction output.

    No business field is added, removed, renamed, coerced, or completed here.
    The returned object still has to pass the full extraction schema and
    evidence-scope checks. Multiple objects and JSON-shaped wrapper content are
    rejected instead of guessing which result the provider intended.
    """
    flags=[]
    value=text.strip()
    if value.startswith('\ufeff'):
        value=value[1:].lstrip();flags.append('UTF8_BOM')
    match=re.fullmatch(r'```(?:json)?\s*(\{.*\})\s*```',value,re.I|re.S)
    if match:
        value=match.group(1);flags.append('MARKDOWN_FENCE')
    try:
        data=json.loads(value)
    except json.JSONDecodeError as original:
        start=value.find('{')
        if start < 0:
            raise
        try:
            data,end=json.JSONDecoder().raw_decode(value,start)
        except json.JSONDecodeError:
            raise original
        prefix=value[:start].strip();suffix=value[end:].strip()
        wrapper=prefix+suffix
        if (not wrapper or len(prefix)>_JSON_WRAPPER_CHAR_LIMIT
                or len(suffix)>_JSON_WRAPPER_CHAR_LIMIT
                or any(char in wrapper for char in '{}[]`')
                or (prefix and not _SAFE_EXTRACTION_PREFIX.fullmatch(prefix))
                or (suffix and not _SAFE_EXTRACTION_SUFFIX.fullmatch(suffix))):
            raise original
        flags.append('SURROUNDING_TEXT')
    if not isinstance(data,dict):
        raise ValueError('extraction object required')
    return data,sorted(set(flags))


def normalize_verification_contract(data: dict) -> tuple[dict,list[str]]:
    """Salvage only harmless verification formatting deviations.

    Input fields echoed by the provider are removed.  Unknown fields still
    fail closed.  Any repaired check is downgraded to ``NEEDS_CONTEXT`` so a
    formatting repair can never create a supported or contradicted decision.
    """
    if not isinstance(data,dict):raise ValueError('verification object required')
    checks=data.get('checks')
    if set(data)!={'checks'} or not isinstance(checks,list):
        return data,[]
    normalized=[];flags=[]
    for raw in checks:
        if not isinstance(raw,dict):
            normalized.append(raw);continue
        extras=set(raw)-_VERIFICATION_FIELDS
        if extras-_VERIFICATION_INPUT_ECHO_FIELDS:
            raise InvalidModelOutput('模型核验项含未知契约外字段')
        item={name:raw[name] for name in raw if name in _VERIFICATION_FIELDS}
        repaired=False
        if extras:
            flags.append('INPUT_ECHO_FIELDS');repaired=True
        reason=item.get('reason')
        if isinstance(reason,str) and len(reason)>240:
            flags.append('REASON_LENGTH');repaired=True
        if repaired:
            item['status']='NEEDS_CONTEXT'
            item['reason']=_VERIFICATION_REPAIR_REASON
        normalized.append(item)
    return {'checks':normalized},sorted(set(flags))


def normalize_extraction_contract(data: dict, evidence_id: str | set[str]) -> tuple[dict,list[str]]:
    """Salvage only contract-safe structure and make every repair review-visible.

    The normalizer never invents a requirement, source reference, property, or
    option. Invalid atomic requirements/properties are discarded instead of
    guessed. Purely representational scalar deviations are preserved as their
    exact string form. Any repair changes the disposition to ``TRUNCATED`` so
    the runner cannot publish the result as fully extracted without review.
    """
    if not isinstance(data,dict):raise ValueError('extraction object required')
    flags=[];allowed_ids={evidence_id} if isinstance(evidence_id,str) else set(evidence_id)
    if set(data)-_EXTRACTION_FIELDS:flags.append('EXTRA_FIELDS')
    disposition=data.get('disposition')
    if disposition not in _DISPOSITIONS:raise ValueError('valid extraction disposition required')
    raw_requirements=data.get('requirements')
    if not isinstance(raw_requirements,list):raise ValueError('requirements array required')
    reason=data.get('reason','')
    if not isinstance(reason,str):
        reason='' if reason is None else str(reason);flags.append('REASON_TYPE')
    if len(reason)>400:
        reason=reason[:400];flags.append('REASON_LENGTH')

    def string_or_none(value: Any) -> str | None:
        if value is None:return None
        if isinstance(value,str):return value if value else None
        flags.append('NULLABLE_TYPE');return None

    def scoped_ids(value: Any, *, required: bool) -> list[str] | None:
        if not isinstance(value,list):
            if required:raise InvalidModelOutput('模型证据引用缺失或类型错误')
            flags.append('EVIDENCE_IDS_TYPE');return []
        out=[]
        for item in value:
            if item in allowed_ids and item not in out:out.append(item)
            else:raise InvalidModelOutput('模型引用了本请求未提供的证据')
        if required and not out:raise InvalidModelOutput('模型证据引用缺失')
        return out

    repaired=[]
    for raw in raw_requirements:
        if not isinstance(raw,dict):
            flags.append('REQUIREMENT_DROPPED');continue
        if set(raw)-_REQUIREMENT_FIELDS:
            raise InvalidModelOutput('模型要求对象含契约外字段')
        core={name:raw.get(name) for name in ('candidate_key','subject','action','object')}
        if any(not isinstance(value,str) or not value for value in core.values()):
            flags.append('REQUIREMENT_DROPPED');continue
        if raw.get('category') not in _REQUIREMENT_CATEGORIES:
            flags.append('REQUIREMENT_DROPPED');continue
        if raw.get('option_relation') not in _OPTION_RELATIONS:
            flags.append('REQUIREMENT_DROPPED');continue
        evidence_ids=scoped_ids(raw.get('evidence_ids'),required=True)
        context_ids=scoped_ids(raw.get('context_evidence_ids',[]),required=False)
        raw_properties=raw.get('properties',[])
        if not isinstance(raw_properties,list):
            raw_properties=[];flags.append('PROPERTIES_TYPE')
        properties=[]
        for prop in raw_properties:
            if not isinstance(prop,dict):
                flags.append('PROPERTY_DROPPED');continue
            if set(prop)-_PROPERTY_FIELDS:
                raise InvalidModelOutput('模型属性对象含契约外字段')
            name=prop.get('name');value=prop.get('value')
            if not isinstance(name,str) or not name:
                flags.append('PROPERTY_DROPPED');continue
            if not isinstance(value,str):
                if type(value) in (int,float,bool):
                    try:value=json.dumps(value,ensure_ascii=False,allow_nan=False)
                    except ValueError:
                        flags.append('PROPERTY_DROPPED');continue
                    flags.append('PROPERTY_VALUE_TYPE')
                else:
                    flags.append('PROPERTY_DROPPED');continue
            if not value:
                flags.append('PROPERTY_DROPPED');continue
            prop_ids=scoped_ids(prop.get('evidence_ids'),required=True)
            unit=string_or_none(prop.get('unit'))
            properties.append({'name':name,'value':value,'unit':unit,'evidence_ids':prop_ids})
        needs_context=raw.get('needs_context')
        if type(needs_context) is not bool:
            needs_context=True;flags.append('NEEDS_CONTEXT_TYPE')
        repaired.append({
            **core,'category':raw['category'],'properties':properties,
            'condition':string_or_none(raw.get('condition')),
            'exception':string_or_none(raw.get('exception')),
            'parent_requirement_key':string_or_none(raw.get('parent_requirement_key')),
            'option_group_key':string_or_none(raw.get('option_group_key')),
            'option_relation':raw['option_relation'],'evidence_ids':evidence_ids,
            'context_evidence_ids':context_ids,'needs_context':needs_context,
        })

    seen={}
    for requirement in repaired:
        key=requirement['candidate_key'];count=seen.get(key,0)+1;seen[key]=count
        if count>1:
            requirement['candidate_key']=f'{key}~{count}'
            flags.append('DUPLICATE_KEY')
    keys={requirement['candidate_key'] for requirement in repaired}
    for requirement in repaired:
        if requirement['parent_requirement_key'] and requirement['parent_requirement_key'] not in keys:
            requirement['parent_requirement_key']=None
            requirement['needs_context']=True
            flags.append('PARENT_REFERENCE')

    if flags:
        disposition='TRUNCATED'
        available=max(0,400-len(_REPAIR_NOTE)-1)
        reason=(reason[:available]+' ' if reason[:available] else '')+_REPAIR_NOTE
    return {'disposition':disposition,'requirements':repaired,'reason':reason},sorted(set(flags))


def normalize_vision_result(text: str) -> tuple[dict,list[str]]:
    """Conservatively repair bounded, non-semantic vision JSON deviations.

    The repair may discard excess output or make a field less specific, but it
    never invents a visible fact, quantity, scale, or approval.
    """
    flags=[]
    match=re.fullmatch(r'\s*```(?:json)?\s*(\{.*\})\s*```\s*',text,re.I|re.S)
    if match:
        text=match.group(1);flags.append('MARKDOWN_FENCE')
    data=json.loads(text)
    if not isinstance(data,dict):raise ValueError('vision object required')
    if set(data)-_VISION_FIELDS:flags.append('EXTRA_FIELDS')

    page_type=data.get('page_type')
    if page_type not in _VISION_PAGE_TYPES:
        page_type='UNKNOWN';flags.append('PAGE_TYPE')

    def nullable_string(name: str,limit: int) -> str | None:
        value=data.get(name)
        if value is None:return None
        if not isinstance(value,str):
            flags.append('DEFAULTED_FIELD');return None
        value=value.strip()
        if not value:return None
        if len(value)>limit:
            flags.append('TRUNCATED_FIELD')
            return value[:limit]
        return value

    important=data.get('important_visible_text','')
    if not isinstance(important,str):
        important='';flags.append('DEFAULTED_FIELD')
    elif len(important)>4000:
        important=important[:4000];flags.append('TRUNCATED_FIELD')

    def string_list(name: str,max_items: int,max_length: int) -> list[str]:
        value=data.get(name,[])
        if not isinstance(value,list):
            flags.append('DEFAULTED_FIELD');return []
        out=[]
        for item in value:
            if not isinstance(item,str) or not item.strip():
                flags.append('FILTERED_ITEM');continue
            item=item.strip()
            if len(item)>max_length:
                item=item[:max_length];flags.append('TRUNCATED_FIELD')
            out.append(item)
            if len(out)==max_items:
                if len(value)>max_items:flags.append('TRUNCATED_FIELD')
                break
        return out

    observations=string_list('observations',12,600)
    quantities=string_list('explicit_quantity_texts',12,300)
    limitations=string_list('limitations',8,300)
    sheet_id=nullable_string('sheet_id',120)
    scale_text=nullable_string('scale_text',160)
    if data.get('needs_review') is not True:flags.append('REVIEW_FLAG')
    if flags:
        note='模型视觉结果已保守规范化；请对照原页审核。'
        if note not in limitations:
            limitations=(limitations[:7]+[note]) if len(limitations)>=8 else limitations+[note]
    return ({'page_type':page_type,'sheet_id':sheet_id,'important_visible_text':important,
             'observations':observations,'explicit_quantity_texts':quantities,
             'scale_text':scale_text,'limitations':limitations,'needs_review':True},sorted(set(flags)))


_STRICT_SCHEMA_UNSUPPORTED={
    'allOf','not','dependentRequired','dependentSchemas','if','then','else','patternProperties',
}

def strict_output_schema(schema:Any)->Any:
    """Translate the small compatible subset used by Reference QA, or fail before dispatch."""
    if isinstance(schema,list):return [strict_output_schema(value) for value in schema]
    if not isinstance(schema,dict):return schema
    unsupported=sorted(_STRICT_SCHEMA_UNSUPPORTED.intersection(schema))
    if unsupported:
        raise ValueError('unsupported strict-schema keyword: '+unsupported[0])
    if 'oneOf' in schema and 'anyOf' in schema:
        raise ValueError('strict schema cannot contain both oneOf and anyOf')
    result={}
    for key,value in schema.items():
        if key=='oneOf':result['anyOf']=strict_output_schema(value)
        elif key=='const':result['enum']=[strict_output_schema(value)]
        else:result[key]=strict_output_schema(value)
    properties=result.get('properties')
    if result.get('type')=='object' and not isinstance(properties,dict):
        raise ValueError('strict schema object must declare properties')
    if isinstance(properties,dict):
        if result.get('additionalProperties') is not False:
            raise ValueError('strict schema object must disable additional properties')
        required=result.get('required')
        if not isinstance(required,list) or set(required)!=set(properties):
            raise ValueError('strict schema object must require every property')
    return result

class Gateway:
    def __init__(self, settings: Settings, db: Database, client: httpx.Client | None = None):
        self.s=settings;self.db=db
        self.client=client or httpx.Client(timeout=httpx.Timeout(120,connect=15),follow_redirects=False)
        self.prompt=(ROOT/'prompts/joint-extraction/system.md').read_text(encoding="utf-8")
        self.schema=expand_schema(json.loads((ROOT/'spec/schemas/extraction-result.schema.json').read_text(encoding="utf-8")))
        self.prompt_hash=hashlib.sha256((self.prompt+dumps(self.schema)).encode()).hexdigest()
        self.vision_prompt=(ROOT/'prompts/drawing-crop/system.md').read_text(encoding='utf-8')
        self.vision_schema=expand_schema(json.loads((ROOT/'spec/schemas/vision-result.schema.json').read_text(encoding='utf-8')))
        self.vision_prompt_hash=hashlib.sha256((self.vision_prompt+dumps(self.vision_schema)).encode()).hexdigest()
        self.answer_prompt=(ROOT/'prompts/project-question/system.md').read_text(encoding='utf-8')
        self.answer_schema=expand_schema(json.loads((ROOT/'spec/schemas/project-answer.schema.json').read_text(encoding='utf-8')))
        self.answer_prompt_hash=hashlib.sha256((self.answer_prompt+dumps(self.answer_schema)).encode()).hexdigest()
        self.answer_v2_prompt=(ROOT/'prompts/project-question-v2/system.md').read_text(encoding='utf-8')
        self.answer_v2_schema=expand_schema(json.loads((ROOT/'spec/schemas/project-answer-v2.schema.json').read_text(encoding='utf-8')))
        self.answer_v2_prompt_hash=hashlib.sha256(
            (self.answer_v2_prompt+dumps(self.answer_v2_schema)).encode()).hexdigest()
        self.evidence_decision_prompt=(ROOT/'prompts/project-evidence-loop/system.md').read_text(encoding='utf-8')
        self.evidence_decision_schema=expand_schema(json.loads(
            (ROOT/'spec/schemas/project-evidence-decision.schema.json').read_text(encoding='utf-8')))
        self.evidence_decision_prompt_hash=hashlib.sha256(
            (self.evidence_decision_prompt+dumps(self.evidence_decision_schema)).encode()).hexdigest()
        self.complete_decision_schema=expand_schema(json.loads(
            (ROOT/'spec/schemas/project-evidence-decision-complete.schema.json').read_text(encoding='utf-8')))
        self.complete_decision_prompt_hash=hashlib.sha256(
            (self.evidence_decision_prompt+dumps(self.complete_decision_schema)).encode()).hexdigest()
        self._request_slot_lock=threading.Lock();self._last_request_started=0.0

    def close(self):self.client.close()

    def _transport_marker(self)->list[str]:
        # Preserve every pre-Responses cache/task identity for existing chat routes.
        return [] if self.s.api_protocol=='chat_completions' else ['responses-v1']

    def _evidence_input_receipt(
            self,*,call_id:str,round_index:int,request_hash:str,question:str,
            evidence:list[dict],images:list[dict],model_name:str,system_text:str,
            user_text:str,upper:int,limit:int,cached:bool,selector_version:str|None=None,
            route:dict|None=None,initial_evidence_manifest_sha256:str|None=None,
            layout_navigation_identity:dict|None=None,prompt_contract_hash:str|None=None,
            profile_neutral_input_sha256:str|None=None)->dict:
        evidence_inputs=[]
        for item in evidence:
            text=item.get('prompt_text',item['raw_text'])
            evidence_input={
                'evidence_id':item['evidence_id'],
                'text_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
            }
            layout=item.get('layout_lines')
            if isinstance(layout,str) and layout:
                evidence_input.update({
                    'layout_sha256':hashlib.sha256(layout.encode('utf-8')).hexdigest(),
                    'layout_bytes':len(layout.encode('utf-8')),
                })
            evidence_inputs.append(evidence_input)
        visual_inputs=[{
            'region_id':item['region_id'],
            'image_sha256':hashlib.sha256(item['png']).hexdigest(),
            'image_bytes':len(item['png']),
        } for item in images]
        value={
            'receipt_version':'reference-model-input-receipt-1',
            'model_call_id':call_id,'round':round_index+1,
            'request_hash':request_hash,
            'prompt_contract_hash':prompt_contract_hash or self.evidence_decision_prompt_hash,
            'question_hash':hashlib.sha256(
                ' '.join(question.split()).encode('utf-8')).hexdigest(),
            'provider':route['provider'] if route else self.s.provider,'model':model_name,
            'api_protocol':route['api_protocol'] if route else self.s.api_protocol,
            'structured_output_mode':route['structured_output_mode'] if route else self.s.structured_output_mode,
            'inference_mode':route['inference_mode'] if route else self.s.inference_mode(),'cached':cached,
            'source_text_included':False,'prompt_content_included':False,
            'chain_of_thought_included':False,
            'evidence_count':len(evidence_inputs),'evidence_inputs':evidence_inputs,
            'visual_inputs':visual_inputs,
            'system_text_bytes':len(system_text.encode('utf-8')),
            'user_text_bytes':len(user_text.encode('utf-8')),
            'image_bytes':sum(item['image_bytes'] for item in visual_inputs),
            'request_upper_bound_bytes':upper,'max_output_tokens':limit,
        }
        if selector_version=='literal-page-selector-8':
            value.update({
                'receipt_version':'reference-model-input-receipt-2',
                'prompt_contract_hash':self.complete_decision_prompt_hash,
                'selector_version':selector_version,'context_policy':'COMPLETE_SELECTED_SCOPE_V1',
                'source_text_clipped':False,
                'ordered_evidence_manifest_sha256':hashlib.sha256(dumps(evidence_inputs).encode()).hexdigest(),
                'source_text_bytes':sum(len(item.get('prompt_text',item['raw_text']).encode('utf-8'))
                                        for item in evidence),
            })
        elif selector_version=='literal-page-selector-9':
            if not isinstance(layout_navigation_identity,dict):
                raise InvalidModelOutput('Selector v9 layout navigation identity is required.')
            from app.reference_layout_input import (LAYOUT_CONTEXT_POLICY,RECEIPT_VERSION,
                                                    ordered_manifest_sha256)
            value.update({
                'receipt_version':RECEIPT_VERSION,
                'selector_version':selector_version,'context_policy':LAYOUT_CONTEXT_POLICY,
                'source_text_clipped':False,
                'ordered_evidence_manifest_sha256':ordered_manifest_sha256(
                    evidence_inputs,layout_navigation_identity),
                'source_text_bytes':sum(len(item.get('prompt_text',item['raw_text']).encode('utf-8'))
                                        for item in evidence),
                **layout_navigation_identity,
            })
        if initial_evidence_manifest_sha256 is not None:
            value['initial_evidence_manifest_sha256']=initial_evidence_manifest_sha256
        if route is not None:
            value['profile_neutral_input_sha256']=profile_neutral_input_sha256 or hashlib.sha256(dumps([
                'reference-profile-neutral-input-1',system_text,user_text,[]
            ]).encode('utf-8')).hexdigest()
        validate_schema('reference-model-input-receipt',value)
        return value

    @staticmethod
    def _responses_input(messages:list[dict])->list[dict]:
        converted=[]
        for message in messages:
            role=message.get('role');content=message.get('content')
            if role not in ('system','developer','user','assistant'):
                raise InvalidModelOutput(
                    'The Responses API input role is unsupported; no API call was made.')
            if isinstance(content,str):
                converted.append({'role':role,'content':content});continue
            if not isinstance(content,list):
                raise InvalidModelOutput(
                    'The Responses API input content is malformed; no API call was made.')
            parts=[]
            for part in content:
                if not isinstance(part,dict):
                    raise InvalidModelOutput(
                        'The Responses API input content is malformed; no API call was made.')
                if part.get('type')=='text' and isinstance(part.get('text'),str):
                    parts.append({'type':'input_text','text':part['text']})
                elif part.get('type')=='image_url':
                    image=part.get('image_url')
                    url=image.get('url') if isinstance(image,dict) else None
                    if not isinstance(url,str) or not url.startswith('data:image/'):
                        raise InvalidModelOutput(
                            'The Responses API image input is malformed; no API call was made.')
                    parts.append({'type':'input_image','image_url':url,'detail':'auto'})
                else:
                    raise InvalidModelOutput(
                        'The Responses API input content type is unsupported; no API call was made.')
            converted.append({'role':role,'content':parts})
        return converted

    def payload(self, messages: list[dict], limit: int, model: str | None = None,
                response_schema:dict|None=None,schema_name:str|None=None,
                inference_parameters:dict|None=None) -> dict:
        output_format={'type':'json_object'}
        if response_schema is not None and self.s.structured_output_mode=='json_schema':
            if not schema_name or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',schema_name):
                raise InvalidModelOutput(
                    'The native structured-output schema name is invalid; no API call was made.')
            try:schema=strict_output_schema(response_schema)
            except ValueError as exc:
                raise InvalidModelOutput(
                    'The Reference QA schema is incompatible with native structured output; '
                    'no API call was made.') from exc
            output_format={'type':'json_schema','name':schema_name,
                           'strict':True,'schema':schema}
        if self.s.api_protocol=='responses':
            payload={'model':model or self.s.cheap_model,
                     'input':self._responses_input(messages),
                     'text':{'format':output_format},
                     'max_output_tokens':limit,'store':False}
        else:
            response_format=(
                {'type':'json_schema','json_schema':{
                    'name':output_format['name'],'strict':True,'schema':output_format['schema']}}
                if output_format['type']=='json_schema' else output_format)
            payload = {'model': model or self.s.cheap_model, 'response_format': response_format,
                       'max_tokens': limit, 'stream': False, 'messages': messages}
        payload.update(self.s.inference_parameters()
                       if inference_parameters is None else inference_parameters)
        return payload

    @staticmethod
    def _normalize_responses_body(body:dict)->dict:
        """Map one stateless Responses result into the gateway's settled internal envelope."""
        usage=body.get('usage');normalized_usage={}
        if isinstance(usage,dict):
            normalized_usage={
                'prompt_tokens':usage.get('input_tokens'),
                'completion_tokens':usage.get('output_tokens'),
                'total_tokens':usage.get('total_tokens'),
            }
            details=usage.get('output_tokens_details')
            if isinstance(details,dict):normalized_usage['completion_tokens_details']=details
        texts=[];refused=False;unexpected=False
        output=body.get('output')
        if isinstance(output,list):
            for item in output:
                if not isinstance(item,dict):unexpected=True;continue
                if item.get('type')=='reasoning':continue
                if item.get('type')!='message':unexpected=True;continue
                if item.get('status')!='completed' or item.get('role')!='assistant':
                    unexpected=True
                content=item.get('content')
                if not isinstance(content,list):unexpected=True;continue
                for part in content:
                    if not isinstance(part,dict):unexpected=True
                    elif part.get('type')=='output_text' and isinstance(part.get('text'),str):
                        texts.append(part['text'])
                    elif part.get('type')=='refusal':refused=True
                    else:unexpected=True
        else:unexpected=True
        status=body.get('status')
        completed=(status=='completed' and texts and not refused and not unexpected)
        return {
            'id':body.get('id'),'object':'response-normalized',
            'choices':[{'finish_reason':'stop' if completed else str(status or 'invalid'),
                        'message':{'content':''.join(texts) if completed else None,
                                   'refusal':refused}}],
            'usage':normalized_usage,
        }

    def _wait_for_request_slot(self) -> None:
        interval=self.s.min_request_interval_seconds
        if interval<=0:return
        with self._request_slot_lock:
            delay=max(0.0,self._last_request_started+interval-time.monotonic())
            if delay:time.sleep(delay)
            self._last_request_started=time.monotonic()

    def _wait_and_record(self, run_id: str) -> None:
        started=time.perf_counter()
        self._wait_for_request_slot()
        self.db.record_metric(run_id,'model.request_wait',(time.perf_counter()-started)*1000)

    def _request_timed(self, run_id: str, attempt: str, payload: dict, purpose: str) -> dict:
        started=time.perf_counter()
        try:return self._request_json(attempt,payload,purpose)
        finally:self.db.record_metric(run_id,'model.response',(time.perf_counter()-started)*1000)

    def _contains_api_key(self, value: str) -> bool:
        return bool(self.s.api_key and self.s.api_key in value)

    def _safe_provider_id(self, value: Any) -> str | None:
        """Accept only a short opaque upstream id that cannot echo the active key."""
        if not isinstance(value,str) or not _SAFE_REQUEST_ID.fullmatch(value):
            return None
        return None if self._contains_api_key(value) else value

    def _safe_header(self, response: httpx.Response, names: tuple[str, ...], pattern: re.Pattern) -> str | None:
        for name in names:
            value = response.headers.get(name)
            if value and pattern.fullmatch(value) and not self._contains_api_key(value):
                return value
        return None

    def _provider_error_code(self, response: httpx.Response) -> str | None:
        # Error messages and response bodies may contain document text or upstream details.
        # Only retain a short machine-readable code from a small JSON error response.
        if len(response.content) > 65536:
            return None
        try:
            error = response.json().get('error', {})
        except (AttributeError, ValueError):
            return None
        for key in ('status', 'code'):
            value = error.get(key) if isinstance(error, dict) else None
            if (isinstance(value, str) and _SAFE_PROVIDER_CODE.fullmatch(value)
                    and value.upper() in _PROVIDER_ERROR_CODES and not self._contains_api_key(value)):
                return value
        return None

    @staticmethod
    def _http_class(status: int) -> str:
        if status == 400: return 'REQUEST_REJECTED'
        if status == 401: return 'AUTHENTICATION'
        if status == 403: return 'PERMISSION'
        if status == 404: return 'NOT_FOUND'
        if status == 408: return 'TRANSIENT_TIMEOUT'
        if status == 429: return 'RATE_LIMIT_OR_QUOTA'
        if 500 <= status <= 599: return 'PROVIDER_TRANSIENT'
        return 'HTTP_ERROR'

    def _http_diagnostic(self, response: httpx.Response) -> tuple[dict, str | None]:
        request_id = self._safe_header(response, _REQUEST_ID_HEADERS, _SAFE_REQUEST_ID)
        diagnostic = {'kind': 'HTTP_STATUS', 'status': response.status_code,
                      'class': self._http_class(response.status_code)}
        provider_code = self._provider_error_code(response)
        retry_after = self._safe_header(response, ('retry-after',), _SAFE_RETRY_AFTER)
        if provider_code:
            diagnostic['provider_code'] = provider_code
            if response.status_code==400 and provider_code.upper() in {
                    'CONTEXT_LENGTH_EXCEEDED','CONTEXT_WINDOW_EXCEEDED','MAX_CONTEXT_LENGTH_EXCEEDED'}:
                diagnostic['class']='MODEL_CONTEXT_LIMIT'
        if retry_after: diagnostic['retry_after'] = retry_after
        if request_id: diagnostic['provider_request_id'] = request_id
        return diagnostic, request_id

    @staticmethod
    def _http_pause_message(diagnostic: dict, purpose: str) -> str:
        status = diagnostic['status']
        label = {
            400: '请求参数被供应商拒绝',
            401: '供应商鉴权失败',
            403: '供应商权限或账户前置条件不满足',
            404: '供应商模型或接口不存在',
            408: '供应商请求超时',
            429: '供应商配额或速率限制',
        }.get(status, '供应商暂时不可用' if 500 <= status <= 599 else '供应商HTTP错误')
        if diagnostic['class']=='MODEL_CONTEXT_LIMIT':label='完整资料超出模型实际上下文容量'
        retry = f"；Retry-After={diagnostic['retry_after']}" if diagnostic.get('retry_after') else ''
        return f'{purpose}{label}（HTTP {status}）{retry}；费用保持预留，需对账，不自动重试。'

    def _request_json(self, attempt: str, payload: dict, purpose: str) -> dict:
        response = None
        try:
            headers={'Authorization': 'Bearer ' + self.s.api_key} if self.s.api_key else {}
            endpoint='/responses' if self.s.api_protocol=='responses' else '/chat/completions'
            response = self.client.post(self.s.api_base_url + endpoint, json=payload,
                                        headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            diagnostic, request_id = self._http_diagnostic(exc.response)
            self.db.unknown(attempt, dumps(diagnostic), request_id)
            raise ProviderPaused(self._http_pause_message(diagnostic, purpose), 409) from exc
        except httpx.TimeoutException as exc:
            diagnostic = {'kind': 'NETWORK_ERROR', 'class': 'TIMEOUT', 'exception': type(exc).__name__}
            self.db.unknown(attempt, dumps(diagnostic))
            raise ProviderPaused(f'{purpose}网络超时；费用保持预留，需对账，不自动重试。', 409) from exc
        except httpx.RequestError as exc:
            diagnostic = {'kind': 'NETWORK_ERROR', 'class': 'TRANSPORT', 'exception': type(exc).__name__}
            self.db.unknown(attempt, dumps(diagnostic))
            raise ProviderPaused(f'{purpose}网络传输失败；费用保持预留，需对账，不自动重试。', 409) from exc
        except Exception as exc:
            exception_name = type(exc).__name__ if _SAFE_PROVIDER_CODE.fullmatch(type(exc).__name__) else 'Exception'
            self.db.unknown(attempt, dumps({'kind': 'CLIENT_ERROR', 'class': 'UNEXPECTED',
                                            'exception': exception_name}))
            raise ProviderPaused(f'{purpose}客户端异常；费用保持预留，需对账，不自动重试。', 409) from exc
        request_id = self._safe_header(response, _REQUEST_ID_HEADERS, _SAFE_REQUEST_ID)
        try:
            body = response.json()
            if not isinstance(body, dict): raise ValueError('JSON object required')
            return self._normalize_responses_body(body) if self.s.api_protocol=='responses' else body
        except (AttributeError, ValueError) as exc:
            diagnostic = {'kind': 'INVALID_RESPONSE', 'status': response.status_code,
                          'class': 'INVALID_JSON'}
            if request_id: diagnostic['provider_request_id'] = request_id
            self.db.unknown(attempt, dumps(diagnostic), request_id)
            raise ProviderPaused(f'{purpose}响应不是可用JSON；费用保持预留，需对账，不自动重试。', 409) from exc

    def billed_usage(self, body: dict) -> dict:
        """Normalize OpenAI-compatible usage and conservatively include hidden output tokens."""
        usage = body.get('usage')
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                                              for k in ('prompt_tokens', 'completion_tokens')):
            raise ValueError('响应缺少可信usage')
        normalized = dict(usage)
        total = usage.get('total_tokens')
        if self.s.provider == 'gemini' and (type(total) is not int or total < usage['prompt_tokens'] + usage['completion_tokens']):
            raise ValueError('Gemini响应缺少可信total_tokens')
        if type(total) is int and total >= usage['prompt_tokens']:
            generated = total - usage['prompt_tokens']
            if generated > usage['completion_tokens']:
                normalized['provider_completion_tokens'] = usage['completion_tokens']
                normalized['completion_tokens'] = generated
        return normalized

    def rejects_reasoning(self, body: dict, usage: dict,*,allow_reasoning:bool=False) -> bool:
        if self.s.provider != 'deepseek':
            return False
        message = (body.get('choices') or [{}])[0].get('message', {})
        details = usage.get('completion_tokens_details') or {}
        return (not allow_reasoning
                and bool(message.get('reasoning_content') or details.get('reasoning_tokens', 0)))

    def _terminal_response_policy(self, attempt: str, actual: Decimal, usage: dict,
                                  provider_id: str | None, body: dict, *,
                                  allow_reasoning: bool = False) -> bool:
        """Validate billed response shapes before any route publishes a result.

        A trustworthy usage block means the paid call is terminal even when its
        envelope is malformed.  Missing usage is handled by the caller before
        this helper and deliberately remains UNKNOWN for billing reconciliation.
        """
        try:
            choices=body.get('choices')
            if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
                raise ValueError('response choices are malformed')
            if not isinstance(choices[0].get('message'),dict):
                raise ValueError('response message is malformed')
            details=usage.get('completion_tokens_details')
            if details is not None and not isinstance(details,dict):
                raise ValueError('completion token details are malformed')
            return self.rejects_reasoning(body,usage,allow_reasoning=allow_reasoning)
        except Exception as exc:
            self.db.finalize_model_call(
                attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('RESPONSE_ENVELOPE',exc))
            raise InvalidModelOutput(
                'Provider response envelope was malformed; the cost was recorded and no result was published.') from exc

    def validate(self, data: dict, evidence: dict | list[dict]):
        validate_schema('extraction-result',data)
        records=evidence if isinstance(evidence,list) else [evidence]
        allowed={item['evidence_id'] for item in records}
        refs=evidence_references(data)
        if not refs.issubset(allowed):raise InvalidModelOutput('模型引用了本请求未提供的证据')
        for requirement in data['requirements']:
            if any(not prop['evidence_ids'] for prop in requirement['properties']):
                raise InvalidModelOutput('属性缺少字段级证据')
        keys=[r['candidate_key'] for r in data['requirements']]
        if len(keys)!=len(set(keys)):raise InvalidModelOutput('模型输出重复的要求编号')
        if any(r['parent_requirement_key'] and r['parent_requirement_key'] not in keys for r in data['requirements']):
            raise InvalidModelOutput('父要求引用不完整')

    def _terminal_diagnostic(self, result_class: str, exc: Exception | None = None,
                             *, kind: str = 'CONTRACT_ERROR') -> dict:
        """Build a bounded diagnostic without retaining provider response text."""
        diagnostic={'kind':kind,'class':result_class}
        if exc is None:return diagnostic
        from app.answer_diagnostics import (NumericEvidenceError,valid_semantic_detail,
                                             valid_numeric_diagnostic)
        exception=('ValueError' if isinstance(exc,NumericEvidenceError) else type(exc).__name__)
        diagnostic['exception']=exception if _SAFE_PROVIDER_CODE.fullmatch(exception) else 'Exception'
        path=getattr(exc,'absolute_path',None)
        if path is not None:
            safe_path='.'.join(str(value) for value in path)
            if (re.fullmatch(r'[A-Za-z0-9_.-]{0,160}',safe_path)
                    and not self._contains_api_key(safe_path)):
                diagnostic['path']=safe_path
        validator=getattr(exc,'validator',None)
        if (isinstance(validator,str) and _SAFE_PROVIDER_CODE.fullmatch(validator)
                and not self._contains_api_key(validator)):
            diagnostic['validator']=validator
        elif result_class in ('PROJECT_ANSWER','PROJECT_EVIDENCE_DECISION'):
            if (isinstance(exc,NumericEvidenceError) and kind=='CONTRACT_ERROR'
                    and valid_semantic_detail(exc.semantic_detail)):
                diagnostic['semantic_detail']=dict(exc.semantic_detail)
            if isinstance(exc,json.JSONDecodeError):
                diagnostic['validator']='json_decode'
            else:
                message=str(exc).casefold()
                patterns=(_EVIDENCE_DECISION_REASON_PATTERNS+_ANSWER_CONTRACT_REASON_PATTERNS
                          if result_class=='PROJECT_EVIDENCE_DECISION'
                          else _ANSWER_CONTRACT_REASON_PATTERNS)
                diagnostic['validator']=next(
                    (code for pattern,code in patterns
                     if pattern in message),'answer_contract')
        if 'semantic_detail' in diagnostic and not valid_numeric_diagnostic(diagnostic):
            # Optional diagnostics must not prevent safe settlement if an
            # internal detail and its old classification are inconsistent.
            diagnostic.pop('semantic_detail')
        return diagnostic

    def _recover_terminal(self, run_id: str, task_key: str, validator) -> ModelResult | None:
        """Recover a billed task after a process crash without another HTTP call.

        Successful terminal responses are revalidated and returned even if the
        ordinary cache has expired.  Failed or legacy response-less settlements
        are terminal: a caller must create an explicitly versioned task before
        another paid request is permitted.
        """
        recent=self._family_calls(run_id,paid_task_family(task_key))
        row=next((item for item in reversed(recent)
                  if item['task_key']==task_key and item['actual_units'] is not None),None)
        if row is None:return None
        if row['has_response']:
            try:
                response=self.db.model_call_response(row['id'])
                if response is None:raise ValueError('terminal response disappeared')
                data=json.loads(response)
                validator(data)
            except Exception as exc:
                raise ProviderPaused('已结算结果的持久化内容损坏；禁止自动重复收费，需人工处理。',409) from exc
            return ModelResult(data,row['id'],True,self.s.provider)
        try:
            diagnostic=json.loads(row['error'] or '{}')
        except json.JSONDecodeError:
            diagnostic={}
        if not isinstance(diagnostic,dict):diagnostic={}
        if diagnostic.get('kind')=='POLICY_ERROR':
            raise ProviderPaused('该任务已有已结算的供应商策略违规响应；禁止自动重复收费，需显式重排。',409)
        if diagnostic.get('kind')=='CONTRACT_ERROR':
            raise InvalidModelOutput('该任务已有已结算但未通过契约的响应；不会自动再次收费，需显式重排。')
        raise ProviderPaused('该任务已有已结算但无可发布结果的旧记录；禁止自动重复收费，需显式重排。',409)

    def _generation(self, run: dict, task_family: str, *, job_id: str | None = None,
                    explicit_generation: int | None = None) -> int:
        """Resolve only a durable, explicitly authorized paid retry generation."""
        if paid_task_family(task_family) != task_family:
            raise InvalidModelOutput('付费任务族格式无效；未调用API')
        if job_id is not None:
            try:
                self.db.authorize_verification_generation(
                    run['project_id'],run['id'],task_family,job_id)
            except DomainError as exc:
                raise ProviderPaused(str(exc),getattr(exc,'code',409)) from exc
        generation=self.db.authorized_generation(
            run['id'],task_family,context_id=job_id if job_id is not None else None)
        if explicit_generation is not None:
            if type(explicit_generation) is not int or not 0 <= explicit_generation < MAX_PAID_TASK_CALLS:
                raise InvalidModelOutput('显式重排代次无效；未调用API')
            if generation and generation != explicit_generation:
                raise ProviderPaused('持久化恢复授权与任务代次不一致；未调用API，需人工处理。',409)
            # Visual contract requeue predates the reconciliation-generation
            # audit event and remains an explicitly persisted document task.
            generation=explicit_generation if not generation else generation
        return generation

    def _family_calls(self, run_id: str, task_family: str) -> list[dict]:
        return self.db.family_calls(run_id,task_family)

    @staticmethod
    def _guard_family_before_request(recent: list[dict], task_key: str,
                                     generation: int, label: str) -> None:
        if any(row['actual_units'] is None for row in recent):
            raise ProviderPaused(f'{label}请求待对账；避免自动重复收费',409)
        # Handles legacy verification :job: keys and any corrupted/mismatched
        # task state which exact-key recovery cannot see.
        response_less=[row for row in recent if row['actual_units'] is not None and not row['has_response']]
        if generation==0 and response_less and all(row['task_key']!=task_key for row in response_less):
            raise ProviderPaused(f'{label}已有对账或已结算但无可发布结果的旧调用；需显式恢复代次。',409)
        if len(recent)>=MAX_PAID_TASK_CALLS:
            raise ProviderPaused(f'{label}任务族已达到三次累计调用上限；保留旧记录且不再收费',409)

    def extract(self, run: dict, evidence: dict) -> ModelResult:
        return self.extract_many(run,[evidence])

    def extract_many(self, run: dict, evidences: list[dict]) -> ModelResult:
        if not isinstance(evidences,list) or not 1<=len(evidences)<=4:
            raise InvalidModelOutput('相邻证据批次必须包含1至4个片段；未调用API')
        ids=[item.get('evidence_id') for item in evidences]
        if any(not isinstance(value,str) or not value for value in ids) or len(ids)!=len(set(ids)):
            raise InvalidModelOutput('相邻证据批次标识无效或重复；未调用API')
        evidence=evidences[0];allowed_ids=set(ids)
        if self.s.provider=='mock':
            data=mock_extract_many(evidences)
            data,_quality_flags=apply_deterministic_quality(data,evidences)
            self.validate(data,evidences)
            return ModelResult(data,None)
        blockers=self.s.live_errors()
        if blockers:raise ProviderPaused('；'.join(blockers),409)
        if run.get('provider') != self.s.provider:
            raise ProviderPaused('运行Provider与当前服务不一致；为避免误收费，请新建分析。',409)
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',(run['project_id'],),False):
            raise ProviderPaused('项目存在待对账请求；禁止新建或重复付费分析',409)
        task_family=(evidence['evidence_id'] if len(evidences)==1 else
                     'extract-batch:'+evidence['evidence_id']+':'+hashlib.sha256(dumps(ids).encode()).hexdigest()[:16])
        generation=self._generation(run,task_family)
        task=paid_task_key(task_family,generation)
        recent=self._family_calls(run['id'],task_family)
        if any(x['actual_units'] is None for x in recent):raise ProviderPaused('存在待对账请求；避免自动重复收费',409)
        recovered=self._recover_terminal(run['id'],task,lambda value:self.validate(value,evidences))
        if recovered is not None:return recovered
        self._guard_family_before_request(recent,task,generation,'相邻片段组' if len(evidences)>1 else '该片段')
        # The paid task key may include an internal manual-requeue generation.
        # The model and evidence-scope validator must always see the immutable
        # source evidence ID, never that billing-only suffix.
        items=[{'evidence_id':item['evidence_id'],'text':item['raw_text'],
                'internal_revision_date':item['internal_revision_date'],'locator':item['locator']}
               for item in evidences]
        content=items[0] if len(items)==1 else {'evidence_items':items}
        limit=min(_EXTRACTION_OUTPUT_TOKEN_CAP,self.s.output_limit)
        messages=[{'role':'system','content':self.prompt+'\nJSON Schema:\n'+dumps(self.schema)},
                  {'role':'user','content':dumps(content)}]
        payload=self.payload(messages,limit)
        # UTF-8 bytes are a conservative input-size estimate, not token billing.
        upper_input=sum(len(x['content'].encode('utf-8')) for x in messages)+256
        if upper_input>min(_EXTRACTION_INPUT_BYTE_CAP,self.s.input_limit):
            raise InvalidModelOutput('片段加Schema超出简单任务输入预算；需细分，未调用API')
        key=cache_key({'tenant_id':'local','project_id':run['project_id'],'input_snapshot_id':run['snapshot_id'],
                      'input_hash':hashlib.sha256(dumps(content).encode()).hexdigest(),'crop_hashes':[],
                      'parser_version':'+'.join(sorted({item.get('parser_version','text-baseline-1') for item in evidences})),
                      'prompt_version':self.prompt_hash,
                      'provider':self.s.api_base_url,'model_id':self.s.cheap_model,'model_snapshot':None,
                      'schema_version':'0.2.0','retrieval_version':'none-v1','assembly_rule_version':'disabled-v1',
                      'revision_policy_version':'0.2.0','routing_version':'0.2.0',
                      'parameters':{**({'api_protocol':self.s.api_protocol}
                                       if self.s.api_protocol!='chat_completions' else {}),
                                    **self.s.inference_parameters(),
                                    'max_tokens':limit,'result_normalizer':'extraction-contract-v2'},
                      **({'manual_requeue_generation':generation} if generation else {})})
        cached=self.db.cached(key,run['project_id'])
        if cached:
            cached,_quality_flags=apply_deterministic_quality(cached,evidences)
            self.validate(cached,evidences)
            return ModelResult(cached,None,True,self.s.provider)
        amount=Decimal('0')
        self._wait_and_record(run['id'])
        attempt=self.db.reserve(run['project_id'],run['id'],task,amount,self.s.cheap_model,
                                hashlib.sha256(dumps(payload).encode()).hexdigest(),Decimal('0'),Decimal('0'),
                                allow_zero=self.s.is_local_model())
        body=self._request_timed(run['id'],attempt,payload,'提取')
        try:
            usage=self.billed_usage(body)
        except ValueError:
            self.db.unknown(attempt,dumps({'kind':'INVALID_RESPONSE','class':'MISSING_USAGE'}))
            raise ProviderPaused('响应缺少usage；保持预留并暂停，需核对账单。',409)
        actual=Decimal('0')
        provider_id=self._safe_provider_id(body.get('id'))
        # DeepSeek忽略非思考请求时保留账务并停止；Gemini 3最低推理属于预期行为。
        if self._terminal_response_policy(attempt,actual,usage,provider_id,body):
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('REASONING_NOT_DISABLED',kind='POLICY_ERROR'))
            raise ProviderPaused('供应商未遵守非思考设置；本次费用已记录，需确认接口兼容性。', 409)
        # A terminal provider response is recorded before publishing a cache entry.
        try:
            choice=body['choices'][0]
            if choice.get('finish_reason')!='stop':raise ValueError('响应截断或非正常完成')
            text=choice['message']['content']
            if not isinstance(text,str) or len(text)>100_000:raise ValueError('空响应或响应过大')
            data,_wrapper_flags=normalize_extraction_result(text)
            data,_contract_flags=normalize_extraction_contract(data,allowed_ids)
            data,_quality_flags=apply_deterministic_quality(data,evidences)
            self.validate(data,evidences)
        except Exception as exc:
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('EXTRACTION_RESULT',exc))
            raise InvalidModelOutput('模型结果未通过契约；已记录本次费用，不自动重复请求。') from exc
        self.db.finalize_model_call(attempt,actual,usage,provider_id,response=data,cache_key=key)
        return ModelResult(data,attempt,False,self.s.provider)

    def vision(self, run: dict, task_key: str, image_png: bytes, metadata: dict,
               *, billing_generation: int = 0) -> ModelResult:
        """Analyze one bounded page image through the single audited gateway."""
        if self.s.provider not in ('deepseek','openai') or not self.s.vision_enabled:
            raise ProviderPaused('当前供应商的整页视觉路由未启用；未发送图像。',409)
        blockers=self.s.live_errors()
        if blockers:raise ProviderPaused('；'.join(blockers),409)
        if run.get('provider') != self.s.provider:
            raise ProviderPaused('运行Provider与当前服务不一致；为避免误收费，请新建分析。',409)
        if not image_png or len(image_png)>32*1024*1024:
            raise InvalidModelOutput('视觉派生图为空或超过32MiB；未调用API')
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',(run['project_id'],),False):
            raise ProviderPaused('项目存在待对账请求；禁止新建或重复付费分析',409)
        task_family='vision:'+task_key
        generation=self._generation(run,task_family,explicit_generation=billing_generation)
        task=paid_task_key(task_family,generation)
        recovered=self._recover_terminal(run['id'],task,lambda value:validate_schema('vision-result',value))
        if recovered is not None:return recovered
        recent=self._family_calls(run['id'],task_family)
        self._guard_family_before_request(recent,task,generation,'该视觉页')
        safe_metadata={k:metadata[k] for k in (
            'document_id','page','coordinate_system','width','height','page_type_hint',
            'region_id','region_type','crop_bbox') if k in metadata and metadata[k] is not None}
        instruction=self.vision_prompt+'\nJSON Schema:\n'+dumps(self.vision_schema)+'\nPage metadata:\n'+dumps(safe_metadata)
        data_url='data:image/png;base64,'+base64.b64encode(image_png).decode('ascii')
        # The 59-page live drawing proved that 1,200 output tokens truncated a
        # repeatable subset of dense sheets.  Keep the normal application cap
        # (currently 2,000) while keeping the same bounded response contract.
        limit=min(_VISION_OUTPUT_TOKEN_CAP,self.s.output_limit)
        payload=self.payload([
            {'role':'system','content':instruction},
            {'role':'user','content':[{'type':'text','text':'分析这一页，只返回JSON。'},
                                      {'type':'image_url','image_url':{'url':data_url}}]},
        ],limit,self.s.vision_model)
        # DeepSeek documents at most 384 image tokens per image. Text bytes remain a conservative
        # upper estimate; base64 transport bytes are not billed as text tokens.
        upper_input=len(instruction.encode('utf-8'))+len('分析这一页，只返回JSON。'.encode('utf-8'))+384+256
        if upper_input>min(_VISION_INPUT_BYTE_CAP,self.s.input_limit):
            raise InvalidModelOutput('视觉提示加Schema超出简单任务输入预算；未调用API')
        image_hash=hashlib.sha256(image_png).hexdigest()
        cache_fingerprint=['vision-v1',run['project_id'],run['snapshot_id'],self.s.api_base_url,
                           *self._transport_marker(),
                           self.s.vision_model,self.vision_prompt_hash,image_hash,safe_metadata,
                           self.s.inference_parameters(),limit]
        # Generation zero retains the v0.2.6 cache key.  An explicitly paid
        # requeue receives a distinct cache boundary and cannot be mistaken for
        # an ordinary recovery of the original attempt.
        if generation:
            cache_fingerprint.append({'manual_requeue_generation':generation})
        cache_id=hashlib.sha256(dumps(cache_fingerprint).encode()).hexdigest()
        cached=self.db.cached(cache_id,run['project_id'])
        if cached is not None:
            validate_schema('vision-result',cached)
            original=self.db.one('SELECT id FROM model_calls WHERE project_id=? AND task_key=? AND actual_units IS NOT NULL AND response IS NOT NULL ORDER BY created_at DESC LIMIT 1',
                                 (run['project_id'],task),False)
            return ModelResult(cached,original['id'] if original else None,True,self.s.provider)
        amount=Decimal('0')
        self._wait_and_record(run['id'])
        request_hash=hashlib.sha256(dumps(
            [*self._transport_marker(),self.s.vision_model,self.vision_prompt_hash,
             image_hash,safe_metadata,limit]).encode()).hexdigest()
        attempt=self.db.reserve(run['project_id'],run['id'],task,amount,self.s.vision_model,
                                request_hash,Decimal('0'),Decimal('0'))
        body=self._request_timed(run['id'],attempt,payload,'视觉')
        try:
            usage=self.billed_usage(body)
        except ValueError:
            self.db.unknown(attempt,dumps({'kind':'INVALID_RESPONSE','class':'MISSING_USAGE'}))
            raise ProviderPaused('视觉响应缺少usage；费用保持预留并暂停，需对账。',409)
        actual=Decimal('0')
        provider_id=self._safe_provider_id(body.get('id'))
        if self._terminal_response_policy(attempt,actual,usage,provider_id,body):
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('REASONING_NOT_DISABLED',kind='POLICY_ERROR'))
            raise ProviderPaused('视觉接口未遵守非思考配置；本次费用已记录，需确认接口兼容性。',409)
        try:
            choice=body['choices'][0]
            if choice.get('finish_reason')!='stop':raise ValueError('视觉输出截断')
            text=choice['message']['content']
            if not isinstance(text,str) or len(text)>50_000:raise ValueError('视觉响应为空或过长')
            data,_normalization_flags=normalize_vision_result(text)
            validate_schema('vision-result',data)
        except Exception as exc:
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('VISION_RESULT',exc))
            raise InvalidModelOutput('视觉结果未通过契约；已记录本次费用，不自动重复请求。') from exc
        self.db.finalize_model_call(attempt,actual,usage,provider_id,response=data,cache_key=cache_id)
        return ModelResult(data,attempt,False,self.s.provider)


    def verify_claims(self, run: dict, fields: list[dict], evidence: dict, job_id=None) -> ModelResult:
        """Low-cost independent verification. All HTTP remains in this gateway.

        Uses the same durable call log, with no automatic retries or model upgrades.
        """
        from app.verification import VerificationService
        if self.s.provider == 'mock':
            raise ProviderPaused('模拟模式不执行语义核验', 409)
        if self.s.live_errors():
            raise ProviderPaused('；'.join(self.s.live_errors()), 409)
        if run.get('provider') != self.s.provider:
            raise ProviderPaused('运行Provider与当前服务不一致；为避免误收费，请新建分析。',409)
        prompt = (ROOT / 'prompts/claim-verification/system.md').read_text(encoding='utf-8')
        content = {'fields': [{k: f[k] for k in ('path', 'claim', 'label', 'evidence_ids', 'context')} for f in fields],
                   'evidence': [{'evidence_id': eid, 'text': e['raw_text'], 'revision_date': e.get('internal_revision_date'),
                                 'locator': e['locator']} for eid, e in sorted(evidence.items())]}
        limit = min(_VERIFICATION_OUTPUT_TOKEN_CAP, self.s.output_limit)
        messages=[{'role':'system','content':prompt},{'role':'user','content':dumps(content)}]
        payload = self.payload(messages,limit)
        upper = sum(len(m['content'].encode('utf-8')) for m in messages) + 256
        if upper > min(_VERIFICATION_INPUT_BYTE_CAP,self.s.input_limit):
            raise InvalidModelOutput('核验证据超过单次输入限额，不能截掉上下文后标记通过')
        cache_id = hashlib.sha256(dumps(['verify-v1',run['project_id'],run['snapshot_id'],self.s.api_base_url,
                    self.s.cheap_model, payload, [e.get('file_sha256') for _,e in sorted(evidence.items())]]).encode()).hexdigest()
        task_family = 'verify:' + cache_id
        generation=self._generation(run,task_family,job_id=job_id)
        task=paid_task_key(task_family,generation)
        from copy import deepcopy
        recovered=self._recover_terminal(
            run['id'],task,
            lambda value:VerificationService.apply_model(deepcopy(fields),value,evidence,None))
        if recovered is not None:return recovered
        result_cache_id=(cache_id if generation==0 else
                         hashlib.sha256(dumps([cache_id,{'manual_requeue_generation':generation}]).encode()).hexdigest())
        cached = self.db.cached(result_cache_id, run['project_id'])
        if cached is not None:
            VerificationService.apply_model(deepcopy(fields), cached, evidence, None)
            original_call=next((row for row in reversed(self._family_calls(run['id'],task_family))
                                if row['actual_units'] is not None and row['has_response']),None)
            return ModelResult(cached, original_call['id'] if original_call else None, True, self.s.provider)
        recent=self._family_calls(run['id'],task_family)
        self._guard_family_before_request(recent,task,generation,'该核验')
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL', (run['project_id'],), False):
            raise ProviderPaused('项目有未对账调用，暂停核验', 409)
        amount = Decimal('0')
        self._wait_and_record(run['id'])
        attempt = self.db.reserve(run['project_id'], run['id'], task, amount, self.s.cheap_model,
                                  hashlib.sha256(dumps(payload).encode()).hexdigest(), Decimal('0'),
                                  Decimal('0'), verification_job_id=job_id,
                                  allow_zero=self.s.is_local_model())
        body = self._request_timed(run['id'], attempt, payload, '核验')
        try:
            usage = self.billed_usage(body)
        except ValueError:
            self.db.unknown(attempt,dumps({'kind':'INVALID_RESPONSE','class':'MISSING_USAGE'}))
            raise ProviderPaused('核验响应缺少usage，费用保持预留', 409)
        actual = Decimal('0')
        provider_id=self._safe_provider_id(body.get('id'))
        if self._terminal_response_policy(attempt,actual,usage,provider_id,body):
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('REASONING_NOT_DISABLED',kind='POLICY_ERROR'))
            raise ProviderPaused('核验接口未遵守非思考配置，已记账并暂停', 409)
        try:
            choice = body['choices'][0]; message = choice['message']
            if choice.get('finish_reason') != 'stop': raise ValueError('核验输出截断')
            text = message['content']
            if not isinstance(text, str) or len(text) > 50000: raise ValueError('核验响应过长')
            data = json.loads(text)
            data,_normalizer_flags = normalize_verification_contract(data)
            VerificationService.apply_model(deepcopy(fields), data, evidence, attempt)
        except Exception as exc:
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('CLAIM_CHECK_BATCH',exc))
            raise InvalidModelOutput('核验响应未通过格式、范围或逐字引用检查，已记账') from exc
        self.db.finalize_model_call(attempt,actual,usage,provider_id,response=data,cache_key=result_cache_id)
        return ModelResult(data, attempt, False, self.s.provider)

    def answer(self, run: dict, question: str, evidence: list[dict]) -> ModelResult:
        """Answer one explicit user question from a bounded immutable evidence set."""
        from app.questions import validate_answer_model
        if self.s.provider=='mock':
            raise ProviderPaused('Mock mode does not generate project answers.',409)
        blockers=self.s.live_errors()
        if blockers:raise ProviderPaused('; '.join(blockers),409)
        if run.get('status') not in ('PARTIAL','COMPLETED'):
            raise ProviderPaused('Project Q&A requires a completed or partial analysis run.',409)
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',(run['project_id'],),False):
            raise ProviderPaused('The project has an unresolved model call. Reconcile it before asking another paid question.',409)
        content={'question':question,'evidence':[{
            'evidence_id':item['evidence_id'],'file_name':item.get('file_name'),
            'locator':item.get('locator'),'text':item.get('prompt_text',item['raw_text'])
        } for item in evidence]}
        limit=min(_ANSWER_OUTPUT_TOKEN_CAP,self.s.output_limit)
        messages=[
            {'role':'system','content':self.answer_prompt+'\nJSON Schema:\n'+dumps(self.answer_schema)},
            {'role':'user','content':dumps(content)},
        ]
        payload=self.payload(messages,limit)
        upper=sum(len(message['content'].encode('utf-8')) for message in messages)+256
        if upper>min(_ANSWER_INPUT_BYTE_CAP,self.s.input_limit):
            raise InvalidModelOutput('The retrieved question context exceeds the configured input limit; no API call was made.')
        evidence_fingerprint=[(item['evidence_id'],item.get('prompt_text',item['raw_text']))
                              for item in evidence]
        cache_question=' '.join(question.split())
        fingerprint=[run['project_id'],run['snapshot_id'],self.s.api_base_url,
                     *self._transport_marker(),self.s.cheap_model,
                     self.answer_prompt_hash,cache_question,evidence_fingerprint]
        task_family='answer:'+hashlib.sha256(dumps(fingerprint).encode()).hexdigest()
        task=paid_task_key(task_family,0)
        recovered=self._recover_terminal(
            run['id'],task,lambda value:validate_answer_model(value,evidence,question))
        # Preserve an exact settled response created before cache normalization.
        if recovered is None and cache_question!=question:
            legacy=[run['project_id'],run['snapshot_id'],self.s.api_base_url,
                    *self._transport_marker(),self.s.cheap_model,
                    self.answer_prompt_hash,question,evidence_fingerprint]
            legacy_family='answer:'+hashlib.sha256(dumps(legacy).encode()).hexdigest()
            recovered=self._recover_terminal(
                run['id'],paid_task_key(legacy_family,0),
                lambda value:validate_answer_model(value,evidence,question))
        if recovered is not None:return recovered
        recent=self._family_calls(run['id'],task_family)
        self._guard_family_before_request(recent,task,0,'This project question')
        amount=Decimal('0')
        self._wait_and_record(run['id'])
        attempt=self.db.reserve(run['project_id'],run['id'],task,amount,self.s.cheap_model,
                                hashlib.sha256(dumps(payload).encode()).hexdigest(),
                                Decimal('0'),Decimal('0'),
                                allow_zero=self.s.is_local_model(),interactive_question=True)
        body=self._request_timed(run['id'],attempt,payload,'project question')
        try:usage=self.billed_usage(body)
        except ValueError:
            self.db.unknown(attempt,dumps({'kind':'INVALID_RESPONSE','class':'MISSING_USAGE'}))
            raise ProviderPaused('The question response omitted usage. The reservation remains pending for billing review.',409)
        actual=Decimal('0')
        provider_id=self._safe_provider_id(body.get('id'))
        if self._terminal_response_policy(attempt,actual,usage,provider_id,body):
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('REASONING_NOT_DISABLED',kind='POLICY_ERROR'))
            raise ProviderPaused('The provider did not honor the non-reasoning setting. The cost was recorded and no answer was published.',409)
        try:
            choice=(body.get('choices') or [{}])[0]
            if choice.get('finish_reason')!='stop':raise ValueError('question output was truncated')
            text=choice.get('message',{}).get('content')
            if not isinstance(text,str) or len(text)>50000:raise ValueError('question response was empty or too large')
            data=json.loads(text);validate_answer_model(data,evidence,question)
        except Exception as exc:
            diagnostic=self._terminal_diagnostic('PROJECT_ANSWER',exc)
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=diagnostic)
            raise InvalidModelOutput(
                'The project question response failed its evidence contract '
                f'({diagnostic.get("validator","answer_contract")}); the cost was recorded '
                'and the request was not retried.') from exc
        self.db.finalize_model_call(attempt,actual,usage,provider_id,response=data)
        return ModelResult(data,attempt,False,self.s.provider)

    def answer_v2(self,run:dict,question:str,evidence:list[dict],source_conflicts:list[str],
                  visual_regions:list[dict]|None=None,images:list[dict]|None=None,
                  required_missing:list[str]|None=None)->ModelResult:
        """Answer from one QA V2 evidence bundle with claim-level citations."""
        from app.project_qa_v2 import validate_answer_v2
        visual_regions=list(visual_regions or []);images=list(images or [])
        required_missing=list(required_missing or [])
        if self.s.provider=='mock':
            raise ProviderPaused('Mock mode does not generate QA V2 answers.',409)
        blockers=self.s.live_errors()
        if blockers:raise ProviderPaused('; '.join(blockers),409)
        if run.get('status') not in ('PARTIAL','COMPLETED'):
            raise ProviderPaused('QA V2 requires a completed or partial analysis run.',409)
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',
                       (run['project_id'],),False):
            raise ProviderPaused(
                'The project has an unresolved model call. Reconcile it before asking another paid question.',409)
        if images:
            if not self.s.vision_enabled:
                raise ProviderPaused('QA V2 visual evidence requires an enabled multimodal model route.',409)
            if not visual_regions or len(images)>2:
                raise InvalidModelOutput('QA V2 visual input must contain one region and at most two images.')
            region_ids={item.get('region_id') for item in visual_regions}
            if any(item.get('region_id') not in region_ids
                   or item.get('role') not in ('PAGE_OVERVIEW','LOCAL_CROP')
                   or not isinstance(item.get('png'),bytes) or not item['png']
                   or len(item['png'])>32*1024*1024 for item in images):
                raise InvalidModelOutput('QA V2 visual input is malformed or exceeds 32 MiB.')
        elif visual_regions:
            raise InvalidModelOutput('QA V2 visual regions require their bounded page images.')
        safe_regions=[{key:item[key] for key in (
            'region_id','document_id','file_name','page_number','bbox','coordinate_system',
            'source_evidence_ids') if key in item} for item in visual_regions]
        content={
            'question':question,'effective_source_conflicts':source_conflicts,
            'required_missing':required_missing,'visual_regions':safe_regions,
            'evidence':[{
                'evidence_id':item['evidence_id'],'file_name':item.get('file_name'),
                'locator':item.get('locator'),'text':item.get('prompt_text',item['raw_text'])
            } for item in evidence],
        }
        limit=min(_ANSWER_V2_OUTPUT_TOKEN_CAP,self.s.output_limit)
        system_text=self.answer_v2_prompt+'\nJSON Schema:\n'+dumps(self.answer_v2_schema)
        user_text=dumps(content)
        model_name=self.s.vision_model if images else self.s.cheap_model
        if images:
            parts=[{'type':'text','text':user_text}]
            for index,item in enumerate(images,1):
                parts.extend([
                    {'type':'text','text':f'Image {index}: {item["role"]} for region {item["region_id"]}.'},
                    {'type':'image_url','image_url':{'url':'data:image/png;base64,'+
                        base64.b64encode(item['png']).decode('ascii')}},
                ])
            messages=[{'role':'system','content':system_text},{'role':'user','content':parts}]
        else:messages=[{'role':'system','content':system_text},{'role':'user','content':user_text}]
        payload=self.payload(messages,limit,model_name,self.answer_v2_schema,
                             'cirp_project_answer_v2')
        upper=len(system_text.encode('utf-8'))+len(user_text.encode('utf-8'))+256+len(images)*640
        if upper>min(_ANSWER_INPUT_BYTE_CAP,self.s.input_limit):
            raise InvalidModelOutput('The QA V2 evidence bundle exceeds the configured input limit; no API call was made.')
        cache_question=' '.join(question.split())
        evidence_fingerprint=[(item['evidence_id'],item.get('prompt_text',item['raw_text']))
                              for item in evidence]
        image_hashes=[(item['region_id'],item['role'],hashlib.sha256(item['png']).hexdigest())
                      for item in images]
        fingerprint=['qa-v2-answer-1',run['project_id'],run['snapshot_id'],self.s.api_base_url,
                     *self._transport_marker(),
                     model_name,self.answer_v2_prompt_hash,cache_question,evidence_fingerprint,
                     source_conflicts,required_missing,safe_regions,image_hashes,
                     self.s.inference_parameters(),self.s.structured_output_mode,limit]
        prefix='answer-v2-vision:' if images else 'answer-v2:'
        task_family=prefix+hashlib.sha256(dumps(fingerprint).encode()).hexdigest()
        task=paid_task_key(task_family,0)
        recovered=self._recover_terminal(
            run['id'],task,lambda value:validate_answer_v2(
                value,evidence,question,visual_regions,required_missing))
        if recovered is not None:return recovered
        self._guard_family_before_request(self._family_calls(run['id'],task_family),task,0,
                                          'This QA V2 question')
        self._wait_and_record(run['id'])
        attempt=self.db.reserve(
            run['project_id'],run['id'],task,Decimal('0'),model_name,
            hashlib.sha256(dumps(fingerprint).encode()).hexdigest(),Decimal('0'),Decimal('0'),
            allow_zero=self.s.is_local_model(),interactive_question=True)
        body=self._request_timed(run['id'],attempt,payload,
                                 'QA V2 visual project question' if images else 'QA V2 project question')
        try:usage=self.billed_usage(body)
        except ValueError:
            self.db.unknown(attempt,dumps({'kind':'INVALID_RESPONSE','class':'MISSING_USAGE'}))
            raise ProviderPaused(
                'The QA V2 response omitted usage. The request remains pending for billing review.',409)
        actual=Decimal('0');provider_id=self._safe_provider_id(body.get('id'))
        if self._terminal_response_policy(attempt,actual,usage,provider_id,body):
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('REASONING_NOT_DISABLED',kind='POLICY_ERROR'))
            raise ProviderPaused(
                'The provider did not honor the non-reasoning setting. The cost was recorded and no answer was published.',409)
        try:
            choice=(body.get('choices') or [{}])[0]
            if choice.get('finish_reason')!='stop':raise ValueError('QA V2 output was truncated')
            text=choice.get('message',{}).get('content')
            if not isinstance(text,str) or len(text)>50_000:
                raise ValueError('QA V2 response was empty or too large')
            data=json.loads(text);validate_answer_v2(
                data,evidence,question,visual_regions,required_missing)
        except Exception as exc:
            diagnostic=self._terminal_diagnostic('PROJECT_ANSWER',exc)
            self.db.finalize_model_call(attempt,actual,usage,provider_id,diagnostic=diagnostic)
            raise InvalidModelOutput(
                'The QA V2 response failed its claim and evidence contract; the cost was recorded '
                'and the request was not retried.') from exc
        self.db.finalize_model_call(attempt,actual,usage,provider_id,response=data)
        return ModelResult(data,attempt,False,self.s.provider)

    def _execute_evidence_decision(
            self,*,run:dict,task:str,task_family:str,request_hash:str,model_name:str,
            route:dict|None,payload:dict,thinking:bool,normalize,input_receipt,
            verify_receipt_sources,request_label:str,diagnostic_class:str,
            terminal_contract_message:str,terminal_identity_message:str,
            contract_failure_message:str,policy_failure_message:str,
            missing_usage_message:str,preflight=None)->ModelResult:
        """The sole paid lifecycle for bounded reference decisions.

        Entrypoints own input preparation and source/receipt authentication;
        this kernel owns terminal recovery, reservation, provider exchange and
        atomic settlement for both legacy v3 and the selector10 contract.
        """
        from app.reference_input_commitment import VERSION,sha256

        def verify_named_commitment(call_id:str,receipt:dict)->None:
            if route is None:return
            call=self.db.one('''SELECT reference_input_commitment_version,
                                        reference_input_commitment_sha256
                                 FROM model_calls WHERE id=?''',(call_id,))
            if (call['reference_input_commitment_version']!=VERSION
                    or call['reference_input_commitment_sha256']!=sha256(route,receipt)):
                raise ProviderPaused(terminal_identity_message,409)

        try:recovered=self._recover_terminal(run['id'],task,normalize)
        except InvalidModelOutput as exc:
            settled=self.db.one('''SELECT id,request_hash FROM model_calls
                                   WHERE run_id=? AND task_key=? AND state='SETTLED_ERROR'
                                   ORDER BY created_at DESC LIMIT 1''',(run['id'],task),False)
            if settled is None or settled['request_hash']!=request_hash:raise
            receipt=input_receipt(settled['id'],cached=True)
            verify_receipt_sources(receipt);verify_named_commitment(settled['id'],receipt)
            raise InvalidModelOutput(terminal_contract_message,execution_receipt=receipt) from exc
        if recovered is not None:
            saved=self.db.one('SELECT request_hash FROM model_calls WHERE id=?',(recovered.request_id,))
            if saved['request_hash']!=request_hash:
                raise ProviderPaused(terminal_identity_message,409)
            receipt=input_receipt(recovered.request_id,cached=True)
            verify_receipt_sources(receipt);verify_named_commitment(recovered.request_id,receipt)
            return ModelResult(normalize(recovered.data),recovered.request_id,True,recovered.mode,receipt)
        self._guard_family_before_request(self._family_calls(run['id'],task_family),task,0,request_label)
        self._wait_and_record(run['id'])
        if preflight is not None:preflight()
        attempt=self.db.reserve(
            run['project_id'],run['id'],task,Decimal('0'),model_name,request_hash,
            Decimal('0'),Decimal('0'),allow_zero=self.s.is_local_model(),interactive_question=True)
        receipt=input_receipt(attempt,cached=False)
        commitment=(VERSION,sha256(route,receipt)) if route is not None else None
        body=self._request_timed(run['id'],attempt,payload,request_label)
        try:usage=self.billed_usage(body)
        except ValueError:
            self.db.unknown(attempt,dumps({'kind':'INVALID_RESPONSE','class':'MISSING_USAGE'}))
            raise ProviderPaused(missing_usage_message,409)
        actual=Decimal('0');provider_id=self._safe_provider_id(body.get('id'))
        if self._terminal_response_policy(attempt,actual,usage,provider_id,body,allow_reasoning=thinking):
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('REASONING_NOT_DISABLED',kind='POLICY_ERROR'),
                reference_input_commitment=commitment)
            raise ProviderPaused(policy_failure_message,409)
        try:
            choice=(body.get('choices') or [{}])[0]
            if choice.get('finish_reason')!='stop':raise ValueError('QA V3 output was truncated')
            text=choice.get('message',{}).get('content')
            if not isinstance(text,str) or len(text)>50_000:
                raise ValueError('QA V3 response was empty or too large')
            raw_data=json.loads(text);data=normalize(raw_data)
        except Exception as exc:
            diagnostic=self._terminal_diagnostic(diagnostic_class,exc)
            self.db.finalize_model_call(attempt,actual,usage,provider_id,diagnostic=diagnostic,
                                        reference_input_commitment=commitment)
            raise InvalidModelOutput(contract_failure_message,execution_receipt=receipt) from exc
        self.db.finalize_model_call(attempt,actual,usage,provider_id,response=raw_data,
                                    reference_input_commitment=commitment)
        return ModelResult(data,attempt,False,self.s.provider,receipt)

    def _projection_decision_v10(
            self,run:dict,question:str,evidence:list[dict],source_conflicts:list[str],
            request_history:list[dict],round_index:int,*,visual_regions:list[dict]|None,
            images:list[dict]|None,route:dict|None,projection_input:object|None,
            initial_projection_context_sha256:str|None,
            expected_prompt_contract_hash:str|None)->ModelResult:
        """One receipt4 projection decision using the existing paid-call lifecycle.

        Selector10 deliberately has no fallback to E rows.  Its narrow entry
        authenticates the frozen PDF projection before both terminal recovery
        and reservation; the shared reserve/finalize/recover primitives remain
        the only accounting path.
        """
        from app.evidence_loop import MAX_MODEL_DECISIONS
        from app.reference_projection_decision import (SELECTOR_VERSION,prompt_contract,
                                                        validate_projection_decision)
        from app.reference_projection_input import authenticate_projection_input
        from app.reference_projection_receipt import (authenticate_projection_receipt,
                                                       build_projection_receipt)
        from app.reference_text_profiles import require_channel,route as canonical_route,inference_parameters
        from app.uploads import Uploads
        if (evidence or visual_regions or images or route is None
                or projection_input is None):
            raise InvalidModelOutput('Selector v10 requires only an authenticated projection input and a named text route.')
        route=canonical_route(route)
        if route is None:
            raise InvalidModelOutput('Selector v10 requires a canonical named text route.')
        require_channel(self.s,route)
        if (type(round_index) is not int or round_index!=0
                or not isinstance(question,str) or not question.strip()
                or not isinstance(initial_projection_context_sha256,str)
                or not re.fullmatch(r'[0-9a-f]{64}',initial_projection_context_sha256)
                or not isinstance(expected_prompt_contract_hash,str)
                or not re.fullmatch(r'[0-9a-f]{64}',expected_prompt_contract_hash)):
            raise InvalidModelOutput('Selector v10 projection identity or prompt contract is malformed.')
        uploads=Uploads(self.db,self.s)
        # This establishes both the trusted dataclass boundary and the current
        # run/snapshot/PDF bytes binding.  Never serialize caller-owned values
        # before the re-build succeeds.
        authenticate_projection_input(projection_input,self.db,uploads)
        if (projection_input.run_id!=run.get('id')
                or projection_input.project_id!=run.get('project_id')
                or projection_input.snapshot_id!=run.get('snapshot_id')
                or projection_input.question!=question):
            raise InvalidModelOutput('Selector v10 projection input is outside this frozen run and question.')
        if initial_projection_context_sha256!=projection_input.projection_context_sha256:
            raise DomainError('PREVIEW_STALE: Preview this projected question again before asking.',409)
        bundle_conflicts=projection_input.context.get('source_conflicts')
        if (not isinstance(source_conflicts,list) or source_conflicts!=bundle_conflicts
                or any(not isinstance(item,str) for item in source_conflicts)):
            raise InvalidModelOutput('Selector v10 source conflicts do not match the authenticated projection input.')
        if request_history!=[]:
            raise InvalidModelOutput('Selector v10 stage one does not accept supplement request history.')
        if (not isinstance(request_history,list) or len(request_history)>200 or any(
                not isinstance(item,dict) or set(item)!={'tool','query'}
                or item['tool'] not in ('FIND_IDENTIFIER','SEARCH_TEXT')
                or not isinstance(item['query'],str) or not 2<=len(item['query'].strip())<=240
                for item in request_history)):
            raise InvalidModelOutput('Selector v10 request history is malformed.')
        safe_history=[{'tool':item['tool'],'query':item['query']} for item in request_history]
        if self.s.provider=='mock':
            raise ProviderPaused('Mock mode does not generate projection decisions.',409)
        blockers=self.s.live_errors()
        if blockers:raise ProviderPaused('; '.join(blockers),409)
        if run.get('status') not in ('PARTIAL','COMPLETED'):
            raise ProviderPaused('Selector v10 requires a completed or partial analysis run.',409)
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',
                       (run['project_id'],),False):
            raise ProviderPaused('The project has an unresolved model call. Reconcile it before asking selector v10.',409)
        system_text,decision_schema,prompt_hash=prompt_contract()
        if prompt_hash!=expected_prompt_contract_hash:
            raise DomainError('PREVIEW_STALE: Preview this projected question again before asking.',409)
        # The user payload is an authenticated projection view, not the legacy
        # selected E rows.  Receipt4 independently parses this exact JSON.
        user_text=dumps({
            'contract_version':'project-projection-decision-1',
            'projection_input_sha256':projection_input.projection_input_sha256,
            'round':round_index+1,'question':question,
            'remaining_model_decisions':1,
            'required_answer_parts':projection_input.required_parts,
            'projection_context':projection_input.context,
            'effective_source_conflicts':source_conflicts,
            'request_history':safe_history,
        })
        model_name=route['text_model'];limit=route['max_output_tokens']
        payload=self.payload(
            [{'role':'system','content':system_text},{'role':'user','content':user_text}],
            limit,model_name,decision_schema,'project-projection-decision-1',
            inference_parameters(route))
        upper=len(system_text.encode('utf-8'))+len(user_text.encode('utf-8'))+256
        fingerprint=['projection-decision-v10-1',run['project_id'],run['snapshot_id'],
                     self.s.api_base_url,*self._transport_marker(),model_name,prompt_hash,
                     ' '.join(question.split()),round_index,
                     projection_input.projection_input_sha256,
                     projection_input.projection_context_sha256,
                     projection_input.ordered_projection_manifest_sha256,
                     initial_projection_context_sha256,
                     source_conflicts,safe_history,inference_parameters(route),
                     route['structured_output_mode'],limit,
                     route['profile_version'],route['profile_id']]
        request_hash=hashlib.sha256(dumps(fingerprint).encode()).hexdigest()
        # Keep the existing interactive-QA task-key syntax: Database.reserve
        # enforces this durable idempotency boundary for all bounded rounds.
        task_family=f'answer-v3-r{round_index}:'+request_hash
        task=paid_task_key(task_family,0)

        def receipt(call_id:str,*,cached:bool)->dict:
            return build_projection_receipt(
                call_id=call_id,round_index=round_index,request_hash=request_hash,
                question=question,bundle=projection_input,route=route,
                system_text=system_text,user_text=user_text,upper=upper,limit=limit,
                cached=cached,prompt_contract_hash=prompt_hash,
                initial_projection_context_sha256=initial_projection_context_sha256)

        def authenticate_receipt(value:dict)->None:
            authenticate_projection_input(projection_input,self.db,uploads)
            authenticate_projection_receipt(value,projection_input,system_text=system_text,
                                            user_text=user_text,route=route)

        normalize=lambda value:validate_projection_decision(
            value,projection_input,source_conflicts=source_conflicts,
            request_history=safe_history,remaining_decisions=1)
        return self._execute_evidence_decision(
            run=run,task=task,task_family=task_family,request_hash=request_hash,
            model_name=model_name,route=route,payload=payload,
            thinking=route['inference_mode']=='thinking-low',normalize=normalize,
            input_receipt=receipt,verify_receipt_sources=authenticate_receipt,
            request_label=f'Selector v10 projection decision {round_index+1}',
            diagnostic_class='PROJECT_PROJECTION_DECISION',
            terminal_contract_message=(
                'This selector v10 round already has a settled contract-rejected response; '
                'the request was not retried.'),
            terminal_identity_message=(
                'The settled selector v10 input commitment is inconsistent; no retry was started.'),
            contract_failure_message=(
                'The selector v10 response failed its bounded projection decision contract; '
                'the request was not retried.'),
            policy_failure_message=(
                'The provider did not honor the configured inference mode; no decision was published.'),
            missing_usage_message=(
                'The selector v10 response omitted usage. The request remains pending for billing review.'),
            preflight=lambda:(authenticate_projection_input(projection_input,self.db,uploads),
                              receipt('CALL-'+'0'*32,cached=False)))

    def _projection_loop_request_hash(self, run: dict, stage, route: dict,
                                      prior_chain: list[dict], prompt_hash: str, *, protocol=None) -> str:
        """Return the paid-request identity from authenticated loop inputs only."""
        from app.reference_projection_loop_receipt import chain_sha256, request_history
        from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1, resolve_projection_protocol
        from app.reference_text_profiles import inference_parameters
        protocol=resolve_projection_protocol(PROJECTION_PROTOCOL_V1 if protocol is None else protocol)
        chain_hash = chain_sha256(prior_chain, protocol=protocol)
        fingerprint = [
            protocol.request_domain, run['project_id'], run['snapshot_id'],
            self.s.api_base_url, *self._transport_marker(), route['text_model'], prompt_hash,
            ' '.join(stage.question.split()), stage.round_index,
            stage.projection_input_sha256, stage.projection_context_sha256,
            stage.ordered_projection_manifest_sha256,
            stage.initial_projection_input_sha256,
            stage.initial_projection_context_sha256,
            stage.initial_ordered_projection_manifest_sha256,
            chain_hash, request_history(prior_chain, protocol=protocol), inference_parameters(route),
            route['structured_output_mode'], route['max_output_tokens'],
            route['profile_version'], route['profile_id'],
        ]
        return hashlib.sha256(dumps(fingerprint).encode()).hexdigest()

    def _authenticate_projection_loop_chain(
            self,run:dict,question:str,stage,route:dict,prior_chain:list[dict],
            preview_proof:dict,uploads,system_text:str,*,protocol=None)->list[dict]:
        """Rebuild every settled selector10 supplement before another call.

        The caller's chain is only a locator: every decision is re-read from
        the settled ledger response and every next stage is appended locally.
        """
        from app.reference_input_commitment import VERSION,require_ledger_profile,sha256
        from app.reference_projection_decision import validate_projection_decision_for
        from app.reference_projection_loop_receipt import (
            authenticate_projection_loop_receipt,build_loop_user_text,chain_sha256,request_history)
        from app.reference_projection_preview import projection_preview_proof
        from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1, resolve_projection_protocol
        from app.reference_projection_stage import (
            VERSION as STAGE_VERSION, ProjectionStage, append_projection_stage,
            prepare_projection_stage,
        )
        protocol=resolve_projection_protocol(PROJECTION_PROTOCOL_V1 if protocol is None else protocol)
        if not isinstance(prior_chain,list) or len(prior_chain)>2:
            raise DomainError('Projection loop prior chain is malformed.',409)
        if not isinstance(stage,ProjectionStage) or stage.stage_version!=STAGE_VERSION:
            raise DomainError('Projection loop stage is malformed.',409)
        initial=prepare_projection_stage(stage.initial_bundle,self.db,uploads)
        if (initial.initial_bundle.run_id!=run.get('id')
                or initial.initial_bundle.project_id!=run.get('project_id')
                or initial.initial_bundle.snapshot_id!=run.get('snapshot_id')
                or initial.question!=question
                or projection_preview_proof(run,initial,route,protocol=protocol)!=preview_proof):
            raise DomainError('PREVIEW_STALE: Preview this projected question again before asking.',409)
        if len(prior_chain)!=stage.round_index:
            raise DomainError('Projection loop stage and prior chain differ.',409)
        # Reject malformed receipt sequencing before reading any provider data.
        chain_sha256(prior_chain, protocol=protocol)
        current=initial;authenticated=[]
        for index,item in enumerate(prior_chain):
            receipt=item['receipt'];supplied=item['decision']
            history=request_history(authenticated, protocol=protocol)
            user_text=build_loop_user_text(current,authenticated, protocol=protocol)
            authenticate_projection_loop_receipt(
                receipt,current,prior_chain=authenticated,system_text=system_text,
                user_text=user_text,route=route,protocol=protocol)
            expected_hash=self._projection_loop_request_hash(
                run,current,route,authenticated,
                receipt['prompt_contract_hash'], protocol=protocol)
            expected_task=paid_task_key(f'answer-v3-r{index}:{expected_hash}',0)
            call=self.db.one('''SELECT project_id,run_id,task_key,state,request_hash,response,error,model,
                                        actual_units,
                                        reference_input_commitment_version,
                                        reference_input_commitment_sha256
                                 FROM model_calls WHERE id=?''',(receipt['model_call_id'],),False)
            if (call is None or call['project_id']!=run['project_id'] or call['run_id']!=run['id']
                    or call['task_key']!=expected_task or call['state']!='SETTLED'
                    or call['request_hash']!=expected_hash or receipt['request_hash']!=expected_hash
                    or call['response'] is None or call['error'] is not None
                    or call['model']!=receipt['model'] or call['actual_units'] is None):
                raise DomainError('Projection loop prior receipt does not match a settled model call.',409)
            require_ledger_profile(call,route['profile_id'])
            if (call['reference_input_commitment_version']!=VERSION
                    or call['reference_input_commitment_sha256']!=sha256(route,receipt)):
                raise DomainError('Projection loop prior receipt has no matching input commitment.',409)
            try:stored=json.loads(call['response'] or '')
            except (TypeError,ValueError,json.JSONDecodeError) as exc:
                raise DomainError('Projection loop prior settled response is malformed.',409) from exc
            if dumps(stored)!=dumps(supplied):
                raise DomainError('Projection loop prior decision differs from its settled response.',409)
            decision=validate_projection_decision_for(
                protocol,stored,current,source_conflicts=current.context['source_conflicts'],
                request_history=history,remaining_decisions=3-index)
            if decision['status']!='NEED_EVIDENCE':
                raise DomainError('Projection loop prior decision does not request evidence.',409)
            next_stage=append_projection_stage(current,decision['requests'],self.db,uploads)
            if next_stage.added_page_count<=0:
                raise DomainError('Projection loop supplemental request did not add a projected page.',409)
            authenticated.append({'receipt':receipt,'decision':decision})
            current=next_stage
        if current!=stage:
            raise DomainError('Projection loop current stage does not match authenticated supplements.',409)
        return authenticated

    def _projection_loop_decision_v10(
            self,run:dict,question:str,stage,route:dict,*,prior_chain:list[dict],
            preview_proof:dict)->ModelResult:
        """Historical V1 wrapper; V2 must be selected explicitly."""
        from app.reference_projection_protocol import PROJECTION_PROTOCOL_V1
        return self._projection_loop_decision_core(
            run, question, stage, route, prior_chain=prior_chain, preview_proof=preview_proof,
            protocol=PROJECTION_PROTOCOL_V1)

    def _projection_loop_decision_v10_loop2(
            self,run:dict,question:str,stage,route:dict,*,prior_chain:list[dict],
            preview_proof:dict)->ModelResult:
        """Explicit V2 wrapper; no public endpoint selects this path."""
        from app.reference_projection_protocol import PROJECTION_PROTOCOL_V2
        return self._projection_loop_decision_core(
            run, question, stage, route, prior_chain=prior_chain, preview_proof=preview_proof,
            protocol=PROJECTION_PROTOCOL_V2)

    def _projection_loop_decision_core(
            self,run:dict,question:str,stage,route:dict,*,prior_chain:list[dict],
            preview_proof:dict,protocol)->ModelResult:
        """Execute one authenticated append-only selector10 decision round."""
        from app.reference_projection_protocol import resolve_projection_protocol
        protocol=resolve_projection_protocol(protocol)
        from app.evidence_loop import MAX_MODEL_DECISIONS
        from app.reference_projection_decision import prompt_contract_for,validate_projection_decision_for
        from app.reference_projection_loop_receipt import (
            LOOP_VERSION,authenticate_projection_loop_receipt,build_loop_user_text,
            build_projection_loop_receipt,chain_sha256,request_history)
        from app.reference_text_profiles import require_channel,route as canonical_route,inference_parameters
        from app.uploads import Uploads
        canonical=canonical_route(route)
        if canonical is None:
            raise InvalidModelOutput('Selector v10 loop requires a canonical named text route.')
        require_channel(self.s,canonical)
        if self.s.provider=='mock':
            raise ProviderPaused('Mock mode does not generate selector v10 loop decisions.',409)
        if run.get('status') not in ('PARTIAL','COMPLETED'):
            raise ProviderPaused('Selector v10 loop requires a completed or partial analysis run.',409)
        blockers=self.s.live_errors()
        if blockers:raise ProviderPaused('; '.join(blockers),409)
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',
                       (run['project_id'],),False):
            raise ProviderPaused('The project has an unresolved model call. Reconcile it before asking selector v10.',409)
        system_text,decision_schema,prompt_hash=prompt_contract_for(protocol)
        uploads=Uploads(self.db,self.s)
        authenticated=self._authenticate_projection_loop_chain(
            run,question,stage,canonical,prior_chain,preview_proof,uploads,system_text,protocol=protocol)
        history=request_history(authenticated, protocol=protocol)
        user_text=build_loop_user_text(stage,authenticated, protocol=protocol)
        model_name=canonical['text_model'];limit=canonical['max_output_tokens']
        payload=self.payload(
            [{'role':'system','content':system_text},{'role':'user','content':user_text}],
            limit,model_name,decision_schema,protocol.loop_version,
            inference_parameters(canonical))
        upper=len(system_text.encode('utf-8'))+len(user_text.encode('utf-8'))+256
        request_hash=self._projection_loop_request_hash(
            run,stage,canonical,authenticated,prompt_hash,protocol=protocol)
        task_family=f'answer-v3-r{stage.round_index}:{request_hash}'
        task=paid_task_key(task_family,0)

        def receipt(call_id:str,*,cached:bool)->dict:
            return build_projection_loop_receipt(
                call_id=call_id,round_index=stage.round_index,request_hash=request_hash,
                question=question,stage=stage,prior_chain=authenticated,route=canonical,
                system_text=system_text,user_text=user_text,upper=upper,limit=limit,
                cached=cached,prompt_contract_hash=prompt_hash,protocol=protocol)

        def authenticate_current_ledger(value:dict)->None:
            """Bind a recovered current receipt to its exact terminal row.

            ``_recover_terminal`` deliberately has broad legacy recovery
            semantics.  The append-only loop is narrower: it must not turn a
            row whose model, owner, state, or terminal shape changed after
            settlement into a cache hit.
            """
            from app.reference_input_commitment import VERSION, require_ledger_profile, sha256
            call=self.db.one('''SELECT project_id,run_id,task_key,request_hash,model,state,
                                        actual_units,response,error,
                                        reference_input_commitment_version,
                                        reference_input_commitment_sha256
                                 FROM model_calls WHERE id=?''',(value['model_call_id'],),False)
            if (call is None or call['project_id']!=run['project_id'] or call['run_id']!=run['id']
                    or call['task_key']!=task or call['request_hash']!=request_hash
                    or call['model']!=model_name or call['actual_units'] is None):
                raise DomainError('Projection loop recovered ledger identity is inconsistent.',409)
            require_ledger_profile(call,canonical['profile_id'])
            if (call['reference_input_commitment_version']!=VERSION
                    or call['reference_input_commitment_sha256']!=sha256(canonical,value)):
                raise DomainError('Projection loop recovered input commitment is inconsistent.',409)
            if call['state']=='SETTLED':
                if call['response'] is None or call['error'] is not None:
                    raise DomainError('Projection loop recovered success terminal is inconsistent.',409)
                return
            if call['state']!='SETTLED_ERROR' or call['response'] is not None:
                raise DomainError('Projection loop recovered terminal state is inconsistent.',409)
            try:diagnostic=json.loads(call['error'] or '')
            except (TypeError,ValueError,json.JSONDecodeError) as exc:
                raise DomainError('Projection loop recovered terminal diagnostic is inconsistent.',409) from exc
            from app.answer_diagnostics import valid_numeric_diagnostic
            allowed={'kind','class','exception','path','validator','semantic_detail'}
            safe_code=re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
            safe_path=re.compile(r'^[A-Za-z0-9_.-]{0,160}$')
            if (not isinstance(diagnostic,dict) or set(diagnostic)-allowed
                    or diagnostic.get('kind')!='CONTRACT_ERROR'
                    or diagnostic.get('class')!='PROJECT_PROJECTION_DECISION'
                    or any(not isinstance(diagnostic.get(key),str)
                           or not (safe_path if key=='path' else safe_code).fullmatch(diagnostic[key])
                           for key in ('exception','path','validator') if key in diagnostic)
                    or ('semantic_detail' in diagnostic and not valid_numeric_diagnostic(diagnostic))):
                raise DomainError('Projection loop recovered terminal diagnostic is inconsistent.',409)

        def authenticate_receipt(value:dict)->None:
            self._authenticate_projection_loop_chain(
                run,question,stage,canonical,authenticated,preview_proof,uploads,system_text,protocol=protocol)
            authenticate_projection_loop_receipt(
                value,stage,prior_chain=authenticated,system_text=system_text,
                user_text=user_text,route=canonical,protocol=protocol)
            authenticate_current_ledger(value)

        normalize=lambda value:validate_projection_decision_for(
            protocol,value,stage,source_conflicts=stage.context['source_conflicts'],
            request_history=history,remaining_decisions=MAX_MODEL_DECISIONS-stage.round_index)
        return self._execute_evidence_decision(
            run=run,task=task,task_family=task_family,request_hash=request_hash,
            model_name=model_name,route=canonical,payload=payload,
            thinking=canonical['inference_mode']=='thinking-low',normalize=normalize,
            input_receipt=receipt,verify_receipt_sources=authenticate_receipt,
            request_label=f'Selector v10 {protocol.loop_version} decision {stage.round_index+1}',
            diagnostic_class='PROJECT_PROJECTION_DECISION',
            terminal_contract_message=(
                'This selector v10 loop round already has a settled contract-rejected response; '
                'the request was not retried.'),
            terminal_identity_message=(
                'The settled selector v10 loop input commitment is inconsistent; no retry was started.'),
            contract_failure_message=(
                'The selector v10 loop response failed its bounded projection decision contract; '
                'the request was not retried.'),
            policy_failure_message=(
                'The provider did not honor the configured inference mode; no decision was published.'),
            missing_usage_message=(
                'The selector v10 loop response omitted usage. The request remains pending for billing review.'),
            preflight=lambda:(self._authenticate_projection_loop_chain(
                run,question,stage,canonical,authenticated,preview_proof,uploads,system_text,protocol=protocol),
                receipt('CALL-'+'0'*32,cached=False)))

    def evidence_decision_v3(self,run:dict,question:str,evidence:list[dict],
                             source_conflicts:list[str],request_history:list[dict],
                             round_index:int,visual_regions:list[dict]|None=None,
                             images:list[dict]|None=None,*,selector_version:str|None=None,
                             route:dict|None=None,initial_evidence_manifest_sha256:str|None=None,
                             expected_prompt_contract_hash:str|None=None,
                             projection_input:object|None=None,
                             initial_projection_context_sha256:str|None=None)->ModelResult:
        """Make one bounded QA V3 answer-or-evidence decision."""
        if selector_version=='literal-page-selector-10':
            return self._projection_decision_v10(
                run,question,evidence,source_conflicts,request_history,round_index,
                visual_regions=visual_regions,images=images,route=route,
                projection_input=projection_input,
                initial_projection_context_sha256=initial_projection_context_sha256,
                expected_prompt_contract_hash=expected_prompt_contract_hash)
        if projection_input is not None or initial_projection_context_sha256 is not None:
            raise InvalidModelOutput('Projection input fields require selector v10.')
        from app.evidence_loop import MAX_MODEL_DECISIONS,_answer_parts,validate_evidence_decision
        from app.page_selector import (COMPLETE_SELECTOR_VERSION,LAYOUT_BOUND_SELECTOR_VERSION,
                                       COMPLETE_SELECTOR_VERSIONS)
        from app.reference_text_profiles import require_channel,route as canonical_route
        if route is not None:
            route=canonical_route(route)
            require_channel(self.s,route)
            if (selector_version not in (COMPLETE_SELECTOR_VERSION,LAYOUT_BOUND_SELECTOR_VERSION) or images or visual_regions
                    or not isinstance(initial_evidence_manifest_sha256,str)
                    or not re.fullmatch(r'[0-9a-f]{64}',initial_evidence_manifest_sha256)):
                raise InvalidModelOutput('Named Reference text profiles require complete text input and an initial evidence manifest.')
        if selector_version not in (None,*COMPLETE_SELECTOR_VERSIONS):
            raise InvalidModelOutput('QA V3 context policy does not match the selector.')
        complete=selector_version in COMPLETE_SELECTOR_VERSIONS
        layout_bound=selector_version==LAYOUT_BOUND_SELECTOR_VERSION
        if layout_bound and (route is None or images or visual_regions):
            raise InvalidModelOutput('Selector v9 requires a canonical named text-only route without images.')
        if layout_bound and any('layout_lines' in item for item in evidence):
            raise InvalidModelOutput('Selector v9 evidence must not carry legacy layout lines.')
        if complete and any(item.get('prompt_text',item['raw_text'])!=item['raw_text'] for item in evidence):
            raise InvalidModelOutput('Complete-source context contains clipped source text.')
        decision_schema=self.complete_decision_schema if complete else self.evidence_decision_schema
        prompt_hash=self.complete_decision_prompt_hash if complete else self.evidence_decision_prompt_hash
        visual_regions=list(visual_regions or []);images=list(images or [])
        if self.s.provider=='mock':
            raise ProviderPaused('Mock mode does not generate QA V3 decisions.',409)
        if type(round_index) is not int or not 0<=round_index<MAX_MODEL_DECISIONS:
            raise InvalidModelOutput('QA V3 round is outside the bounded decision loop.')
        blockers=self.s.live_errors()
        if blockers:raise ProviderPaused('; '.join(blockers),409)
        if run.get('status') not in ('PARTIAL','COMPLETED'):
            raise ProviderPaused('QA V3 requires a completed or partial analysis run.',409)
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',
                       (run['project_id'],),False):
            raise ProviderPaused(
                'The project has an unresolved model call. Reconcile it before asking QA V3.',409)
        image_hashes=[]
        if images:
            if not self.s.vision_enabled:
                raise ProviderPaused('QA V3 page images require an enabled multimodal model route.',409)
            if (not visual_regions or len(images)!=len(visual_regions) or len(images)>3
                    or sum(len(item.get('png',b'')) for item in images)>48*1024*1024):
                raise InvalidModelOutput('QA V3 visual input exceeds its page or byte bound.')
            region_by_id={item.get('region_id'):item for item in visual_regions}
            if (len(region_by_id)!=len(visual_regions)
                    or any(not isinstance(item.get('region_id'),str)
                           or not isinstance(item.get('document_id'),str)
                           or type(item.get('page_number')) is not int or item['page_number']<1
                           or not isinstance(item.get('bbox'),list) or len(item['bbox'])!=4
                           or not isinstance(item.get('coordinate_system'),str)
                           or not isinstance(item.get('image_sha256'),str)
                           or not re.fullmatch(r'[0-9a-f]{64}',item['image_sha256'])
                           for item in visual_regions)):
                raise InvalidModelOutput('QA V3 visual input is malformed.')
            for item in images:
                region=region_by_id.get(item.get('region_id'));png=item.get('png')
                if (region is None or item.get('role')!='PAGE_OVERVIEW'
                        or not isinstance(png,bytes) or not png or len(png)>32*1024*1024):
                    raise InvalidModelOutput('QA V3 visual input is malformed.')
                digest=hashlib.sha256(png).hexdigest()
                if region['image_sha256']!=digest:
                    raise InvalidModelOutput('QA V3 visual input hash does not match its audit metadata.')
                image_hashes.append((item['region_id'],digest))
        elif visual_regions:
            raise InvalidModelOutput('QA V3 visual regions require their bounded page images.')
        safe_regions=[{'region_ref':f'V{index}',**{key:item[key] for key in (
            'document_id','file_name','page_number','bbox','coordinate_system',
            'source_evidence_ids','image_sha256') if key in item}}
            for index,item in enumerate(visual_regions,1)]
        region_refs={item['region_id']:f'V{index}'
                     for index,item in enumerate(visual_regions,1)}
        safe_history=[{'tool':item['tool'],'query':item['query']} for item in request_history]
        answer_parts=_answer_parts(question)
        content={
            'question':question,'round':round_index+1,
            'remaining_model_decisions':MAX_MODEL_DECISIONS-round_index,
            'required_answer_parts':answer_parts,
            'effective_source_conflicts':source_conflicts,
            'request_history':safe_history,
            'visual_regions':safe_regions,
            'evidence':[],
            'source_groups':_evidence_source_groups(evidence),
        }
        layout_navigation_identity=None
        if layout_bound:
            from app.reference_layout_input import navigation_and_identity
            layout_navigation,layout_navigation_identity=navigation_and_identity(evidence)
            content['layout_navigation']=layout_navigation
        for index,item in enumerate(evidence,1):
            safe_item={
                'evidence_ref':f'E{index}',
                'text':item.get('prompt_text',item['raw_text'])}
            if not layout_bound:
                layout=item.get('layout_lines')
                if isinstance(layout,str) and layout:safe_item['layout_lines']=layout
            content['evidence'].append(safe_item)
        thinking=(route is not None and route['inference_mode']=='thinking-low') or (
            route is None and self.s.provider=='deepseek' and self.s.reference_reasoning_effort!='none')
        limit=(route['max_output_tokens'] if route is not None else min(
            (_EVIDENCE_DECISION_THINKING_OUTPUT_TOKEN_CAP if thinking else _EVIDENCE_DECISION_OUTPUT_TOKEN_CAP),self.s.output_limit))
        if layout_bound:
            from app.reference_layout_input import prompt_contract
            system_text,prompt_hash=prompt_contract()
            if (expected_prompt_contract_hash is not None
                    and prompt_hash!=expected_prompt_contract_hash):
                raise DomainError(
                    'PREVIEW_STALE: Preview this question and profile again before asking.',409)
        else:
            system_text=self.evidence_decision_prompt+'\nJSON Schema:\n'+dumps(decision_schema)
        user_text=dumps(content)
        if images:
            parts=[{'type':'text','text':user_text}]
            for index,item in enumerate(images,1):
                parts.extend([
                    {'type':'text','text':f'Selected source page {index}, region {region_refs[item["region_id"]]}.'},
                    {'type':'image_url','image_url':{'url':'data:image/png;base64,'+
                        base64.b64encode(item['png']).decode('ascii')}},
                ])
            messages=[{'role':'system','content':system_text},{'role':'user','content':parts}]
        else:messages=[{'role':'system','content':system_text},{'role':'user','content':user_text}]
        model_name=route['text_model'] if route is not None else (self.s.vision_model if images else self.s.cheap_model)
        payload=self.payload(
            messages,limit,model_name,decision_schema,
            'cirp_evidence_decision_v3',(__import__('app.reference_text_profiles',fromlist=['inference_parameters']).inference_parameters(route)
                                         if route is not None else self.s.reference_inference_parameters()))
        upper=len(system_text.encode('utf-8'))+len(user_text.encode('utf-8'))+256+len(images)*640
        if not complete and upper>min(_ANSWER_INPUT_BYTE_CAP,self.s.input_limit):
            raise InvalidModelOutput('The QA V3 evidence context exceeds the configured input limit; no API call was made.')
        evidence_fingerprint=[(
            item['evidence_id'],item.get('prompt_text',item['raw_text']),
            item.get('layout_lines')) for item in evidence]
        if layout_bound:
            evidence_fingerprint=[(item['evidence_id'],item.get('prompt_text',item['raw_text']))
                                  for item in evidence]
        fingerprint=['qa-v3-evidence-loop-2',run['project_id'],run['snapshot_id'],
                     self.s.api_base_url,*self._transport_marker(),model_name,
                     prompt_hash,
                     ' '.join(question.split()),answer_parts,round_index,evidence_fingerprint,
                     content['source_groups'],source_conflicts,safe_history,safe_regions,image_hashes,
                     (__import__('app.reference_text_profiles',fromlist=['inference_parameters']).inference_parameters(route)
                      if route is not None else self.s.reference_inference_parameters()),
                     route['structured_output_mode'] if route else self.s.structured_output_mode,limit,
                     *((route['profile_version'],route['profile_id'],initial_evidence_manifest_sha256)
                       if route else (None,))]
        neutral_input_hash=None
        if complete:
            fingerprint[0]='qa-v3-evidence-loop-3'
            if layout_bound:
                from app.reference_layout_input import LAYOUT_CONTEXT_POLICY
                neutral_input_hash=hashlib.sha256(dumps([
                    'reference-profile-neutral-input-1',system_text,user_text,[]
                ]).encode('utf-8')).hexdigest()
                fingerprint.extend([selector_version,LAYOUT_CONTEXT_POLICY,
                                    layout_navigation_identity,neutral_input_hash])
            else:
                fingerprint.extend([selector_version,'COMPLETE_SELECTED_SCOPE_V1'])
        request_hash=hashlib.sha256(dumps(fingerprint).encode()).hexdigest()
        task_family=f'answer-v3-r{round_index}:'+request_hash
        task=paid_task_key(task_family,0)
        def input_receipt(call_id:str,*,cached:bool)->dict:
            return self._evidence_input_receipt(
                call_id=call_id,round_index=round_index,request_hash=request_hash,
                question=question,evidence=evidence,images=images,model_name=model_name,
                system_text=system_text,user_text=user_text,upper=upper,limit=limit,
                cached=cached,selector_version=selector_version,route=route,
                initial_evidence_manifest_sha256=initial_evidence_manifest_sha256,
                layout_navigation_identity=layout_navigation_identity,
                prompt_contract_hash=prompt_hash,profile_neutral_input_sha256=neutral_input_hash)

        def verify_layout_receipt_sources(receipt:dict)->None:
            if not layout_bound:return
            from app.reference_layout_input import authenticate_receipt_sources
            authenticate_receipt_sources(receipt,{item['evidence_id']:item for item in evidence})

        normalize=lambda value:validate_evidence_decision(
            value,evidence,question,safe_history,visual_regions,answer_parts,
            complete_context=complete)
        return self._execute_evidence_decision(
            run=run,task=task,task_family=task_family,request_hash=request_hash,
            model_name=model_name,route=route,payload=payload,thinking=thinking,
            normalize=normalize,input_receipt=input_receipt,
            verify_receipt_sources=verify_layout_receipt_sources,
            request_label=(f'QA V3 multimodal decision {round_index+1}' if images
                           else f'QA V3 decision {round_index+1}'),
            diagnostic_class='PROJECT_EVIDENCE_DECISION',
            terminal_contract_message=(
                'This QA V3 round already has a settled contract-rejected response; '
                'the request was not retried.'),
            terminal_identity_message=(
                'The settled QA V3 input commitment is inconsistent; no retry was started.'),
            contract_failure_message=(
                'The QA V3 response failed its bounded decision contract; the cost was recorded '
                'and the request was not retried.'),
            policy_failure_message=(
                'The provider did not honor the non-reasoning setting. The cost was recorded and no decision was published.'),
            missing_usage_message=(
                'The QA V3 response omitted usage. The request remains pending for billing review.'))


def mock_extract(evidence: dict) -> dict:
    """仅识别明确的合成演示标记。不能冒充真实施工语义分析。"""
    text=evidence['raw_text']; eid=evidence['evidence_id']; rows=[]
    for line in text.splitlines():
        if not line.startswith(('DEMO_MATERIAL|','DEMO_TEST|','DEMO_REPORT|')):continue
        fields=line.split('|')
        if len(fields)<4:continue
        category={'DEMO_MATERIAL':'MATERIAL','DEMO_TEST':'TEST','DEMO_REPORT':'REPORT'}[fields[0]]
        props=[]
        for part in fields[4:]:
            if '=' not in part:continue
            name,value=part.split('=',1)
            props.append({'name':name.strip(),'value':value.strip(),'unit':None,'evidence_ids':[eid]})
        rows.append({'candidate_key':'R'+str(len(rows)+1),'category':category,'subject':fields[1],
                     'action':'provide' if category=='MATERIAL' else 'perform','object':fields[2],
                     'properties':props,'condition':None if fields[3]=='-' else fields[3],
                     'exception':None,'parent_requirement_key':None,'option_group_key':None,
                     'option_relation':'NONE','evidence_ids':[eid],'context_evidence_ids':[], 'needs_context':False})
    return {'disposition':'CANDIDATES' if rows else 'NEEDS_CONTEXT','requirements':rows,
            'reason':'合成演示，不代表工程结论。' if rows else '模拟Provider不分析任意施工文件。请配置真实API；本片段尚未进行语义审查。'}


def mock_extract_many(evidences: list[dict]) -> dict:
    requirements=[]
    for evidence in evidences:
        requirements.extend(mock_extract(evidence)['requirements'])
    for index,requirement in enumerate(requirements,1):requirement['candidate_key']='R'+str(index)
    return {'disposition':'CANDIDATES' if requirements else 'NEEDS_CONTEXT','requirements':requirements,
            'reason':'Synthetic demo only; no construction conclusion.' if requirements else
                     'Mock mode does not analyze arbitrary construction content.'}
