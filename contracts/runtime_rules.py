"""离线策略参考：没有HTTP请求、数据库写入或生产服务。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
MONEY = Decimal('0.000001')


def load_json(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def registry() -> Registry:
    result = Registry()
    for path in sorted((ROOT / 'spec/schemas').glob('*.json')):
        schema = json.loads(path.read_text(encoding='utf-8'))
        result = result.with_resource(schema['$id'], Resource.from_contents(schema))
    return result


def validate_schema(name: str, value: Any) -> None:
    schema = load_json(f'spec/schemas/{name}.schema.json')
    Draft202012Validator(schema, registry=registry(), format_checker=FormatChecker()).validate(value)


def evidence_references(value: Any) -> set[str]:
    """遍历结构化引用，不把任意自由文本当证据ID。"""
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith('evidence_ids') and isinstance(child, list):
                refs.update(child)
            elif key in {'evidence_id', 'selected_evidence_id'} and isinstance(child, str):
                refs.add(child)
            elif key == 'field_evidence' and isinstance(child, dict):
                for ids in child.values():
                    refs.update(ids)
            else:
                refs.update(evidence_references(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(evidence_references(child))
    return refs


@dataclass(frozen=True)
class EvidenceScope:
    tenant_id: str
    project_id: str
    input_snapshot_id: str
    records: Mapping[str, Mapping[str, Any]]

    def check(self, ids: Iterable[str]) -> None:
        for eid in ids:
            if eid not in self.records:
                raise ValueError(f'不存在的证据: {eid}')
            record = self.records[eid]
            for name in ('tenant_id', 'project_id', 'input_snapshot_id'):
                if record.get(name) != getattr(self, name):
                    raise ValueError(f'证据范围不匹配: {eid}/{name}')


def validate_candidate(name: str, value: dict[str, Any], scope: EvidenceScope) -> None:
    validate_schema(name, value)
    scope.check(evidence_references(value))
    if name == 'material-item':
        option_ids = [o['option_id'] for o in value['options']]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError('选项ID重复')
        if not set(value['selected_option_ids']).issubset(option_ids):
            raise ValueError('选中项不存在于选项组')
        for option in value['options']:
            for field in ('manufacturer', 'product', 'model_number'):
                if option[field] is not None and not option['field_evidence'].get(field):
                    raise ValueError(f'产品字段缺来源: {field}')
        for prop in value['design_properties']:
            if not prop['evidence_ids']:
                raise ValueError('具体设计属性缺来源；推导候选也不能虚构尺寸或等级')
        for selected in value['selected_option_ids']:
            selected_option = next(o for o in value['options'] if o['option_id'] == selected)
            if selected_option['role'] != 'DOCUMENT_SELECTED':
                raise ValueError('没有文件选型依据，不能选择产品')
    if name == 'inspection-item':
        for field in ('frequency', 'acceptance_criteria', 'standard_reference', 'submission_trigger'):
            if value[field] is not None and not value['field_evidence'].get(field):
                raise ValueError(f'检查字段缺来源: {field}')
    if name == 'conflict-item':
        for claim in value['claims']:
            if claim['internal_revision_date'] != scope.records[claim['evidence_id']].get('internal_revision_date'):
                raise ValueError('模型日期与源证据日期不符')
        if any(c['scope_key'] != value['comparison_key'] for c in value['claims']):
            raise ValueError('冲突不是同一比较范围')
        if value['resolution_status'] == 'LATEST_APPLIED':
            decision = select_latest([
                RevisionClaim(c['evidence_id'], c['scope_key'], c['normalized_value'], c['internal_revision_date'])
                for c in value['claims']
            ])
            selected = value['selected_evidence_id']
            if decision.status != 'LATEST_APPLIED' or selected not in decision.selected_evidence_ids:
                raise ValueError('当前采用值不符合内部日期规则')


def wrap_candidate(kind: str, candidate: dict[str, Any], meta: dict[str, Any], scope: EvidenceScope) -> dict[str, Any]:
    names = {'MATERIAL': 'material-item', 'INSPECTION': 'inspection-item', 'CONFLICT': 'conflict-item', 'MISSING': 'missing-information-item'}
    validate_candidate(names[kind], candidate, scope)
    for field in ('tenant_id', 'project_id', 'input_snapshot_id'):
        if meta[field] != getattr(scope, field):
            raise ValueError('服务端运行与证据范围不同')
    record = {
        'meta': meta, 'kind': kind, 'candidate': candidate,
        'review': {'status': 'PENDING', 'event_id': None, 'actor_id': None},
        'quantity_review': 'PENDING' if kind == 'MATERIAL' and candidate.get('quantity') is not None else 'NOT_APPLICABLE',
    }
    validate_schema('record-envelope', record)
    return record


@dataclass(frozen=True)
class RevisionClaim:
    evidence_id: str
    scope_key: str
    value: str
    internal_revision_date: str | None
    upload_time: str | None = None
    revision_sequence: int | None = None
    document_family: str | None = None
    asserted: bool = True


@dataclass(frozen=True)
class RevisionDecision:
    status: str
    value: str | None
    selected_evidence_ids: tuple[str, ...]
    reason: str


def select_latest(claims: list[RevisionClaim]) -> RevisionDecision:
    relevant = [c for c in claims if c.asserted]
    unresolved = lambda reason: RevisionDecision('UNRESOLVED', None, (), reason)
    if not relevant:
        return unresolved('没有正式要求断言')
    if len({c.scope_key for c in relevant}) != 1:
        return unresolved('不同适用范围，不得按新旧覆盖')
    try:
        dates = [(date.fromisoformat(c.internal_revision_date), c) for c in relevant if c.internal_revision_date]
    except ValueError:
        return unresolved('内部日期不可解析')
    if len(dates) != len(relevant):
        return unresolved('候选缺少内部日期')
    # 只有明确属于同文档链且提供标准化序列时才检查修订倒序。
    for da, a in dates:
        for db, b in dates:
            if (a.document_family and a.document_family == b.document_family
                and a.revision_sequence is not None and b.revision_sequence is not None
                and a.revision_sequence > b.revision_sequence and da < db):
                return unresolved('修订序列与内部日期相矛盾')
    newest = max(d for d, _ in dates)
    latest = [c for d, c in dates if d == newest]
    if len({c.value for c in latest}) != 1:
        return unresolved('同日最新内容矛盾')
    return RevisionDecision('LATEST_APPLIED', latest[0].value, tuple(c.evidence_id for c in latest), '按内部修订日期；上传时间未参与')


@dataclass(frozen=True)
class Route:
    role: str
    model: str | None
    thinking: str
    reason: str


def route_task(task: str, *, evidence_complete: bool = True, has_image: bool = False,
               vision_available: bool = False, ambiguous: bool = False, calls_used: int = 0,
               requested_high: bool = False, high_approved: bool = False) -> Route:
    cfg = load_json('config/model_routing.json')
    if task in cfg['programmatic_tasks']:
        return Route('programmatic', None, 'disabled', '确定性程序任务')
    if not evidence_complete:
        return Route('NEEDS_REVIEW', None, 'disabled', '先补证据，不用贵模型猜测')
    if calls_used >= cfg['max_total_model_calls_per_logical_task']:
        return Route('NEEDS_REVIEW', None, 'disabled', '达到逻辑任务调用上限')
    if has_image:
        if not vision_available:
            return Route('NEEDS_REVIEW', None, 'disabled', '视觉能力未验证')
        return Route('vision', cfg['roles']['vision']['default_model'], 'disabled', '需要真实图像能力')
    if requested_high:
        high = cfg['roles']['high']
        if not high_approved or not high['enabled'] or not high['default_model']:
            return Route('NEEDS_REVIEW', None, 'disabled', '高级模型未配置或未获批准')
        return Route('high', high['default_model'], 'enabled', '明确获批的升级')
    if ambiguous or task in cfg['reasoning_tasks']:
        return Route('reasoning', cfg['roles']['reasoning']['default_model'], 'enabled', '证据充足的关系判断')
    if task not in cfg['cheap_tasks']:
        return Route('NEEDS_REVIEW', None, 'disabled', '未知任务，禁止自由代理扩展')
    return Route('cheap', cfg['roles']['cheap']['default_model'], 'disabled', '最低成本足够角色')


def make_chat_payload(route: Route, system_prompt: str, user_content: Any, max_tokens: int = 2000) -> dict[str, Any]:
    """仅创建请求对象，不进行HTTP调用。"""
    if route.model is None or route.role not in {'cheap', 'reasoning', 'vision', 'high'}:
        raise ValueError('当前路由不应调用模型')
    cfg = load_json('config/model_routing.json')['roles'][route.role]
    if max_tokens <= 0 or max_tokens > cfg['max_output_tokens']:
        raise ValueError('输出token超过角色上限')
    if 'json' not in system_prompt.lower():
        raise ValueError('JSON模式需要明确json任务提示')
    payload = {'model': route.model, 'thinking': {'type': route.thinking},
               'messages': [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_content}],
               'response_format': {'type': 'json_object'}, 'max_tokens': max_tokens, 'stream': False}
    if route.thinking == 'enabled':
        payload['reasoning_effort'] = cfg.get('reasoning_effort', 'low')
    return payload


def quote_tokens(input_tokens: int, output_tokens_total: int, input_rate: Decimal, output_rate: Decimal,
                 *, safety: Decimal = Decimal('1.10')) -> Decimal:
    if min(input_tokens, output_tokens_total) < 0 or min(input_rate, output_rate) < 0 or safety < 1:
        raise ValueError('非法计费参数')
    amount = (Decimal(input_tokens) * input_rate + Decimal(output_tokens_total) * output_rate) / Decimal(1_000_000)
    return (amount * safety).quantize(MONEY, rounding=ROUND_CEILING)


class BudgetBlocked(RuntimeError):
    pass


class BudgetLedger:
    """单进程内存模型；生产必须替换为持久化事务预留，不跨进程保证。"""
    def __init__(self, limit: Decimal = Decimal('300')):
        if limit <= 0:
            raise ValueError('预算须大于零')
        self.limit = limit
        self.spent = Decimal('0')
        self.reservations: dict[str, Decimal] = {}
        self.settled: dict[str, Decimal] = {}
        self.frozen = False
        self._lock = RLock()

    @property
    def outstanding(self) -> Decimal:
        with self._lock:
            return sum(self.reservations.values(), Decimal('0'))

    def reserve(self, attempt_id: str, upper_bound: Decimal) -> None:
        if not attempt_id or upper_bound <= 0:
            raise ValueError('预留ID和费用必须有效')
        with self._lock:
            if attempt_id in self.settled:
                raise ValueError('已结算的请求ID不能重用')
            if attempt_id in self.reservations:
                if self.reservations[attempt_id] == upper_bound:
                    return  # 相同派发意图幂等；真正重试必须新的attempt ID。
                raise ValueError('同一预留ID金额发生变化')
            if self.frozen or self.spent + self.outstanding + upper_bound > self.limit:
                raise BudgetBlocked('停止新付费调用，保留未处理Coverage')
            self.reservations[attempt_id] = upper_bound

    def settle(self, attempt_id: str, actual: Decimal) -> None:
        if actual < 0:
            raise ValueError('实际费用不能为负')
        with self._lock:
            if attempt_id in self.settled:
                if self.settled[attempt_id] == actual:
                    return
                raise ValueError('重复结算金额不一致')
            if attempt_id not in self.reservations:
                raise ValueError('没有对应预留')
            expected = self.reservations.pop(attempt_id)
            self.spent += actual
            self.settled[attempt_id] = actual
            if actual > expected or self.spent + self.outstanding > self.limit:
                self.frozen = True  # 记录真实费用，不能通过拒记账隐藏超费。

    def mark_unknown(self, attempt_id: str) -> None:
        with self._lock:
            if attempt_id not in self.reservations:
                raise ValueError('不存在的待对账请求')
            # 未知计费保持全额预留，不释放。

    def release_unbilled(self, attempt_id: str, *, confirmed_unbilled: bool) -> None:
        if not confirmed_unbilled:
            raise ValueError('没有确定未计费依据')
        self.settle(attempt_id, Decimal('0'))


CACHE_REQUIRED = {'tenant_id', 'project_id', 'input_snapshot_id', 'input_hash', 'crop_hashes',
                  'parser_version', 'prompt_version', 'provider', 'model_id', 'model_snapshot',
                  'schema_version', 'retrieval_version', 'assembly_rule_version', 'revision_policy_version',
                  'routing_version', 'parameters'}


def cache_key(parts: Mapping[str, Any]) -> str:
    if CACHE_REQUIRED - set(parts):
        raise ValueError('缓存隔离/版本字段不足')
    serialized = json.dumps(dict(parts), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(serialized.encode()).hexdigest()


def sum_design_instances(rows: list[dict[str, Any]]) -> tuple[Decimal, str | None]:
    """示例：同一已识别安装实例的多次图面出现去重；不负责推断实例ID。"""
    units = {r['unit'] for r in rows}
    if len(units) > 1:
        raise ValueError('先做可靠单位换算，不能直接混合求和')
    by_id: dict[str, Decimal] = {}
    for row in rows:
        if not row.get('instance_id'):
            raise ValueError('缺少实物实例ID')
        value = Decimal(str(row['design_value']))
        if value < 0:
            raise ValueError('设计净量不能为负')
        if row['instance_id'] in by_id and by_id[row['instance_id']] != value:
            raise ValueError('同实例数量矛盾，不自动择一求和')
        by_id[row['instance_id']] = value
    return sum(by_id.values(), Decimal('0')), next(iter(units)) if units else None
