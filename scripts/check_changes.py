"""对Git实际diff检查变更清单；人类审批由仓库保护及审查负责。"""
from __future__ import annotations
import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from contracts.runtime_rules import validate_schema

WATCHED=('app/','web/','contracts/','prompts/','config/','spec/','.github/','AGENTS.md')
def check_diff(paths: list[str], records: list[dict], known_ids: set[str]) -> list[str]:
    errors=[]
    relevant=[p for p in paths if p.startswith(WATCHED)]
    if not relevant:return []
    if not records:return ['受控文件变化缺少新增/修改的changes记录']
    for record in records:
        if not set(record['requirement_ids']).issubset(known_ids):errors.append('变更引用未知需求ID')
        if record['change_type']=='spec_only' and any(p.startswith(('app/','web/')) for p in relevant):
            errors.append('产品代码改动不能伪报spec_only')
        if record['change_type'] in ('implementation','bugfix'):
            if not any(p.startswith('tests/') for p in paths):errors.append('实现改动缺少实际测试diff')
            if 'spec/traceability.json' not in paths:errors.append('实现改动缺少追踪更新')
        if record['change_type']=='implementation':
            if not any(p.startswith('docs/') for p in paths):errors.append('行为实现缺少说明更新')
    for p in relevant:
        if not any(fnmatch.fnmatchcase(p,pattern) for r in records for pattern in r['affected_paths']):
            errors.append('变更清单未覆盖: '+p)
    return errors

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',required=True);args=parser.parse_args()
    if not args.base or args.base.startswith('-'):raise SystemExit('无效base revision')
    subprocess.run(['git','rev-parse','--verify',args.base+'^{commit}'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    raw=subprocess.check_output(['git','diff','--name-only','-z',args.base,'HEAD','--'],cwd=ROOT)
    paths=[p for p in raw.decode().split('\0') if p]
    records=[]
    for p in paths:
        if p.startswith('changes/') and p.endswith('.json') and (ROOT/p).is_file():
            value=json.loads((ROOT/p).read_text(encoding='utf-8'));validate_schema('change-record',value);records.append(value)
    ids={r['id'] for r in json.loads((ROOT/'spec/requirements.json').read_text(encoding='utf-8'))['requirements']}
    errors=check_diff(paths,records,ids)
    for r in records:
        for p in r['tests']:
            if not (ROOT/p).is_file():errors.append('变更声明的测试不存在: '+p)
    for e in errors:print('[FAIL]',e)
    if errors:raise SystemExit(1)
    print('[PASS] Git差异与变更清单；人类审批状态需在Git平台确认。')
