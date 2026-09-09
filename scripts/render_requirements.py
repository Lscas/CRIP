"""从机器需求表生成可读需求表。"""
from __future__ import annotations
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def render(data: dict) -> str:
    lines = ['# 完整需求表（自动生成）', '', f"规格版本：{data['spec_version']}", '',
             '权威来源：`spec/requirements.json`。修改JSON后重新生成，不手改此表。所有产品业务功能仍未实现；superseded保留历史语义，不作为现行规则。', '']
    for r in data['requirements']:
        lines.extend([f"## {r['id']} · {r['title']}", '', f"优先级：{r['priority']}；状态：{r['status']}", '', r['statement'], ''])
        if r.get('superseded_by'):
            lines.append('替代需求：'+', '.join(r['superseded_by']))
        lines.extend('- '+s for s in r['acceptance_criteria'])
        lines.append('')
    return '\n'.join(lines).rstrip()+'\n'
if __name__ == '__main__':
    data=json.loads((ROOT/'spec/requirements.json').read_text(encoding='utf-8'))
    (ROOT/'docs/REQUIREMENTS.md').write_text(render(data),encoding='utf-8')
