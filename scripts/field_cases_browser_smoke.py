"""No-network Chromium acceptance for local human Reference cases.

The browser is real Chromium, while every request is served by a synthetic
HTTPS origin backed by FastAPI TestClient and a temporary SQLite database.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from fastapi.testclient import TestClient
    from playwright.sync_api import sync_playwright
    from app.main import create_app
    from app.settings import Settings
    from tests.app.test_reference_cases import _result, _followup_result
    from tests.app.test_reference_results import _saved_run

    report_path=ROOT/'reports/field_cases_browser_validation_2026-10-03.md'
    checks=[];errors=[];requests=[]
    with tempfile.TemporaryDirectory(prefix='cirp-field-cases-') as tmp:
        app=create_app(Settings(Path(tmp),start_worker=False,allowed_hosts=('field-cases.test','testserver')))
        with TestClient(app,headers={'X-CIRP-Client':'browser'}) as client:
            project=client.post('/api/projects',json={'name':'Synthetic field case project'}).json()
            db,run,document,_=_saved_run(client,project,'REFERENCE_QA')
            questions=[
                ('What approved color applies to Finish key PT9?','ANSWERED'),
                ('Which section contains the finish requirement?','CANNOT_ANSWER'),
                ('What field confirmation is still missing?','NEED_USER_INPUT'),
            ]
            result_ids=[_result(app.state.reference_results,run,question,status) for question,status in questions]
            assert client.get(f'/api/projects/{project["id"]}/reference-results?limit=50').json()['total']==3
            evaluation=client.post(f'/api/projects/{project["id"]}/reference-evaluations',json={
                'run_id':run['id'],'name':'Synthetic failed field case','questions':['What failed field check needs review?']}).json()
            app.state.reference_evaluations.fail(
                evaluation['evaluation_id'],evaluation['items'][0]['item_id'],'MODEL_OUTPUT_REJECTED')
            other=client.post('/api/projects',json={'name':'Synthetic other project'}).json()
            with sync_playwright() as p:
                candidates=(shutil.which('chromium'),shutil.which('google-chrome'),shutil.which('chrome'),
                            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
                executable=next((str(Path(path)) for path in candidates if path and Path(path).is_file()),None)
                browser=p.chromium.launch(**({'executable_path':executable} if executable else {}))
                context=browser.new_context(viewport={'width':1440,'height':1000})
                def fulfill(route):
                    request=route.request;url=urlsplit(request.url)
                    if url.hostname!='field-cases.test':route.abort();return
                    headers={k:v for k,v in request.headers.items() if k.lower() not in {'host','content-length'}}
                    body=request.post_data_buffer or b''
                    requests.append((request.method,url.path))
                    if url.path.endswith('/follow-up-results'):print('SMOKE route: follow-up request',flush=True)
                    response=client.request(request.method,request.url,headers=headers,content=body)
                    if url.path.endswith('/follow-up-results'):print(f'SMOKE route: follow-up response {response.status_code}',flush=True)
                    route.fulfill(status=response.status_code,headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-encoding'}},body=response.content)
                context.route('**/*',fulfill)
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                try:
                    def wait_state(expression,timeout_ms=5000):
                        for _ in range(timeout_ms//50):
                            if page.evaluate(f'() => Boolean({expression})'):return
                            page.wait_for_timeout(50)
                        raise AssertionError(f'UI state did not settle: {expression}; version={page.locator("#version").text_content()!r}; state={page.evaluate("() => JSON.stringify({project:window.__cirpTestState?.project,results:window.__cirpTestState?.referenceResults?.length})")}; errors={errors!r}')
                    page.goto('https://field-cases.test/');wait_state("document.querySelector('#version').textContent.startsWith('v0.2.')")
                    page.evaluate('window.__cirpTestState=state;window.__cirpTestIntervals=[...[]]')
                    page.locator('#project-select').select_option(project['id']);wait_state(f"__cirpTestState.project==='{project['id']}'")
                    wait_state('__cirpTestState.referenceResults.length===3')
                    # Each saved result status visibly offers explicit human follow-up.
                    assert page.locator('#reference-results .reference-result-card').count()==3
                    assert page.get_by_role('button',name='Send to human follow-up').count()>=4
                    assert page.locator('#reference-evaluations .reference-evaluation-item').count()==1
                    checks.append('ANSWERED, CANNOT_ANSWER, NEED_USER_INPUT result cards and FAILED evaluation item expose explicit follow-up actions')
                    page.locator('#reference-results .reference-result-card').filter(
                        has_text=questions[0][0]).get_by_role('button',name='Send to human follow-up').click()
                    wait_state('__cirpTestState.referenceCaseSelected!==null')
                    assert page.locator('#reference-case-detail').inner_text()
                    page.locator('#reference-case-detail textarea').nth(0).fill('Synthetic site note.')
                    page.locator('#reference-case-detail .reference-case-form input').fill('field-engineer')
                    picker=page.locator('#reference-case-detail .reference-case-form .reference-case-attachments')
                    picker.select_option(document['document_id'])
                    page.locator('#reference-case-detail').get_by_role('button',name='Save human update').click()
                    wait_state('__cirpTestState.referenceCaseSelected.version===1')
                    detail=page.locator('#reference-case-detail').inner_text()
                    assert page.locator('#reference-case-detail .reference-case-form input').input_value()=='field-engineer'
                    assert document['name'] in detail
                    page.locator('#reference-case-detail summary').click()
                    assert 'Synthetic site note.' in page.locator('#reference-case-detail details').inner_text()
                    checks.append('Create, assignee, note, and same-project uploaded-document attachment persist through the visible case editor')
                    print('SMOKE followup: creating saved result',flush=True)
                    followup_run,followup_result=_followup_result(db,client,project,document,questions[0][0])
                    print('SMOKE followup: refreshing results',flush=True)
                    page.evaluate('void loadReferenceResults()');wait_state('__cirpTestState.referenceResults.length===4')
                    print('SMOKE followup: linking',flush=True)
                    page.evaluate('renderReferenceCaseDetail()')
                    link_form=page.locator('#reference-case-detail .reference-case-followup-form')
                    wait_state("document.querySelector('#reference-case-detail .reference-case-followup-form select')")
                    visible_result_ids=link_form.locator('select').nth(0).locator('option').evaluate_all(
                        "options => options.map(option => option.value)")
                    assert followup_result in visible_result_ids,visible_result_ids
                    link_form.locator('select').nth(0).select_option(followup_result)
                    link_form.locator('select').nth(1).select_option(document['document_id'])
                    link_form.get_by_role('button',name='Link saved new answer').click()
                    wait_state("__cirpTestState.referenceCaseSelected.followups.length===1")
                    print('SMOKE followup: refreshing cases',flush=True)
                    page.locator('#reference-cases-refresh').click();page.wait_for_timeout(80)
                    wait_state("__cirpTestState.referenceCases.some(item=>item.case_id===__cirpTestState.referenceCaseSelected.case_id&&item.status==='IN_REVIEW')")
                    assert page.locator('#reference-cases .reference-case-card').count()>=1
                    page.locator('#reference-cases .reference-case-card button').first.click()
                    wait_state("document.querySelector('#reference-case-detail .reference-case-form')")
                    checks.append('A saved new-run answer can be linked to its actual sent attachment and remains visible after refresh')
                    page.locator('#reference-case-detail select').first.select_option('RESOLVED')
                    page.locator('#reference-case-detail textarea').nth(1).fill('Synthetic human resolution.')
                    page.locator('#reference-case-detail').get_by_role('button',name='Save human update').click()
                    wait_state("__cirpTestState.referenceCaseSelected.status==='RESOLVED'")
                    page.locator('#reference-case-detail select').first.select_option('OPEN')
                    page.locator('#reference-case-detail').get_by_role('button',name='Save human update').click()
                    wait_state("__cirpTestState.referenceCaseSelected.status==='OPEN'")
                    assert page.locator('#reference-case-detail textarea').nth(1).input_value()==''
                    page.locator('#reference-case-detail summary').last.click()
                    assert 'Synthetic human resolution.' in page.locator('#reference-case-detail details').last.inner_text()
                    checks.append('Resolve then reopen clears current response while preserving the old response in history')
                    before=len(requests);draft=page.locator('#reference-case-detail textarea').nth(0);draft.fill('Draft survives display language update.')
                    page.evaluate("CIRPI18n.setLanguage('en')")
                    assert draft.input_value()=='Draft survives display language update.' and len(requests)==before
                    checks.append('Display-language update preserves active editor input and makes no request')
                    for width in (320,390):
                        page.set_viewport_size({'width':width,'height':844});page.wait_for_timeout(40)
                        assert page.evaluate('document.documentElement.scrollWidth')<=width
                    checks.append('Case editor has no document-level horizontal overflow at 320px and 390px')
                    # Hold an old-project case response while changing the selected project.
                    page.evaluate('''() => { const original=window.fetch;let hold=true;window.fetch=async(...args)=>{
                      if(hold&&String(args[0]).includes('/reference-cases?')){hold=false;await new Promise(resolve=>window.__releaseCaseList=resolve);}return original(...args);};}''')
                    page.set_viewport_size({'width':1440,'height':1000});page.locator('#reference-cases-refresh').click()
                    page.wait_for_timeout(30);page.locator('#project-select').select_option(other['id']);page.evaluate('window.__releaseCaseList()')
                    wait_state(f"__cirpTestState.project==='{other['id']}'")
                    page.wait_for_timeout(80)
                    assert page.locator('#reference-cases').inner_text()=='' and page.locator('#reference-case-detail').inner_text()==''
                    checks.append('A delayed old-project case response cannot render after a project switch')
                    assert not errors,errors
                finally:
                    context.close();browser.close()
    report_path.write_text('\n'.join([
        '# Field cases browser validation — 2026-10-03','',
        '## Result','',f'- PASS: {len(checks)} checks; model calls: 0; external network requests: 0.','',
        '## Actual browser path','',
        '- Used real Chromium through Playwright at a synthetic `https://field-cases.test` origin.',
        '- Every request was intercepted and served by FastAPI `TestClient` over a temporary SQLite database; it did not reach a LAN, public site, `.local` customer service, or a provider.',
        '- Covered four source statuses, case create/update/attachment/resolve/reopen, project-switch stale response protection, language update, and 320/390px layout.',
        '', '## Offline DOM boundary','',
        '- This dedicated case acceptance uses the actual Chromium route above. The existing `scripts/browser_i18n_smoke.py --offline-dom` remains a separate DOM bridge check; it does not verify browser navigation, CSP, or native browser storage.',
        '', '## Checks','',*['- '+check for check in checks],'',
        '## Not verified','',
        '- Native desktop installation routing, real remote HTTP, live provider behavior, customer documents, construction correctness, and post-upload snapshot/re-answer relationships.',
    ])+'\n',encoding='utf-8')
    print(f'PASS {len(checks)} field-case browser checks; report={report_path}')

if __name__=='__main__':main()
