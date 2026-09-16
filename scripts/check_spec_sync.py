"""离线结构治理；不宣称自然语言语义完全一致或人类已批准。"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jsonschema import Draft202012Validator
from contracts.runtime_rules import validate_schema
from scripts.render_requirements import render

def check(root: Path = ROOT) -> list[str]:
    errors=[]
    def load(p):
        return json.loads((root/p).read_text(encoding='utf-8'))
    version=(root/'VERSION').read_text(encoding="utf-8").strip()
    req=load('spec/requirements.json');trace=load('spec/traceability.json')
    if req['spec_version']!=version or trace['spec_version']!=version:errors.append('版本不一致')
    spec=(root/'docs/PRODUCT_SPEC.md').read_text(encoding='utf-8')
    if f'**规格版本：** {version}' not in spec:errors.append('产品规格版本不一致')
    items=req['requirements']; ids=[r['id'] for r in items]
    if len(set(ids))!=len(ids):errors.append('重复需求ID')
    old_ids={r['id'] for r in load('baseline/requirements.v0.1.0.json')['requirements']}
    if old_ids-set(ids):errors.append('丢失历史需求ID')
    statuses={'planned','implemented','deferred','superseded'}
    for r in items:
        if r['status'] not in statuses:errors.append('非法需求状态: '+r['id'])
        if not r.get('acceptance_criteria'):errors.append('缺少验收: '+r['id'])
        if r['status']=='superseded' and not set(r.get('superseded_by',[])).issubset(ids):errors.append('无效替代ID')
    entries=trace['entries'];tids=[e['requirement_id'] for e in entries]
    if len(set(tids))!=len(tids) or set(tids)!=set(ids):errors.append('Traceability非完整一对一')
    byid={r['id']:r for r in items}
    for e in entries:
        if e['implementation_status']!=byid[e['requirement_id']]['status']:errors.append('实现状态不同步')
        for p in e['contract_tests']:
            if not (root/p).is_file():errors.append('契约测试缺失: '+p)
        if e['implementation_status']=='implemented':
            if not e['implemented_product_paths'] or not e['planned_product_tests'] or not e['verification_report']:
                errors.append('implemented缺产品代码/测试/报告: '+e['requirement_id'])
            for p in e['implemented_product_paths']+e['planned_product_tests']+([e['verification_report']] if e['verification_report'] else []):
                if not (root/p).exists():errors.append('实现路径不存在: '+p)
    for p in (root/'spec/schemas').glob('*.json'):
        try:Draft202012Validator.check_schema(json.loads(p.read_text(encoding='utf-8')))
        except Exception as exc:errors.append(f'Schema无效 {p.name}: {exc}')
    pm=load('prompts/manifest.json')
    if pm['bundle_version']!=version:errors.append('Prompt版本不一致')
    for p in pm['prompts']:
        if not (root/p['path']).is_file() or not (root/p['schema']).is_file():errors.append('Prompt正文或Schema不存在: '+p['id'])
        elif len((root/p['path']).read_text(encoding='utf-8').strip())<50:errors.append('Prompt是空占位: '+p['id'])
    generated=(root/'docs/REQUIREMENTS.md').read_text(encoding='utf-8')
    if generated!=render(req):errors.append('自动需求表未同步')
    routing=load('config/model_routing.json');budget=load('config/budget_policy.json')
    if routing['roles']['cheap']['thinking']!='disabled':errors.append('简单任务未关闭思考')
    if routing['roles']['high']['enabled']:errors.append('本基线高价模型不得默认启用')
    if (budget['per_project_limit']!='300.00' or budget['currency']!='CNY'
            or not budget.get('user_configurable')):
        errors.append('项目预算默认值或用户可配置策略不一致')
    if root == ROOT:
        for path in (root/'changes').glob('*.json'):
            try:validate_schema('change-record',json.loads(path.read_text(encoding='utf-8')))
            except Exception as exc:errors.append(f'变更记录无效: {path.name}: {exc}')
    return errors

if __name__=='__main__':
    errs=check()
    for e in errs:print('[FAIL]',e)
    if errs:raise SystemExit(1)
    req=json.loads((ROOT/'spec/requirements.json').read_text(encoding='utf-8'))
    print('[PASS] 规格结构、Schema定义、Prompt正文、生成表、追踪与政策初值')
    print('[INFO] requirements=',len(req['requirements']),'schemas=',len(list((ROOT/'spec/schemas').glob('*.json'))))
    print('[INFO] 当前为本地原型第一切片；本检查不是施工准确率或远端审批证明。')
