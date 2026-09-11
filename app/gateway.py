"""唯一模型入口：短JSON、非思考、先预留、无静默重试/升级。"""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
import httpx
from app.db import Database, DomainError, dumps
from app.settings import Settings, ROOT
from contracts.runtime_rules import (EvidenceScope,evidence_references,validate_schema,quote_tokens,cache_key)

class ProviderPaused(DomainError):pass
class InvalidModelOutput(DomainError):pass

@dataclass
class ModelResult:
    data: dict
    request_id: str | None
    cached: bool = False
    mode: str = 'mock'


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

    def close(self):self.client.close()

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

    def extract(self, run: dict, evidence: dict) -> ModelResult:
        if self.s.provider=='mock':
            data=mock_extract(evidence)
            self.validate(data,evidence)
            return ModelResult(data,None)
        blockers=self.s.live_errors()
        if blockers:raise ProviderPaused('；'.join(blockers),409)
        task=evidence['evidence_id']
        recent=self.db.all('SELECT state,actual_units FROM model_calls WHERE run_id=? AND task_key=?',(run['id'],task))
        if any(x['actual_units'] is None for x in recent):raise ProviderPaused('存在待对账请求；避免自动重复收费',409)
        if len(recent)>=3:raise ProviderPaused('该片段已达到累计调用上限',409)
        content={'evidence_id':task,'text':evidence['raw_text'],
                 'internal_revision_date':evidence['internal_revision_date'],
                 'locator':evidence['locator']}
        payload={'model':self.s.cheap_model,'thinking':{'type':'disabled'},
                 'response_format':{'type':'json_object'},'max_tokens':self.s.output_limit,'stream':False,
                 'messages':[{'role':'system','content':self.prompt+'\nJSON Schema:\n'+dumps(self.schema)},
                             {'role':'user','content':dumps(content)}]}
        # UTF-8字节数作为保守工程估计，非精确token计数；实际usage超预留会冻结预算。
        upper_input=sum(len(x['content'].encode('utf-8')) for x in payload['messages'])+256
        if upper_input>self.s.input_limit:
            raise InvalidModelOutput('片段加Schema超出简单任务输入预算；需细分，未调用API')
        key=cache_key({'tenant_id':'local','project_id':run['project_id'],'input_snapshot_id':run['snapshot_id'],
                      'input_hash':hashlib.sha256(dumps(content).encode()).hexdigest(),'crop_hashes':[],
                      'parser_version':'text-baseline-1','prompt_version':self.prompt_hash,
                      'provider':self.s.api_base_url,'model_id':self.s.cheap_model,'model_snapshot':None,
                      'schema_version':'0.2.0','retrieval_version':'none-v1','assembly_rule_version':'disabled-v1',
                      'revision_policy_version':'0.2.0','routing_version':'0.2.0','parameters':{'thinking':'disabled','max_tokens':self.s.output_limit}})
        cached=self.db.cached(key,run['project_id'])
        if cached:
            self.validate(cached,evidence)
            return ModelResult(cached,None,True,'deepseek')
        amount=quote_tokens(upper_input,self.s.output_limit,self.s.input_rate,self.s.output_rate)
        attempt=self.db.reserve(run['project_id'],run['id'],task,amount,self.s.cheap_model,
                                hashlib.sha256(dumps(payload).encode()).hexdigest(),self.s.input_rate,self.s.output_rate)
        try:
            response=self.client.post(self.s.api_base_url+'/chat/completions',json=payload,
                                      headers={'Authorization':'Bearer '+self.s.api_key})
            response.raise_for_status()
            body=response.json()
        except Exception as exc:
            self.db.unknown(attempt,'网络或HTTP响应未能完成对账：'+type(exc).__name__)
            raise ProviderPaused('API请求未完成；费用保持预留，暂停且不自动重试。',409) from exc
        usage=body.get('usage')
        if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in ('prompt_tokens','completion_tokens')):
            self.db.unknown(attempt,'响应缺少可信usage')
            raise ProviderPaused('响应缺少usage；保持预留并暂停，需核对账单。',409)
        actual=quote_tokens(usage['prompt_tokens'],usage['completion_tokens'],self.s.input_rate,self.s.output_rate,safety=Decimal('1'))
        self.db.settle(attempt,actual,usage,body.get('id'),None)
        # 供应商忽略非思考请求时保留账务并停止。
        message = (body.get('choices') or [{}])[0].get('message', {})
        details = usage.get('completion_tokens_details') or {}
        if message.get('reasoning_content') or details.get('reasoning_tokens', 0):
            raise ProviderPaused('供应商未遵守非思考设置；本次费用已记录，需确认接口兼容性。', 409)
        # 缓存命中部分按已确认输入上限费率记账；不假造未知供应商折扣。
        try:
            choice=body['choices'][0]
            if choice.get('finish_reason')!='stop':raise ValueError('响应截断或非正常完成')
            text=choice['message']['content']
            if not isinstance(text,str) or len(text)>100_000:raise ValueError('空响应或响应过大')
            data=json.loads(text);self.validate(data,evidence)
        except Exception as exc:
            raise InvalidModelOutput('模型结果未通过契约；已记录本次费用，不自动重复请求。') from exc
        self.db.execute('UPDATE model_calls SET response=? WHERE id=?',(dumps(data),attempt))
        self.db.cache_put(key,run['project_id'],data)
        return ModelResult(data,attempt,False,'deepseek')


    def verify_claims(self, run: dict, fields: list[dict], evidence: dict, job_id=None) -> ModelResult:
        """Low-cost independent verification. All HTTP remains in this gateway.

        Uses the same persistent CNY budget, no automatic paid retries or model upgrades.
        """
        from app.verification import VerificationService
        if self.s.provider == 'mock':
            raise ProviderPaused('模拟模式不执行语义核验', 409)
        if self.s.live_errors():
            raise ProviderPaused('；'.join(self.s.live_errors()), 409)
        prompt = (ROOT / 'prompts/claim-verification/system.md').read_text(encoding='utf-8')
        content = {'fields': [{k: f[k] for k in ('path', 'claim', 'label', 'evidence_ids', 'context')} for f in fields],
                   'evidence': [{'evidence_id': eid, 'text': e['raw_text'], 'revision_date': e.get('internal_revision_date'),
                                 'locator': e['locator']} for eid, e in sorted(evidence.items())]}
        limit = min(1400, self.s.output_limit)
        payload = {'model': self.s.cheap_model, 'thinking': {'type': 'disabled'}, 'max_tokens': limit,
                   'stream': False, 'response_format': {'type': 'json_object'},
                   'messages': [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': dumps(content)}]}
        upper = sum(len(m['content'].encode('utf-8')) for m in payload['messages']) + 256
        if upper > self.s.input_limit:
            raise InvalidModelOutput('核验证据超过单次输入限额，不能截掉上下文后标记通过')
        cache_id = hashlib.sha256(dumps(['verify-v1',run['project_id'],run['snapshot_id'],self.s.api_base_url,
                    self.s.cheap_model, payload, [e.get('file_sha256') for _,e in sorted(evidence.items())]]).encode()).hexdigest()
        cached = self.db.cached(cache_id, run['project_id'])
        if cached is not None:
            from copy import deepcopy
            VerificationService.apply_model(deepcopy(fields), cached, evidence, None)
            original_call=self.db.one('SELECT id FROM model_calls WHERE project_id=? AND task_key=? AND actual_units IS NOT NULL ORDER BY created_at DESC LIMIT 1',(run['project_id'],'verify:'+cache_id),False)
            return ModelResult(cached, original_call['id'] if original_call else None, True, 'deepseek')
        task = 'verify:' + cache_id
        recent = self.db.all('SELECT actual_units FROM model_calls WHERE run_id=? AND task_key=?', (run['id'], task))
        if any(r['actual_units'] is None for r in recent):
            raise ProviderPaused('核验请求待对账，不重复付费', 409)
        if len(recent) >= 3:
            raise ProviderPaused('该核验任务达到累计调用上限', 409)
        if self.db.one('SELECT id FROM model_calls WHERE project_id=? AND actual_units IS NULL', (run['project_id'],), False):
            raise ProviderPaused('项目有未对账调用，暂停核验', 409)
        amount = quote_tokens(upper, limit, self.s.input_rate, self.s.output_rate)
        attempt = self.db.reserve(run['project_id'], run['id'], task, amount, self.s.cheap_model,
                                  hashlib.sha256(dumps(payload).encode()).hexdigest(), self.s.input_rate,
                                  self.s.output_rate, verification_job_id=job_id)
        try:
            response = self.client.post(self.s.api_base_url + '/chat/completions', json=payload,
                                        headers={'Authorization': 'Bearer ' + self.s.api_key})
            response.raise_for_status(); body = response.json()
        except Exception as exc:
            self.db.unknown(attempt, '核验调用未完成对账：' + type(exc).__name__)
            raise ProviderPaused('核验API异常，保持费用预留，不自动重试', 409) from exc
        usage = body.get('usage')
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('prompt_tokens','completion_tokens')):
            self.db.unknown(attempt, '核验响应缺少可信usage')
            raise ProviderPaused('核验响应缺少usage，费用保持预留', 409)
        actual = quote_tokens(usage['prompt_tokens'], usage['completion_tokens'], self.s.input_rate, self.s.output_rate, safety=Decimal('1'))
        self.db.settle(attempt, actual, usage, body.get('id'), None)
        choice = (body.get('choices') or [{}])[0]; message = choice.get('message', {})
        if message.get('reasoning_content') or (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0):
            raise ProviderPaused('核验接口未遵守非思考配置，已记账并暂停', 409)
        try:
            if choice.get('finish_reason') != 'stop': raise ValueError('核验输出截断')
            text = message['content']
            if not isinstance(text, str) or len(text) > 50000: raise ValueError('核验响应过长')
            data = json.loads(text)
            from copy import deepcopy
            VerificationService.apply_model(deepcopy(fields), data, evidence, attempt)
        except Exception as exc:
            raise InvalidModelOutput('核验响应未通过格式、范围或逐字引用检查，已记账') from exc
        self.db.execute('UPDATE model_calls SET response=? WHERE id=?', (dumps(data), attempt))
        self.db.cache_put(cache_id, run['project_id'], data)
        return ModelResult(data, attempt, False, 'deepseek')


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
