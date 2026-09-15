"""唯一模型入口：短JSON、供应商最低推理、先预留、无静默重试/升级。"""
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
from contracts.runtime_rules import (EvidenceScope,evidence_references,validate_schema,quote_tokens,cache_key)

class ProviderPaused(DomainError):pass
class InvalidModelOutput(DomainError):pass

_REQUEST_ID_HEADERS = ('x-request-id', 'x-goog-request-id')
_SAFE_REQUEST_ID = re.compile(r'^[A-Za-z0-9._:/=+-]{1,160}$')
_SAFE_RETRY_AFTER = re.compile(
    r'^(?:[0-9]{1,10}|[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT)$')
_SAFE_PROVIDER_CODE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
_PROVIDER_ERROR_CODES = frozenset({
    'INVALID_ARGUMENT', 'FAILED_PRECONDITION', 'UNAUTHENTICATED', 'PERMISSION_DENIED',
    'NOT_FOUND', 'RESOURCE_EXHAUSTED', 'CANCELLED', 'INTERNAL', 'UNAVAILABLE',
    'DEADLINE_EXCEEDED', 'INVALID_REQUEST', 'AUTHENTICATION', 'MODEL_NOT_FOUND',
    'RATE_LIMIT_EXCEEDED', 'QUOTA_EXCEEDED', 'TOO_MANY_REQUESTS', 'API_ERROR',
    'SERVICE_UNAVAILABLE', 'INVALID_REQUEST_ERROR', 'AUTHENTICATION_ERROR',
    'PERMISSION_ERROR', 'RATE_LIMIT_ERROR', 'SERVER_ERROR', 'INSUFFICIENT_QUOTA',
})
_VISION_PAGE_TYPES = frozenset({'DRAWING','SCHEDULE','SPEC_PAGE','PHOTO','DIAGRAM','FORM','OTHER','UNKNOWN'})
_VISION_FIELDS = frozenset({'page_type','sheet_id','important_visible_text','observations',
                            'explicit_quantity_texts','scale_text','limitations','needs_review'})
_EXTRACTION_INPUT_BYTE_CAP = 32000
_VISION_INPUT_BYTE_CAP = 6000
_VERIFICATION_INPUT_BYTE_CAP = 6000
_EXTRACTION_OUTPUT_TOKEN_CAP = 8000
_VISION_OUTPUT_TOKEN_CAP = 2000
_VERIFICATION_OUTPUT_TOKEN_CAP = 1400
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

@dataclass
class ModelResult:
    data: dict
    request_id: str | None
    cached: bool = False
    mode: str = 'mock'


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


def normalize_extraction_contract(data: dict, evidence_id: str) -> tuple[dict,list[str]]:
    """Salvage only contract-safe structure and make every repair review-visible.

    The normalizer never invents a requirement, source reference, property, or
    option. Invalid atomic requirements/properties are discarded instead of
    guessed. Purely representational scalar deviations are preserved as their
    exact string form. Any repair changes the disposition to ``TRUNCATED`` so
    the runner cannot publish the result as fully extracted without review.
    """
    if not isinstance(data,dict):raise ValueError('extraction object required')
    flags=[]
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
            if item==evidence_id and item not in out:out.append(item)
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


