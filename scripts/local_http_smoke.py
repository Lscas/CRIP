"""真实 loopback HTTP 验收。仅临时合成资料；默认不调用模型或读取用户 .env。"""
from __future__ import annotations
import argparse
from collections import Counter
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

import httpx

ROOT=Path(__file__).resolve().parents[1]


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',default='reports/local/local-http.json')
    args=parser.parse_args()
    report={'test_type':'real loopback HTTP, synthetic fixture, local_entry', 'network_model_calls':0}
    with tempfile.TemporaryDirectory(prefix='cirp-http-') as tmp:
        data=Path(tmp)/'data'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        process=None
        log=(Path(tmp)/'http.log').open('wb')

        def start():
            nonlocal process
            env=os.environ.copy();env['PYTHONUNBUFFERED']='1';env['PYTHONUTF8']='1'
            process=subprocess.Popen([sys.executable,'-m','app.local_entry','--port',str(port),'--no-browser',
                                      '--data-dir',str(data)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('本机服务提前退出')
                try:
                    r=httpx.get(base+'/api/health/version',timeout=1,trust_env=False)
                    if r.status_code==200:return r.json()
                except httpx.HTTPError:pass
                time.sleep(.15)
            raise RuntimeError('本机HTTP启动超时')

        def stop():
            nonlocal process
            if process and process.poll() is None:
                if os.name!='nt':process.send_signal(signal.SIGINT)
                else:process.terminate()
                try:process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=5)
            process=None

        try:
            report['health']=start()
            assert report['health']['provider']=='mock'
            with httpx.Client(base_url=base,timeout=15,trust_env=False,headers={'X-CIRP-Client':'browser'}) as c:
                assert c.get('/').status_code==200
                assert c.get('/api/settings').json()['thinking']=='disabled'
                assert c.get('/api/settings').json()['upload_capacity_bytes']==10_000_000_000
                bad=c.post('/api/projects',json={'name':'blocked'},headers={'Origin':'https://untrusted.example'})
                assert bad.status_code==403
                assert c.get('/api/projects',headers={'Host':'untrusted.example'}).status_code==400
                project=c.post('/api/projects',json={'name':'本机部署 HTTP 合成验收'});assert project.status_code==201
                pid=project.json()['id']
                for path in sorted((ROOT/'examples/demo').glob('*.txt')):
                    raw=path.read_bytes()
                    u=c.post(f'/api/projects/{pid}/uploads',json={'name':path.name,'size':len(raw)})
                    assert u.status_code==201
                    upload_id=u.json()['id']
                    assert c.put(f'/api/uploads/{upload_id}/chunk?offset=0',content=raw).status_code==200
                    assert c.post(f'/api/uploads/{upload_id}/complete').status_code==200
                r=c.post(f'/api/projects/{pid}/analysis-runs');assert r.status_code==202
                rid=r.json()['id']
                deadline=time.monotonic()+25
                while time.monotonic()<deadline:
                    run=c.get(f'/api/analysis-runs/{rid}').json()
                    if run['status'] not in ('QUEUED','RUNNING'):break
                    time.sleep(.2)
                assert run['status']=='PARTIAL',run
                rows=c.get(f'/api/analysis-runs/{rid}/records').json()
                counts=dict(Counter(x['record']['kind'] for x in rows))
                assert counts=={'CONFLICT':1,'INSPECTION':2,'MATERIAL':2},counts
                mat=next(x for x in rows if x['record']['kind']=='MATERIAL')
                eid=mat['record']['candidate']['evidence_ids'][0]
                ev=c.get(f'/api/analysis-runs/{rid}/evidence/{eid}')
                assert ev.status_code==200 and ev.json()['evidence']['raw_text']
                record_id=mat['record']['meta']['record_id']
                r=c.post(f'/api/records/{record_id}/review',json={'action':'ACCEPTED','expected_version':0})
                assert r.status_code==200
                cost=c.get(f'/api/analysis-runs/{rid}/cost').json();assert cost['calls']==0
                js=c.get(f'/api/analysis-runs/{rid}/exports/json');assert js.status_code==200 and len(js.json()['records'])==5
                xl=c.get(f'/api/analysis-runs/{rid}/exports/xlsx');assert xl.status_code==200
                with zipfile.ZipFile(io.BytesIO(xl.content)) as z:assert '[Content_Types].xml' in z.namelist()
                report.update(counts=counts,run_status=run['status'],evidence='PASS',review='PASS',json_export='PASS',xlsx_export='PASS',cross_origin_status=bad.status_code,cost=cost)
            stop()
            start()
            with httpx.Client(base_url=base,timeout=5,trust_env=False) as c:
                assert c.get(f'/api/projects/{pid}').status_code==200
                assert len(c.get(f'/api/analysis-runs/{rid}/records').json())==5
                assert len(c.get(f'/api/records/{record_id}/history').json())==1
                assert len(c.get(f'/api/projects/{pid}/manifest').json()['documents'])==2
            report['restart_persistence']='PASS: project, documents, records and review retained'
            report['result']='PASS'
        finally:
            stop();log.close()
    output=Path(args.report).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: real HTTP upload / analyze / review / export / restart persistence; paid calls=0')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
