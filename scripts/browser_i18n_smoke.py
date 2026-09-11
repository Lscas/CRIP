"""Offline browser + real TestClient display regression; never connects to a provider.
Every browser request is fulfilled in memory at a synthetic HTTPS origin.
This is not a Windows installation or public deployment verification.
"""
from __future__ import annotations
import argparse
import base64
import re
import hashlib
import io
import json
import shutil
import sys
import tempfile
import zipfile
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

    def stable_export(client, rid):
        data = client.get(f'/api/analysis-runs/{rid}/exports/json').json()
        data.pop('generated_at', None)
        return data

    def workbook_parts(client, rid):
        response = client.get(f'/api/analysis-runs/{rid}/exports/xlsx')
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            return {name: archive.read(name) for name in archive.namelist() if name.startswith('xl/')}

    with tempfile.TemporaryDirectory(prefix='cirp-i18n-') as tmp:
        app = create_app(Settings(Path(tmp), start_worker=False, allowed_hosts=('cirp.test', 'testserver')))
        with TestClient(app, headers={'X-CIRP-Client': 'browser'}) as client, sync_playwright() as p:
            executable = shutil.which('chromium') or shutil.which('google-chrome')
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

            def wait_loaded():
                page.wait_for_function("document.querySelector('#version').textContent.startsWith('v0.2.')")
                page.wait_for_timeout(100)
                if not args.offline_dom: page.evaluate('window.__cirpTestState=state')
                page.evaluate('window.__testIntervals.forEach(clearInterval)')

            def language(lang, selector='#ui-language'):
                count = len(requests)
                before = page.evaluate("JSON.stringify({project:__cirpTestState.project,run:__cirpTestState.run,records:__cirpTestState.records,kind:__cirpTestState.kind,uploading:__cirpTestState.uploading,settings:__cirpTestState.settings})")
                page.locator(selector).select_option(lang)
                assert page.locator('html').get_attribute('lang') == lang
                assert page.evaluate("JSON.stringify({project:__cirpTestState.project,run:__cirpTestState.run,records:__cirpTestState.records,kind:__cirpTestState.kind,uploading:__cirpTestState.uploading,settings:__cirpTestState.settings})") == before
                page.wait_for_timeout(30)
                assert len(requests) == count, ('language switch caused a request', requests[count:])
                assert page.locator('[data-ui-language]').evaluate_all('(nodes)=>nodes.every(n=>n.value===document.documentElement.lang)')

            try:
                wait_loaded()
                assert page.locator('html').get_attribute('lang') == 'zh-CN'
                assert page.locator('#start').inner_text() == '开始分析'
                page.screenshot(path=str(output/'home-zh.png'), full_page=True)
                passed('Chinese default even with an English browser locale')
                language('en')
                assert page.title() == 'CIRP · Construction Document Review'
                assert 'Zero-cost mock mode' in page.locator('#mode-notice').inner_text()
                page.screenshot(path=str(output/'home-en.png'), full_page=True)
                passed('Header switch updates static UI, notices, title and document lang without API calls')
                if args.offline_dom:
                    saved=page.evaluate('Object.fromEntries(window.__testStorage)')
                    page.close();page=boot(saved)
                else:
                    page.reload()
                wait_loaded()
                assert page.locator('html').get_attribute('lang') == 'en'
                passed('Stored preference restored in a fresh DOM using a storage test double' if args.offline_dom else 'Language persists through real browser reload at the same origin')

                page.locator('#new-project').click()
                project_name = '项目名保持原文 / Office A'
                page.locator('#project-input').fill(project_name)
                language('zh-CN', '#dialog-language')
                assert page.locator('#project-dialog').evaluate('(n)=>n.open')
                assert page.locator('#project-input').input_value() == project_name
                assert page.locator('#close-project').inner_text() == '取消'
                language('en', '#dialog-language')
                page.locator('#project-form button[type=submit]').click()
                page.wait_for_function("document.querySelector('#project-name').textContent.includes('Office A')")
                pid = page.evaluate('__cirpTestState.project')
                assert client.get(f'/api/projects/{pid}').json()['name'] == project_name
                passed('New-project form draft and submitted project name are not translated or reset')

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
                page.wait_for_function('window.__chunkHeld===true')
                language('zh-CN')
                assert page.evaluate('__cirpTestState.uploading') is True
                assert page.locator('#start').is_disabled()
                page.evaluate('window.__releaseChunk()')
                page.wait_for_function("!__cirpTestState.uploading && document.querySelector('#file-total').textContent.includes('2 个')")
                assert page.locator('#files-body tr').count() == 2
                language('en')
                assert page.locator('#file-total').inner_text() == '2 unique-content files'
                assert 'Uploaded (COMPLETE)' in page.locator('#files-body').inner_text()
                passed('Language can change during a held upload without losing file selection, progress or controls')

                page.locator('#start').click()
                page.wait_for_function("document.querySelector('#run-state').dataset.code==='PARTIAL'")
                rid = page.evaluate('__cirpTestState.run')
                counts = {kind: int(page.locator('#count-'+kind).inner_text()) for kind in ('MATERIAL', 'INSPECTION', 'CONFLICT', 'MISSING')}
                assert counts == {'MATERIAL': 2, 'INSPECTION': 2, 'CONFLICT': 1, 'MISSING': 0}, counts
                assert 'Only the current text-processing capabilities' in page.locator('#run-message').inner_text()
                json_before = stable_export(client, rid)
                excel_before = workbook_parts(client, rid)
                states_before = {id_: page.locator('#'+id_).is_disabled() for id_ in ('start', 'pause', 'resume', 'export-json', 'export-xlsx')}
                language('zh-CN')
                assert stable_export(client, rid) == json_before
                assert workbook_parts(client, rid) == excel_before
                assert {id_: page.locator('#'+id_).is_disabled() for id_ in states_before} == states_before
                language('en')
                passed('Run selection, PARTIAL status, candidates, numbers, cost and button enablement are unchanged')
                passed('JSON contents (excluding export timestamp) and XLSX workbook parts are identical across languages')

                page.locator('#filter').fill('Concrete')
                filtered_before = page.locator('#results-body tr').count()
                assert filtered_before > 0
                language('zh-CN')
                assert page.locator('#filter').input_value() == 'Concrete'
                assert page.locator('#results-body tr').count() == filtered_before
                page.locator('#filter').fill('')
                language('en')
                passed('Search text and filter behavior stay unchanged')

                page.locator('#results-body tr td:first-child button').first.click()
                panel=page.locator('.verification-panel')
                assert panel.is_visible()
                assert panel.get_by_role('button',name='Recheck meaning (uses project budget)',exact=True).is_disabled()
                assert 'Verification incomplete' in panel.inner_text()
                quotes=panel.locator('blockquote').all_text_contents()
                assert quotes and all(any(q in e['raw_text'] for e in json_before['evidence']) for q in quotes)
                passed('Mock shows exact field-level citations but never claims semantic verification passed')
                language('zh-CN','#drawer-language')
                assert panel.locator('blockquote').all_text_contents()==quotes
                page.screenshot(path=str(output/'citations-zh.png'),full_page=True)
                language('en','#drawer-language')
                assert panel.locator('blockquote').all_text_contents()==quotes
                page.screenshot(path=str(output/'citations-en.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth')<=390
                page.screenshot(path=str(output/'citations-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                passed('Field verification and verbatim citations survive display-language changes and mobile layout')
                quote=panel.locator('blockquote').first.inner_text()
                panel.locator('.evidence-button').first.click()
                page.wait_for_selector('#drawer-body mark')
                assert page.locator('#drawer-body mark').inner_text()==quote
                language('zh-CN','#drawer-language')
                assert page.locator('#drawer-body mark').inner_text()==quote
                page.screenshot(path=str(output/'source-highlight.png'),full_page=True)
                language('en','#drawer-language')
                passed('Clicking an exact citation highlights the same original Unicode text without translation')
                page.locator('#close-drawer').click()
                page.locator('#results-body tr td:first-child button').first.click()
                area = page.locator('#drawer-body textarea')
                original_candidate = area.input_value()
                edited_draft = original_candidate + '\n'
                area.fill(edited_draft)
                note = '审核记录已保存 / literal user note'
                page.locator('#drawer-body input').fill(note)
                language('zh-CN', '#drawer-language')
                assert area.input_value() == edited_draft
                assert page.locator('#drawer-body input').input_value() == note
                assert not page.locator('#drawer').evaluate('(n)=>n.hidden')
                language('en', '#drawer-language')
                assert area.input_value() == edited_draft
                page.locator('#drawer-body').get_by_role('button', name='Accept', exact=True).click()
                page.wait_for_function("document.querySelector('#results-body').textContent.includes('Accepted (ACCEPTED)')")
                review_requests = [r for r in requests if r['method']=='POST' and r['path'].endswith('/review')]
                review_body = json.loads(review_requests[-1]['body'])
                assert review_body['action'] == 'ACCEPTED' and review_body['note'] == note
                assert set(review_body) == {'action', 'expected_version', 'note'}
                language('zh-CN')
                assert '已接受 (ACCEPTED)' in page.locator('#results-body').inner_text()
                passed('Open editor JSON and note survive switching; review request and canonical status remain unchanged')

                page.locator('#results-body .evidence-button').first.click()
                page.wait_for_function("document.querySelector('#drawer-title').textContent==='原始证据'")
                raw_evidence = page.locator('#drawer-body pre').last.inner_text()
                locator = page.locator('#drawer-body pre').first.inner_text()
                language('en', '#drawer-language')
                assert page.locator('#drawer-title').inner_text() == 'Original evidence'
                assert page.locator('#drawer-body pre').last.inner_text() == raw_evidence
                assert page.locator('#drawer-body pre').first.inner_text() == locator
                original_link = page.locator('#drawer-body a').get_attribute('href')
                assert client.get(original_link).content in [f.read_bytes() for f in (ROOT/'examples/demo').glob('*.txt')]
                page.locator('#close-drawer').click()
                passed('Evidence wording, revision date, locator and original file bytes are preserved')

                for lang in ('zh-CN', 'en'):
                    language(lang)
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
                passed('Export endpoints and unchanged button handlers verified in both languages' if args.offline_dom else 'JSON and Excel download buttons work in both display languages')

                for width in (390, 760, 1024):
                    page.set_viewport_size({'width': width, 'height': 844})
                    for lang in ('zh-CN', 'en'):
                        language(lang)
                        assert page.evaluate('document.documentElement.scrollWidth') <= width, (width, lang, page.evaluate('document.documentElement.scrollWidth'))
                        if width == 390:
                            page.evaluate('window.scrollTo(0,0)')
                            page.screenshot(path=str(output/f'mobile-{lang}.png'), full_page=True)
                passed('Chinese and English layouts fit 390px, 760px and 1024px without page overflow')
                assert not errors, errors
                assert not blocked, blocked
                passed('No JavaScript page errors and no external network attempts')
                report = {
                    'mode': 'No-network Chromium DOM + FastAPI TestClient bridge; storage test double' if args.offline_dom else 'Offline Chromium + in-memory FastAPI TestClient on synthetic HTTPS origin',
                    'checks': checks, 'counts': counts, 'js_errors': errors, 'unexpected_external_requests': blocked,
                    'model_calls': 0, 'locale_api_calls': 0, 'note': 'Language changes are presentation-only. Export notices, schema keys, original documents, model contents and technical diagnostic JSON remain original.',
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