def expand_schema(schema: Any) -> Any:
    """本地引用内联供API理解；不是把整套12个Schema发送给模型。"""
    if isinstance(schema,list):return [expand_schema(v) for v in schema]
    if not isinstance(schema,dict):return schema
    if '$ref' in schema:
        path,*fragment=schema['$ref'].split('#')
        obj=json.loads((ROOT/'spec/schemas'/path.rsplit('/',1)[-1]).read_text(encoding="utf-8"))
        if fragment and fragment[0]:
            for key in fragment[0].strip('/').split('/'):obj=obj[key]
        return expand_schema(obj)
    return {k:expand_schema(v) for k,v in schema.items() if k not in ('$id','$schema','title')}

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
        self._request_slot_lock=threading.Lock();self._last_request_started=0.0

    def close(self):self.client.close()

    def payload(self, messages: list[dict], limit: int, model: str | None = None) -> dict:
        payload = {'model': model or self.s.cheap_model, 'response_format': {'type': 'json_object'},
                   'max_tokens': limit, 'stream': False, 'messages': messages}
        payload.update(self.s.inference_parameters())
        return payload

    def _wait_for_request_slot(self) -> None:
        interval=self.s.min_request_interval_seconds
        if interval<=0:return
        with self._request_slot_lock:
            delay=max(0.0,self._last_request_started+interval-time.monotonic())
            if delay:time.sleep(delay)
            self._last_request_started=time.monotonic()

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
        if provider_code: diagnostic['provider_code'] = provider_code
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
        retry = f"；Retry-After={diagnostic['retry_after']}" if diagnostic.get('retry_after') else ''
        return f'{purpose}{label}（HTTP {status}）{retry}；费用保持预留，需对账，不自动重试。'

    def _request_json(self, attempt: str, payload: dict, purpose: str) -> dict:
        response = None
        try:
            headers={'Authorization': 'Bearer ' + self.s.api_key} if self.s.api_key else {}
            response = self.client.post(self.s.api_base_url + '/chat/completions', json=payload,
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
            return body
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

    def rejects_reasoning(self, body: dict, usage: dict) -> bool:
        if self.s.provider != 'deepseek':
            return False
        message = (body.get('choices') or [{}])[0].get('message', {})
        details = usage.get('completion_tokens_details') or {}
        return bool(message.get('reasoning_content') or details.get('reasoning_tokens', 0))

    def validate(self, data: dict, evidence: dict):
        validate_schema('extraction-result',data)
        allowed={evidence['evidence_id']}
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
        exception=type(exc).__name__
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
        return diagnostic

    def _recover_terminal(self, run_id: str, task_key: str, validator) -> ModelResult | None:
        """Recover a billed task after a process crash without another HTTP call.

        Successful terminal responses are revalidated and returned even if the
        ordinary cache has expired.  Failed or legacy response-less settlements
        are terminal: a caller must create an explicitly versioned task before
        another paid request is permitted.
        """
        row=self.db.one('''SELECT id,state,response,error FROM model_calls
                           WHERE run_id=? AND task_key=? AND actual_units IS NOT NULL
                           ORDER BY created_at DESC LIMIT 1''',(run_id,task_key),False)
        if row is None:return None
        if row['response'] is not None:
            try:
                data=json.loads(row['response'])
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
        rows=self.db.all('''SELECT id,task_key,state,actual_units,response FROM model_calls
                            WHERE run_id=? ORDER BY created_at,id''',(run_id,))
        return [row for row in rows if paid_task_family(row['task_key'])==task_family]

    @staticmethod
    def _guard_family_before_request(recent: list[dict], task_key: str,
                                     generation: int, label: str) -> None:
        if any(row['actual_units'] is None for row in recent):
            raise ProviderPaused(f'{label}请求待对账；避免自动重复收费',409)
        # Handles legacy verification :job: keys and any corrupted/mismatched
        # task state which exact-key recovery cannot see.
        response_less=[row for row in recent if row['actual_units'] is not None and row['response'] is None]
        if generation==0 and response_less and all(row['task_key']!=task_key for row in response_less):
            raise ProviderPaused(f'{label}已有对账或已结算但无可发布结果的旧调用；需显式恢复代次。',409)
        if len(recent)>=MAX_PAID_TASK_CALLS:
            raise ProviderPaused(f'{label}任务族已达到三次累计调用上限；保留旧记录且不再收费',409)

    def extract(self, run: dict, evidence: dict) -> ModelResult:
        if self.s.provider=='mock':
            data=mock_extract(evidence)
            self.validate(data,evidence)
            return ModelResult(data,None)
        blockers=self.s.live_errors()
        if blockers:raise ProviderPaused('；'.join(blockers),409)
        if run.get('provider') != self.s.provider:
            raise ProviderPaused('运行Provider与当前服务不一致；为避免误收费，请新建分析。',409)
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL',(run['project_id'],),False):
            raise ProviderPaused('项目存在待对账请求；禁止新建或重复付费分析',409)
        task_family=evidence['evidence_id']
        generation=self._generation(run,task_family)
        task=paid_task_key(task_family,generation)
        recent=self._family_calls(run['id'],task_family)
        if any(x['actual_units'] is None for x in recent):raise ProviderPaused('存在待对账请求；避免自动重复收费',409)
        recovered=self._recover_terminal(run['id'],task,lambda value:self.validate(value,evidence))
        if recovered is not None:return recovered
        self._guard_family_before_request(recent,task,generation,'该片段')
        # The paid task key may include an internal manual-requeue generation.
        # The model and evidence-scope validator must always see the immutable
        # source evidence ID, never that billing-only suffix.
        content={'evidence_id':evidence['evidence_id'],'text':evidence['raw_text'],
                 'internal_revision_date':evidence['internal_revision_date'],
                 'locator':evidence['locator']}
        limit=min(_EXTRACTION_OUTPUT_TOKEN_CAP,self.s.output_limit)
        payload=self.payload(
            [{'role':'system','content':self.prompt+'\nJSON Schema:\n'+dumps(self.schema)},
             {'role':'user','content':dumps(content)}],limit)
        # UTF-8字节数作为保守工程估计，非精确token计数；实际usage超预留会冻结预算。
        upper_input=sum(len(x['content'].encode('utf-8')) for x in payload['messages'])+256
        if upper_input>min(_EXTRACTION_INPUT_BYTE_CAP,self.s.input_limit):
            raise InvalidModelOutput('片段加Schema超出简单任务输入预算；需细分，未调用API')
        key=cache_key({'tenant_id':'local','project_id':run['project_id'],'input_snapshot_id':run['snapshot_id'],
                      'input_hash':hashlib.sha256(dumps(content).encode()).hexdigest(),'crop_hashes':[],
                      'parser_version':evidence.get('parser_version','text-baseline-1'),'prompt_version':self.prompt_hash,
                      'provider':self.s.api_base_url,'model_id':self.s.cheap_model,'model_snapshot':None,
                      'schema_version':'0.2.0','retrieval_version':'none-v1','assembly_rule_version':'disabled-v1',
                      'revision_policy_version':'0.2.0','routing_version':'0.2.0',
                      'parameters':{**self.s.inference_parameters(),
                                    'max_tokens':limit,'result_normalizer':'extraction-contract-v2'},
                      **({'manual_requeue_generation':generation} if generation else {})})
        cached=self.db.cached(key,run['project_id'])
        if cached:
            self.validate(cached,evidence)
            return ModelResult(cached,None,True,self.s.provider)
        amount=quote_tokens(upper_input,limit,self.s.input_rate,self.s.output_rate)
        self._wait_for_request_slot()
        attempt=self.db.reserve(run['project_id'],run['id'],task,amount,self.s.cheap_model,
                                hashlib.sha256(dumps(payload).encode()).hexdigest(),self.s.input_rate,self.s.output_rate,
                                allow_zero=self.s.is_local_model())
        body=self._request_json(attempt,payload,'提取')
        try:
            usage=self.billed_usage(body)
        except ValueError:
            self.db.unknown(attempt,dumps({'kind':'INVALID_RESPONSE','class':'MISSING_USAGE'}))
            raise ProviderPaused('响应缺少usage；保持预留并暂停，需核对账单。',409)
        actual=quote_tokens(usage['prompt_tokens'],usage['completion_tokens'],self.s.input_rate,self.s.output_rate,safety=Decimal('1'))
        provider_id=self._safe_provider_id(body.get('id'))
        # DeepSeek忽略非思考请求时保留账务并停止；Gemini 3最低推理属于预期行为。
        if self.rejects_reasoning(body, usage):
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('REASONING_NOT_DISABLED',kind='POLICY_ERROR'))
            raise ProviderPaused('供应商未遵守非思考设置；本次费用已记录，需确认接口兼容性。', 409)
        # 缓存命中部分按已确认输入上限费率记账；不假造未知供应商折扣。
        try:
            choice=body['choices'][0]
            if choice.get('finish_reason')!='stop':raise ValueError('响应截断或非正常完成')
            text=choice['message']['content']
            if not isinstance(text,str) or len(text)>100_000:raise ValueError('空响应或响应过大')
            data,_wrapper_flags=normalize_extraction_result(text)
            data,_contract_flags=normalize_extraction_contract(data,evidence['evidence_id'])
            self.validate(data,evidence)
        except Exception as exc:
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('EXTRACTION_RESULT',exc))
            raise InvalidModelOutput('模型结果未通过契约；已记录本次费用，不自动重复请求。') from exc
        self.db.finalize_model_call(attempt,actual,usage,provider_id,response=data,cache_key=key)
        return ModelResult(data,attempt,False,self.s.provider)

    def vision(self, run: dict, task_key: str, image_png: bytes, metadata: dict,
               *, billing_generation: int = 0) -> ModelResult:
        """Analyze one bounded page image through the only paid gateway and budget ledger."""
        if self.s.provider != 'deepseek' or not self.s.vision_enabled:
            raise ProviderPaused('DeepSeek视觉路由未启用；未发送图像。',409)
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
        safe_metadata={k:metadata[k] for k in ('document_id','page','coordinate_system','width','height') if k in metadata}
        instruction=self.vision_prompt+'\nJSON Schema:\n'+dumps(self.vision_schema)+'\nPage metadata:\n'+dumps(safe_metadata)
        data_url='data:image/png;base64,'+base64.b64encode(image_png).decode('ascii')
        # The 59-page live drawing proved that 1,200 output tokens truncated a
        # repeatable subset of dense sheets.  Keep the normal application cap
        # (currently 2,000) while preserving the same prepaid budget guard.
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
        amount=quote_tokens(upper_input,limit,self.s.input_rate,self.s.output_rate)
        self._wait_for_request_slot()
        request_hash=hashlib.sha256(dumps([self.s.vision_model,self.vision_prompt_hash,image_hash,safe_metadata,limit]).encode()).hexdigest()
        attempt=self.db.reserve(run['project_id'],run['id'],task,amount,self.s.vision_model,
                                request_hash,self.s.input_rate,self.s.output_rate)
        body=self._request_json(attempt,payload,'视觉')
        try:
            usage=self.billed_usage(body)
        except ValueError:
            self.db.unknown(attempt,dumps({'kind':'INVALID_RESPONSE','class':'MISSING_USAGE'}))
            raise ProviderPaused('视觉响应缺少usage；费用保持预留并暂停，需对账。',409)
        actual=quote_tokens(usage['prompt_tokens'],usage['completion_tokens'],self.s.input_rate,self.s.output_rate,
                            safety=Decimal('1'))
        provider_id=self._safe_provider_id(body.get('id'))
        if self.rejects_reasoning(body,usage):
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

        Uses the same persistent CNY budget, no automatic paid retries or model upgrades.
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
        payload = self.payload(
            [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': dumps(content)}], limit)
        upper = sum(len(m['content'].encode('utf-8')) for m in payload['messages']) + 256
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
                                if row['actual_units'] is not None and row['response'] is not None),None)
            return ModelResult(cached, original_call['id'] if original_call else None, True, self.s.provider)
        recent=self._family_calls(run['id'],task_family)
        self._guard_family_before_request(recent,task,generation,'该核验')
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL', (run['project_id'],), False):
            raise ProviderPaused('项目有未对账调用，暂停核验', 409)
        amount = quote_tokens(upper, limit, self.s.input_rate, self.s.output_rate)
        self._wait_for_request_slot()
        attempt = self.db.reserve(run['project_id'], run['id'], task, amount, self.s.cheap_model,
                                  hashlib.sha256(dumps(payload).encode()).hexdigest(), self.s.input_rate,
                                  self.s.output_rate, verification_job_id=job_id,
                                  allow_zero=self.s.is_local_model())
        body = self._request_json(attempt, payload, '核验')
        try:
            usage = self.billed_usage(body)
        except ValueError:
            self.db.unknown(attempt,dumps({'kind':'INVALID_RESPONSE','class':'MISSING_USAGE'}))
            raise ProviderPaused('核验响应缺少usage，费用保持预留', 409)
        actual = quote_tokens(usage['prompt_tokens'], usage['completion_tokens'], self.s.input_rate, self.s.output_rate, safety=Decimal('1'))
        provider_id=self._safe_provider_id(body.get('id'))
        choice = (body.get('choices') or [{}])[0]; message = choice.get('message', {})
        if self.rejects_reasoning(body, usage):
            self.db.finalize_model_call(attempt,actual,usage,provider_id,
                diagnostic=self._terminal_diagnostic('REASONING_NOT_DISABLED',kind='POLICY_ERROR'))
            raise ProviderPaused('核验接口未遵守非思考配置，已记账并暂停', 409)
        try:
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
