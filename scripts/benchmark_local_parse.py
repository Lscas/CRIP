"""Measure local PDF parsing with 1/2/4 workers; never calls a model API."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from app.runner import adjacent_extraction_batches
from app.security import environment_without_secrets


def file_hash(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def fixture(path: Path, pages: int) -> None:
    from PIL import Image,ImageDraw
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    scan=Image.new('RGB',(1200,800),'white');draw=ImageDraw.Draw(scan)
    for line in range(12):draw.text((40,40+line*55),f'SCANNED QA NOTE {line+1}: PRESSURE TEST 150 PSI',fill='black')
    document=canvas.Canvas(str(path),pagesize=letter,invariant=1)
    for page in range(1,pages+1):
        if page%12==0:
            document.drawImage(ImageReader(scan),36,180,width=540,height=360)
        else:
            document.setFont('Helvetica',9)
            for line in range(28):
                document.drawString(36,760-line*24,
                    f'Section 22 11 16 page {page}: provide copper water pipe; diameter {line%6+1} inch; '
                    f'perform pressure test at 150 psi by Contractor.')
            for offset in range(6):document.rect(35+offset*80,45,60,25,stroke=1,fill=0)
        document.showPage()
    document.save()


def extraction_counts(result: dict) -> tuple[int,int]:
    items=[]
    for index,fragment in enumerate(result.get('fragments',[]),1):
        evidence={'evidence_id':f'EV-benchmark-{index}','document_id':'BENCHMARK',
                  'raw_text':fragment['text'],'locator':fragment['locator']}
        items.append((None,evidence))
    return len(items),len(adjacent_extraction_batches(items))


def run_once(path: Path, workers: int, timeout: int, directory: Path) -> dict:
    destination=directory/f'{path.stem}-{workers}.json'
    command=[sys.executable,'-m','app.parser_worker',str(path),path.name,str(workers),str(destination)]
    started=time.perf_counter()
    subprocess.run(command,cwd=ROOT,env=environment_without_secrets(),timeout=timeout,check=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    seconds=time.perf_counter()-started
    result=json.loads(destination.read_text(encoding='utf-8'))
    before,after=extraction_counts(result)
    stable={'status':result.get('status'),'pages':result.get('pages'),'fragments':result.get('fragments'),
            'visual_tasks':result.get('visual_tasks'),'geometry_summaries':result.get('geometry_summaries')}
    return {'workers':workers,'seconds':round(seconds,3),'status':result.get('status'),
            'pages_total':result.get('page_count',len(result.get('pages',[]))),
            'pages_processed':sum(page.get('status')!='NOT_PROCESSED' for page in result.get('pages',[])),
            'fragments':len(result.get('fragments',[])),
            'ocr_fragments':sum(item.get('method')=='OCR' for item in result.get('fragments',[])),
            'visual_pages':len(result.get('visual_tasks',[])),
            'geometry_pages':len(result.get('geometry_summaries',[])),
            'warnings':len(result.get('warnings',[])),
            'unbatched_extraction_requests':before,'batched_extraction_requests':after,
            'estimated_request_reduction_percent':round((1-after/max(1,before))*100,1),
            'content_sha256':hashlib.sha256(json.dumps(stable,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('pdf',nargs='*',type=Path)
    parser.add_argument('--workers',nargs='+',type=int,default=[1,2,4])
    parser.add_argument('--fixture-pages',type=int,default=48)
    parser.add_argument('--timeout',type=int,default=1800)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/local/pdf-performance-baseline.json')
    args=parser.parse_args()
    if any(value not in (1,2,4) for value in args.workers):parser.error('workers must be 1, 2, or 4')
    if not 1<=args.fixture_pages<=500:parser.error('fixture pages must be 1..500')
    with tempfile.TemporaryDirectory(prefix='cirp-benchmark-') as temporary:
        directory=Path(temporary);inputs=[path.resolve() for path in args.pdf];generated=False
        if not inputs:
            generated=True;inputs=[directory/'cirp-repeatable-fixture.pdf'];fixture(inputs[0],args.fixture_pages)
        if any(not path.is_file() or path.suffix.lower()!='.pdf' for path in inputs):
            parser.error('every input must be an existing PDF')
        files=[]
        for path in inputs:
            runs=[run_once(path,workers,args.timeout,directory) for workers in args.workers]
            baseline=next(item['seconds'] for item in runs if item['workers']==1) if 1 in args.workers else None
            for item in runs:item['speedup_vs_1_worker']=round(baseline/item['seconds'],2) if baseline else None
            files.append({'name':'generated-repeatable-fixture.pdf' if generated else path.name,
                          'source':'generated-fixture' if generated else 'user-supplied',
                          'sha256':file_hash(path),'bytes':path.stat().st_size,'runs':runs,
                          'outputs_match':len({item['content_sha256'] for item in runs})==1})
    report={'version':1,'created_at':datetime.now(timezone.utc).isoformat(),
            'python':platform.python_version(),'platform':platform.platform(),
            'paid_api_calls':0,'files':files}
    output=args.output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for item in files:
        print(item['name'])
        for run in item['runs']:
            print(f"  workers={run['workers']} seconds={run['seconds']} speedup={run['speedup_vs_1_worker']} "
                  f"requests={run['unbatched_extraction_requests']}->{run['batched_extraction_requests']}")
    print('Report:',output)
    return 0


if __name__=='__main__':raise SystemExit(main())
