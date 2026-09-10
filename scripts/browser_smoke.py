"""离线DOM集成：浏览器fetch通过内存桥接到TestClient，不连接网络。
不替代实际浏览器->本地HTTP的安装验收。使用临时数据，不读.env。
"""
from __future__ import annotations
import argparse,base64,json,re,shutil,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    from playwright.sync_api import sync_playwright
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.settings import Settings
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='reports/local/browser')
    parser.add_argument('--browser',default=None)
    args=parser.parse_args();output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cirp-ui-') as tmp:
        app=create_app(Settings(Path(tmp),start_worker=False))
        with TestClient(app) as client,sync_playwright() as p:
            executable=args.browser or shutil.which('chromium') or shutil.which('google-chrome')
            options={'headless':True}
            if executable:options['executable_path']=executable
            browser=p.chromium.launch(**options)
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/*',lambda route:route.abort())
                def bridge(req):
                    if not req['path'].startswith('/api/'):
                        raise ValueError('仅允许内存测试API')
                    response=client.request(req['method'],req['path'],headers=req['headers'],content=base64.b64decode(req['body']))
                    if req['method']=='POST' and req['path'].endswith('/analysis-runs') and response.is_success:
                        app.state.runner.process(response.json()['id'])
                    return {'status':response.status_code,'text':response.text,'headers':dict(response.headers)}
                page.expose_function('cirpBridge',bridge)
                html=(ROOT/'web/index.html').read_text(encoding='utf-8')
                html=re.sub(r'<script[^>]*>.*?</script>','',html,flags=re.S)
                html=re.sub(r'<link[^>]*>','',html)
                html=html.replace('</head>','<style>'+(ROOT/'web/style.css').read_text(encoding="utf-8")+'</style></head>')
                page.set_content(html)
                page.evaluate('''() => {
                    const store=new Map(); Object.defineProperty(window,'localStorage',{value:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v),removeItem:k=>store.delete(k)}});
                    window.fetch=async(path,opts={})=>{
                        let bytes=new Uint8Array();
                        if(opts.body instanceof Blob)bytes=new Uint8Array(await opts.body.arrayBuffer());
                        else if(opts.body!==undefined)bytes=new TextEncoder().encode(opts.body);
                        let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
                        const result=await window.cirpBridge({path,method:opts.method||'GET',headers:opts.headers||{},body:btoa(binary)});
                        return new Response(result.text,{status:result.status,headers:result.headers});
                    };
                }''')
                page.evaluate((ROOT/'web/i18n.js').read_text(encoding="utf-8"))
                page.evaluate((ROOT/'web/app.js').read_text(encoding="utf-8"))
                page.locator('#new-project').click()
                page.locator('#project-input').fill('商业新建 · 合成演示项目')
                page.locator('#project-form button[type=submit]').click()
                page.wait_for_function("document.querySelector('#project-name').textContent.includes('合成演示')")
                page.locator('#file-input').set_input_files([str(ROOT/'examples/demo/01_original.txt'),str(ROOT/'examples/demo/02_revision.txt')])
                page.wait_for_function("document.querySelector('#file-total').textContent.includes('2 个')")
                page.locator('#start').click()
                page.wait_for_function("document.querySelector('#run-state').dataset.code==='PARTIAL'")
                counts={kind:int(page.locator('#count-'+kind).inner_text()) for kind in ['MATERIAL','INSPECTION','CONFLICT','MISSING']}
                assert counts=={'MATERIAL':2,'INSPECTION':2,'CONFLICT':1,'MISSING':0},counts
                page.locator('#results-body tr td:first-child button').first.click()
                page.locator('#drawer-body').get_by_role('button',name='接受',exact=True).click()
                page.wait_for_function("document.querySelector('#results-body').textContent.includes('ACCEPTED')")
                page.locator('#results-body .evidence-button').first.click()
                page.wait_for_function("document.querySelector('#drawer-title').textContent==='原始证据'")
                assert 'Revision' in page.locator('#drawer-body').inner_text()
                page.locator('#close-drawer').click()
                page.evaluate("window.scrollTo(0, 0); document.getElementById('toast').hidden=true")
                page.screenshot(path=str(output/'desktop.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(output/'mobile.png'),full_page=True)
                width=page.evaluate('document.documentElement.scrollWidth')
                assert width<=390,('移动端横向溢出',width)
                assert not errors,errors
                report={'mode':'offline DOM + TestClient bridge, no network','counts':counts,'review':'ACCEPTED',
                        'evidence':'original text and locator visible','mobile_scroll_width':width,'js_errors':errors,
                        'not_tested':['real localhost browser HTTP','real model API','10GB scale','construction accuracy']}
                (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print('PASS offline DOM integration: project/upload/analyze/review/evidence/mobile; no model HTTP')
            finally:browser.close()

if __name__=='__main__':main()
