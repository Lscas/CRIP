"""定向离线检查；完整日志留在reports/local，终端只给有界摘要。"""
from __future__ import annotations
import argparse,os,subprocess,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
AREAS={'evidence':['tests/app/test_verification.py'],'ui':['tests/web/test_display_contract.py'],'local':['tests/app/test_local_entry.py'],'render':['tests/app/test_render_preview.py'],'remote':['tests/app/test_remote_access.py'],'api':['tests/app/test_api.py'],'gateway':['tests/app/test_gateway_budget.py'],
       'parsers':['tests/app/test_parsers.py'],'governance':['tests/test_governance.py','tests/test_devtools.py'],'all':['tests']}

def build_commands(area: str, node: str | None) -> list[list[str]]:
    commands=[[sys.executable,'-m','pytest',*AREAS[area],'-q','--tb=short']]
    if area in ('governance','all'):
        commands.extend([
            [sys.executable,'scripts/check_spec_sync.py'],
            [sys.executable,'scripts/build_bundle_manifest.py','--check'],
        ])
    if area in ('ui','remote','all') and not node:
        raise RuntimeError(f'Node.js is required for the {area} checks; no JavaScript check was run.')
    if area in ('ui','all'):
        commands.extend([[node,'--check','web/i18n.js'],[node,'--check','web/app.js'],[node,'--test','tests/web/i18n.test.mjs']])
    if area in ('remote','all'):
        commands.append([node,'--test','tests/deploy/worker.test.mjs'])
    return commands

def main()->int:
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    p=argparse.ArgumentParser();p.add_argument('--area',choices=AREAS,default='all');args=p.parse_args()
    try:commands=build_commands(args.area,shutil.which('node'))
    except RuntimeError as exc:
        print(f'FAIL {exc}')
        return 1
    out=ROOT/'reports/local';out.mkdir(parents=True,exist_ok=True);failed=False
    child_env={**os.environ,'PYTHONUTF8':'1'}
    for index,command in enumerate(commands):
        completed=subprocess.run(command,cwd=ROOT,env=child_env,capture_output=True,text=True,encoding='utf-8',errors='replace')
        text=completed.stdout+completed.stderr;(out/f'{args.area}-{index}.log').write_text(text,encoding='utf-8')
        print(('PASS ' if completed.returncode==0 else 'FAIL ')+' '.join(command))
        if completed.returncode:print('\n'.join(text.splitlines()[-50:])[:10000]);failed=True
        elif text.strip():print('\n'.join(text.strip().splitlines()[-2:])[:1500])
    print('完整日志：reports/local/；默认测试没有真实模型调用。')
    return int(failed)
if __name__=='__main__':raise SystemExit(main())
