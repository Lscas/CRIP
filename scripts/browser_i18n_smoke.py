"""Offline browser + real TestClient display regression; never connects to a provider.
Every browser request is fulfilled in memory at a synthetic HTTPS origin.
This is not a Windows installation or public deployment verification.
"""
from __future__ import annotations
import argparse
import base64
import re
import json
import shutil
import sys
import tempfile
import time
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from fastapi.testclient import TestClient
    from playwright.sync_api import sync_playwright
    from app.main import create_app
    from app.settings import Settings

    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='reports/local/i18n-browser')
    parser.add_argument('--offline-dom', action='store_true', help='Use a no-network DOM bridge and storage test double when browser URL navigation is blocked')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    checks, requests, errors, blocked = [], [], [], []

    def passed(name):
        checks.append(name)

    with tempfile.TemporaryDirectory(prefix='cirp-i18n-') as tmp:
        app = create_app(Settings(Path(tmp), start_worker=False, allowed_hosts=('cirp.test', 'testserver')))
        with TestClient(app, headers={'X-CIRP-Client': 'browser'}) as client, sync_playwright() as p:
            candidates = (shutil.which('chromium'), shutil.which('google-chrome'), shutil.which('chrome'),
                          r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                          r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
            executable = next((str(Path(x)) for x in candidates if x and Path(x).is_file()), None)
            browser = p.chromium.launch(**({'executable_path': executable} if executable else {}))
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='en-US', accept_downloads=True)
            context.add_init_script('''
                window.__testIntervals=[];
                const originalSetInterval=window.setInterval;
                window.setInterval=(...args)=>{const id=originalSetInterval(...args);window.__testIntervals.push(id);return id;};
            ''')

            def fulfill(route):
                request = route.request
                url = urlsplit(request.url)
                if url.hostname != 'cirp.test':
                    blocked.append(request.url)
                    route.abort()
                    return
                headers = {k: v for k, v in request.headers.items() if k.lower() not in ('host', 'content-length')}
                body = request.post_data_buffer or b''
                requests.append({'method': request.method, 'path': url.path, 'query': url.query,
                                 'body': body.decode('utf-8', errors='replace') if headers.get('content-type', '').startswith('application/json') else None})
                response = client.request(request.method, request.url, headers=headers, content=body)
                if request.method == 'POST' and url.path.endswith('/analysis-runs') and response.is_success:
                    app.state.runner.process(response.json()['id'])
                response_headers = {k: v for k, v in response.headers.items() if k.lower() not in ('content-length', 'content-encoding')}
                route.fulfill(status=response.status_code, headers=response_headers, body=response.content)

            context.route('**/*', (lambda route: route.abort()) if args.offline_dom else fulfill)
            def offline_bridge(req):
                if not req['path'].startswith('/api/'):
                    raise ValueError('Only the in-memory application API is permitted')
                body=base64.b64decode(req['body'])
                requests.append({'method':req['method'],'path':urlsplit(req['path']).path,
                                 'body':body.decode('utf-8') if req['headers'].get('Content-Type')=='application/json' else None})
                response=client.request(req['method'],req['path'],headers=req['headers'],content=body)
                if req['method']=='POST' and req['path'].endswith('/analysis-runs') and response.is_success:
                    app.state.runner.process(response.json()['id'])
                return {'status':response.status_code,'text':response.text,'headers':dict(response.headers)}

            def boot(saved=None):
                page=context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                if not args.offline_dom:
                    page.goto('https://cirp.test/')
                    return page
                page.expose_function('cirpBridge',offline_bridge)
                html=(ROOT/'web/index.html').read_text(encoding='utf-8')
                html=re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S)
                html=re.sub(r'<link[^>]*>', '', html)
                html=html.replace('</head>', '<style>'+(ROOT/'web/style.css').read_text(encoding='utf-8')+'</style></head>')
                page.set_content(html)
                page.evaluate('''(saved) => {
                    const store=new Map(Object.entries(saved||{}));window.__testStorage=store;
                    Object.defineProperty(window,'localStorage',{value:{getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,String(v)),removeItem:k=>store.delete(k)}});
                    window.__testIntervals=[];const native=window.setInterval;
                    window.setInterval=(...args)=>{const id=native(...args);window.__testIntervals.push(id);return id;};
                    window.fetch=async(path,opts={})=>{
                        let bytes=new Uint8Array();
                        if(opts.body instanceof Blob)bytes=new Uint8Array(await opts.body.arrayBuffer());
                        else if(opts.body!==undefined)bytes=new TextEncoder().encode(opts.body);
                        let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
                        const result=await window.cirpBridge({path,method:opts.method||'GET',headers:opts.headers||{},body:btoa(binary)});
                        return new Response(result.text,{status:result.status,headers:result.headers});
                    };
                }''',saved)
                page.evaluate((ROOT/'web/i18n.js').read_text(encoding='utf-8'))
                page.evaluate((ROOT/'web/app.js').read_text(encoding='utf-8')+'\nwindow.__cirpTestState=state;')
                return page
            page = boot()

            def wait_until(predicate, message, timeout=5):
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    if predicate():return
                    page.wait_for_timeout(50)
                raise AssertionError(message)

            def wait_loaded():
                wait_until(lambda:(page.locator('#version').text_content() or '').startswith('v0.2.'),'version did not load')
                page.wait_for_timeout(100)
                if not args.offline_dom: page.evaluate('window.__cirpTestState=state')
                page.evaluate('window.__testIntervals.forEach(clearInterval)')

            def assert_english(selector='#ui-language'):
                count = len(requests)
                before = page.evaluate("JSON.stringify({project:__cirpTestState.project,run:__cirpTestState.run,records:__cirpTestState.records,kind:__cirpTestState.kind,uploading:__cirpTestState.uploading,settings:__cirpTestState.settings})")
                assert page.locator('html').get_attribute('lang') == 'en'
                assert page.locator(selector).input_value() == 'en'
                assert page.evaluate("JSON.stringify({project:__cirpTestState.project,run:__cirpTestState.run,records:__cirpTestState.records,kind:__cirpTestState.kind,uploading:__cirpTestState.uploading,settings:__cirpTestState.settings})") == before
                page.wait_for_timeout(30)
                assert len(requests) == count, ('language display check caused a request', requests[count:])
                assert page.locator('[data-ui-language]').evaluate_all("(nodes)=>nodes.every(n=>n.value==='en')")

            try:
                wait_loaded()
                assert_english()
                assert page.locator('#start').inner_text() == 'Start analysis'
                assert page.title() == 'CIRP · Construction Document Review'
                assert 'Zero-cost mock mode' in page.locator('#mode-notice').inner_text()
                page.screenshot(path=str(output/'home-en.png'), full_page=True)
                passed('English-only UI, notices, title and document language load without API calls')
                if args.offline_dom:
                    saved={'cirp.ui.language.v1':'zh-CN'}
                    page.close();page=boot(saved)
                else:
                    page.evaluate("localStorage.setItem('cirp.ui.language.v1','zh-CN')")
                    page.reload()
                wait_loaded()
                assert page.locator('html').get_attribute('lang') == 'en'
                passed('Legacy Chinese preference is ignored in a fresh English-only DOM')

                page.locator('#new-project').click()
                project_name = 'Office A'
                page.locator('#project-input').fill(project_name)
                assert page.locator('#project-dialog').evaluate('(n)=>n.open')
                assert page.locator('#project-input').input_value() == project_name
                assert page.locator('#close-project').inner_text() == 'Cancel'
                assert_english('#dialog-language')
                page.locator('#project-form button[type=submit]').click()
                wait_until(lambda:'Office A' in (page.locator('#project-name').text_content() or ''),'project name did not render')
                pid = page.evaluate('__cirpTestState.project')
                assert client.get(f'/api/projects/{pid}').json()['name'] == project_name
                passed('New-project form draft and submitted project name remain literal in the English-only UI')

                message=EmailMessage();message['Subject']='RFI 55 response package'
                message.set_content('Response:\nSee the selected attachment.')
                message.add_attachment('Attached local note',subtype='plain',filename='attachment.txt')
                email_path=Path(tmp)/'response.eml';email_path.write_bytes(message.as_bytes())

                page.evaluate('''() => {
                    const original=window.fetch; let held=false;
                    window.fetch=async(...args)=>{
                        if(!held && args[1]?.method==='PUT'){
                            held=true;window.__chunkHeld=true;
                            await new Promise(resolve=>{window.__releaseChunk=resolve;});
                        }
                        return original(...args);
                    };
                }''')
                page.locator('#file-input').set_input_files([str(ROOT/'examples/demo/01_original.txt'), str(ROOT/'examples/demo/02_revision.txt')])
                wait_until(lambda:page.evaluate('Boolean(window.__chunkHeld)'),'upload chunk was not held')
                assert_english()
                assert page.evaluate('__cirpTestState.uploading') is True
                assert page.locator('#start').is_disabled()
                page.evaluate('window.__releaseChunk()')
                wait_until(lambda:not page.evaluate('Boolean(__cirpTestState.uploading)') and '2 unique-content files' in (page.locator('#file-total').text_content() or ''),'upload did not complete')
                assert page.locator('#files-body tr').count() == 2
                assert page.locator('#file-total').inner_text() == '2 unique-content files'
                assert 'Uploaded (COMPLETE)' in page.locator('#files-body').inner_text()
                passed('English-only display remains stable during a held upload without losing file selection, progress or controls')

                page.locator('#file-input').set_input_files(str(email_path))
                wait_until(lambda:'3 unique-content files' in (page.locator('#file-total').text_content() or ''),'email upload did not complete')
                page.get_by_role('button',name='Review email attachments',exact=True).click()
                wait_until(lambda:page.locator('#drawer-title').text_content()=='Email attachments','email attachment drawer did not open')
                assert 'attachment.txt' in page.locator('#drawer-body').inner_text()
                page.locator('#drawer-body').get_by_role('button',name='Import into this project',exact=True).click()
                wait_until(lambda:'4 unique-content files' in (page.locator('#file-total').text_content() or ''),'selected attachment import did not complete')
                assert page.locator('#files-body tr').count()==4 and page.locator('#drawer').evaluate('(n)=>n.hidden')
                assert 'Email attachment from response.eml' in page.locator('#files-body').inner_text()
                page.get_by_role('button',name='Review email attachments',exact=True).click()
                page.locator('#drawer-body').get_by_role('button',name='Import into this project',exact=True).click()
                wait_until(lambda:page.locator('#files-body tr').count()==5,'duplicate attachment import was not retained')
                passed('A selected EML attachment imports locally with source provenance and without automatic recursive analysis')

                page.locator('#start').click()
                wait_until(lambda:page.locator('#run-state').get_attribute('data-code')=='PARTIAL','run did not reach PARTIAL')
                rid = page.evaluate('__cirpTestState.run')
                counts = {kind: int(page.locator('#count-'+kind).inner_text()) for kind in ('MATERIAL', 'INSPECTION')}
                assert counts == {'MATERIAL': 2, 'INSPECTION': 2}, counts
                assert page.locator('#run-message').inner_text().strip()
                workflow_text=page.locator('#workflow-body').inner_text()
                assert 'Email attachment' in workflow_text and 'response.eml' in workflow_text and 'attachment.txt' in workflow_text
                assert 'Parent email' in workflow_text and 'Selected attachment' in workflow_text
                assert 'Explicitly imported 2 times' in workflow_text
                states_before = {id_: page.locator('#'+id_).is_disabled() for id_ in ('start', 'pause', 'resume', 'export-json', 'export-xlsx')}
                assert {id_: page.locator('#'+id_).is_disabled() for id_ in states_before} == states_before
                passed('Run selection, PARTIAL status, candidates, numbers, cost and button enablement are unchanged')

                page.locator('#filter').fill('Concrete')
                filtered_before = page.locator('#results-body tr').count()
                assert filtered_before > 0
                assert page.locator('#filter').input_value() == 'Concrete'
                assert page.locator('#results-body tr').count() == filtered_before
                page.locator('#filter').fill('')
                passed('Search text and filter behavior stay unchanged')

                page.locator('#results-body tr td:first-child button').first.click()
                panel=page.locator('.verification-panel')
                page.wait_for_selector('.verification-panel', state='visible')
                assert panel.get_by_role('button',name='Recheck meaning (uses project budget)',exact=True).is_disabled()
                assert 'Verification incomplete' in panel.inner_text()
                quotes=panel.locator('blockquote').all_text_contents()
                source_texts=[f.read_text(encoding='utf-8') for f in (ROOT/'examples/demo').glob('*.txt')]
                assert quotes and all(any(q in text for text in source_texts) for q in quotes)
                passed('Mock shows exact field-level citations but never claims semantic verification passed')
                assert_english('#drawer-language')
                assert panel.locator('blockquote').all_text_contents()==quotes
                assert panel.locator('blockquote').all_text_contents()==quotes
                page.screenshot(path=str(output/'citations-en.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth')<=390
                page.screenshot(path=str(output/'citations-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                passed('Field verification and verbatim citations survive the English-only mobile layout')
                quote=panel.locator('blockquote').first.inner_text()
                panel.locator('.evidence-button').first.click()
                page.wait_for_selector('#drawer-body mark')
                assert page.locator('#drawer-body mark').inner_text()==quote
                assert_english('#drawer-language')
                assert page.locator('#drawer-body mark').inner_text()==quote
                raw_evidence = page.locator('#drawer-body pre').last.inner_text()
                original_link = page.locator('#drawer-body a').get_attribute('href')
                assert quote in raw_evidence
                assert client.get(original_link).content in [f.read_bytes() for f in (ROOT/'examples/demo').glob('*.txt')]
                page.screenshot(path=str(output/'source-highlight.png'),full_page=True)
                passed('Exact citation, original Unicode evidence and source file bytes remain traceable')
                page.locator('#close-drawer').click()

                for lang in ('en',):
                    assert_english()
                    for fmt in ('json', 'xlsx'):
                        if args.offline_dom:
                            response=client.get(f'/api/analysis-runs/{rid}/exports/{fmt}')
                            assert response.status_code==200 and 'attachment;' in response.headers['content-disposition']
                            assert '/exports/' in page.locator('#export-'+fmt).evaluate('(n)=>n.onclick.toString()')
                        else:
                            with page.expect_download() as info:
                                page.locator('#export-'+fmt).click()
                            download = info.value
                            path = output / f'download-{lang}.{fmt}'
                            download.save_as(path)
                            assert path.stat().st_size > 0 and download.failure() is None
                    page.evaluate("window.scrollTo(0,0);document.getElementById('toast').hidden=true")
                    page.screenshot(path=str(output/f'review-{lang}.png'), full_page=True)
                passed('Export endpoints and unchanged button handlers verified in English' if args.offline_dom else 'JSON and Excel download buttons work in English')

                for width in (390, 760, 1024):
                    page.set_viewport_size({'width': width, 'height': 844})
                    for lang in ('en',):
                        assert_english()
                        assert page.evaluate('document.documentElement.scrollWidth') <= width, (width, lang, page.evaluate('document.documentElement.scrollWidth'))
                        if width == 390:
                            page.evaluate('window.scrollTo(0,0)')
                            page.screenshot(path=str(output/f'mobile-{lang}.png'), full_page=True)
                passed('English layout fits 390px, 760px and 1024px without page overflow')
                assert not errors, errors
                assert not blocked, blocked
                passed('No JavaScript page errors and no external network attempts')
                report = {
                    'mode': 'No-network Chromium DOM + FastAPI TestClient bridge; storage test double' if args.offline_dom else 'Offline Chromium + in-memory FastAPI TestClient on synthetic HTTPS origin',
                    'checks': checks, 'counts': counts, 'js_errors': errors, 'unexpected_external_requests': blocked,
                    'model_calls': 0, 'locale_api_calls': 0, 'note': 'The application display is English-only. Export notices, schema keys, original documents, model contents and technical diagnostic JSON remain original.',
                    'not_tested': (['native browser localStorage persistence', 'browser download navigation', 'HTTP-origin CSP execution'] if args.offline_dom else []) + ['Windows installation', 'real public/local HTTP browser route', 'live provider', 'construction accuracy', '10GB throughput'],
                }
                (output/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(f'PASS {len(checks)} browser regression checks; model calls=0; external requests=0')
            except Exception:
                page.screenshot(path=str(output/'failure.png'), full_page=True)
                (output/'failure.json').write_text(json.dumps({'checks': checks, 'errors': errors, 'blocked': blocked, 'request_tail': requests[-10:]}, ensure_ascii=False, indent=2), encoding='utf-8')
                raise
            finally:
                context.close()
                browser.close()


if __name__ == '__main__':
    main()
