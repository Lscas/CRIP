"""无LLM调用的有限任务上下文；输出到stdout，不遍历源代码或客户数据。"""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def build(task_id:str,root:Path=ROOT)->str:
    if not re.fullmatch(r"DEV-\d{3}",task_id):raise ValueError("任务编号格式错误")
    task=json.loads((root/'tasks'/f'{task_id}.json').read_text(encoding='utf-8'))
    req=json.loads((root/'spec/requirements.json').read_text(encoding='utf-8'))
    selected={r['id']:r for r in req['requirements'] if r['id'] in task['requirement_ids']}
    if set(selected)!=set(task['requirement_ids']):raise ValueError("任务引用不存在的需求")
    chunks=['# '+task['task_id']+' / '+task['title'],json.dumps(task,ensure_ascii=False,indent=2),'## 必要需求']
    for r in selected.values():chunks.append(json.dumps({k:r[k] for k in ('id','statement','status','acceptance_criteria')},ensure_ascii=False))
    for relative in task['context_files']:
        path=(root/relative).resolve()
        if not path.is_relative_to(root.resolve()) or not relative.startswith('docs/') or path.suffix!='.md':
            raise ValueError('仅允许任务列出的docs Markdown上下文')
        text=path.read_text(encoding='utf-8')
        chunks.append('## '+relative+'\n'+text)
    output='\n\n'.join(chunks)+'\n'
    if len(output)>min(task['max_context_chars'],20000):raise ValueError('上下文超过字符预算；先缩小任务，禁止截掉关键需求')
    return output
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('task_id');args=parser.parse_args()
    try:print(build(args.task_id),end='')
    except (ValueError,OSError,KeyError) as exc:parser.exit(2,str(exc)+'\n')
