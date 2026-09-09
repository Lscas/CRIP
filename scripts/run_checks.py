"""定向离线检查；完整日志留在reports/local，终端只给有界摘要。"""
from __future__ import annotations
import argparse,subprocess,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
AREAS={'api':['tests/app/test_api.py'],'gateway':['tests/app/test_gateway_budget.py'],
       'parsers':['tests/app/test_parsers.py'],'governance':['tests/test_governance.py','tests/test_devtools.py'],'all':['tests']}
def main()->int:
    p=argparse.ArgumentParser();p.add_argument('--area',choices=AREAS,default='all');args=p.parse_args()
    commands=[[sys.executable,'-m','pytest',*AREAS[args.area],'-q','--tb=short']]
    if args.area in ('governance','all'):commands.append([sys.executable,'scripts/check_spec_sync.py'])
    if args.area=='all' and shutil.which('node'):commands.append(['node','--check','web/app.js'])
    out=ROOT/'reports/local';out.mkdir(parents=True,exist_ok=True);failed=False
    for index,command in enumerate(commands):
        completed=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
        text=completed.stdout+completed.stderr;(out/f'{args.area}-{index}.log').write_text(text,encoding='utf-8')
        print(('PASS ' if completed.returncode==0 else 'FAIL ')+' '.join(command))
        if completed.returncode:print('\n'.join(text.splitlines()[-50:])[:10000]);failed=True
        elif text.strip():print('\n'.join(text.strip().splitlines()[-2:])[:1500])
    print('完整日志：reports/local/；默认测试没有真实模型调用。')
    return int(failed)
if __name__=='__main__':raise SystemExit(main())
