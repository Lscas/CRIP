"""Production v9 UI acceptance with isolated, offline Chromium/TestClient traffic."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(report_path: Path | None = None):
    from fastapi.testclient import TestClient
    from playwright.sync_api import sync_playwright
    from app.main import create_app
    from app.settings import Settings
    from tests.app.conftest import upload
    from tests.app.test_evidence_loop import _empty_answer, _provider_answer
    from tests.app.test_reference_case_upload_flow import _reference_run
    from tests.app.test_reference_v9_evaluations import _channel

    checks, requests, errors = [], [], []
    capabilities = {'mode': 'enabled'}
    with tempfile.TemporaryDirectory(prefix='cirp-v9-browser-') as temporary:
        app = create_app(Settings(Path(temporary), start_worker=False,
                                 reference_layout_enabled=True,
                                 allowed_hosts=('v9-browser.test', 'testserver')))
        with TestClient(app, headers={'X-CIRP-Client': 'browser'}) as client, sync_playwright() as playwright:
            project = client.post('/api/projects', json={'name': 'Synthetic v9 project'}).json()
            other = client.post('/api/projects', json={'name': 'Synthetic other project'}).json()
            upload(client, project['id'], 'initial.txt', b'No approved color is provided in this note.')
            initial = _reference_run(client, project['id'])

            def decision(_payload, content):
                source = next((row for row in content['evidence']
                               if 'The approved color is blue.' in row['text']), None)
                if source:
                    return _provider_answer(source['evidence_ref'])
                return {'status': 'NEED_USER_INPUT', 'reason_code': 'MISSING_PROJECT_FILE',
                        'missing_facts': ['A synthetic field confirmation is missing.'],
                        'requests': [], 'answer': _empty_answer()}

            calls = _channel(client, decision)
            paths = (shutil.which('chromium'), shutil.which('google-chrome'),
                     r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                     r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
            executable = next((path for path in paths if path and Path(path).is_file()), None)
            browser = playwright.chromium.launch(**({'executable_path': executable} if executable else {}))
            context = browser.new_context(viewport={'width': 390, 'height': 844})

            def route(intercept):
                request = intercept.request
                parsed = urlsplit(request.url)
                if parsed.hostname != 'v9-browser.test':
                    intercept.abort()
                    return
                if parsed.path.startswith('/api/'):
                    body = None
                    if request.post_data_buffer and 'application/json' in request.headers.get('content-type', ''):
                        body = json.loads(request.post_data_buffer)
                    requests.append((request.method, parsed.path, body))
                headers = {key: value for key, value in request.headers.items()
                           if key.lower() not in {'host', 'content-length'}}
                response = client.request(request.method, request.url, headers=headers,
                                          content=request.post_data_buffer or b'')
                if parsed.path == '/api/settings' and capabilities['mode'] != 'enabled':
                    data = response.json()
                    if capabilities['mode'] == 'missing':
                        data['capabilities'].pop('reference_layout_v9', None)
                    else:
                        data['capabilities']['reference_layout_v9']['enabled'] = False
                    intercept.fulfill(status=response.status_code, content_type='application/json',
                                      body=json.dumps(data))
                    return
                intercept.fulfill(status=response.status_code,
                                  headers={key: value for key, value in response.headers.items()
                                           if key.lower() not in {'content-length', 'content-encoding'}},
                                  body=response.content)

            context.route('**/*', route)
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on('pageerror', lambda error: errors.append(str(error)))

            def loaded():
                page.goto('https://v9-browser.test/')
                page.wait_for_function('() => Boolean(state.settings && state.project && !state.refreshPromise)')
                page.evaluate("CIRPI18n.setLanguage('en')")
                page.locator('#project-select').select_option(project['id'])
                page.wait_for_function('() => Boolean(state.referenceKnowledge?.available)')

            def wait_idle():
                page.wait_for_function('() => !state.referenceAsking && !state.referencePreviewing && !state.asking && !state.referenceEvaluationBusy && !state.caseFlowBusy')

            def hold_response(suffix):
                page.evaluate("""suffix => {
                    delete window.__v9Release;
                    window.__v9OriginalFetch=window.fetch;
                    window.fetch=async (...args) => {
                        const response=await window.__v9OriginalFetch(...args);
                        if(String(args[0]).endsWith(suffix))
                            return new Promise(resolve=>{window.__v9Release=()=>resolve(response);});
                        return response;
                    };
                }""", suffix)

            def release_response():
                page.evaluate('() => {window.fetch=window.__v9OriginalFetch;window.__v9Release();delete window.__v9Release;}')
                wait_idle()

            def advance_reference_scope(snapshot=False):
                if snapshot:
                    upload(client, project['id'], 'scope-note.txt', b'Synthetic scope note, with no approved color.')
                run = _reference_run(client, project['id'])
                page.evaluate('async () => {await refreshReferenceWorkspace();}')
                return run

            try:
                for mode in ('missing', 'disabled'):
                    capabilities['mode'] = mode
                    before = len(requests)
                    loaded()
                    for selector in ('project-question-profile', 'reference-question-profile', 'reference-evaluation-profile'):
                        assert page.locator('#' + selector).input_value() == ''
                        assert page.locator('#' + selector + ' option[value="FLASH_NONE"]').evaluate('(element) => element.hidden')
                    assert not any(method != 'GET' for method, _, _ in requests[before:])
                    assert not calls
                    checks.append(f'{mode} capability hides v9 and page initialization has zero writes or model calls')

                capabilities['mode'] = 'enabled'
                loaded()
                page.locator('#reference-question-profile').select_option('FLASH_NONE')
                page.locator('#reference-question').fill('What approved color applies?')
                page.evaluate("""() => {
                    window.__v9OriginalFetch=window.fetch;
                    window.fetch=async (...args) => {
                        const response=await window.__v9OriginalFetch(...args);
                        if(String(args[0]).endsWith('/questions-v3/preview'))
                            return new Promise(resolve=>{window.__v9Release=()=>resolve(response);});
                        return response;
                    };
                }""")
                page.locator('#reference-preview').click()
                page.wait_for_function("() => typeof window.__v9Release==='function'")
                page.locator('#project-select').select_option(other['id'])
                page.wait_for_function(f"() => state.project==='{other['id']}'")
                page.evaluate('() => {window.fetch=window.__v9OriginalFetch;window.__v9Release();}')
                wait_idle()
                assert page.evaluate('state.referenceV9Preview') is None
                assert not calls
                checks.append('a real pending preview response cannot reattach proof after project switch')
                page.locator('#project-select').select_option(project['id'])
                page.wait_for_function('() => Boolean(state.referenceKnowledge?.available)')
                page.locator('#reference-question-profile').select_option('FLASH_NONE')
                for change in ('run', 'snapshot'):
                    hold_response('/questions-v3/preview')
                    page.locator('#reference-preview').click()
                    page.wait_for_function("() => typeof window.__v9Release==='function'")
                    previous = initial
                    initial = advance_reference_scope(snapshot=change == 'snapshot')
                    assert initial['id'] != previous['id']
                    if change == 'snapshot':
                        assert initial['snapshot_id'] != previous['snapshot_id']
                    release_response()
                    assert page.evaluate('state.referenceV9Preview') is None
                    assert page.locator('#reference-ask').is_disabled()
                    assert not calls
                    checks.append(f'pending preview is discarded after actual same-project {change} replacement')
                assert page.locator('#reference-ask').is_disabled()
                before = len(requests)
                page.locator('#reference-question-form').evaluate("form => form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))")
                wait_idle()
                assert not any(method == 'POST' for method, _, _ in requests[before:])
                checks.append('v9 Reference submission without a preview is blocked before POST')
                page.locator('#reference-preview').click()
                page.wait_for_function('() => Boolean(state.referenceV9Preview)')
                assert not calls
                page.locator('#reference-question').fill('What approved color applies after editing?')
                assert page.evaluate('state.referenceV9Preview') is None
                assert page.locator('#reference-ask').is_disabled()
                checks.append('editing a question clears its preview without a model call')
                page.locator('#reference-question').fill('What approved color applies?')
                page.locator('#reference-preview').click()
                page.wait_for_function('() => Boolean(state.referenceV9Preview)')
                page.locator('#reference-question-profile').select_option('FLASH_LOW')
                assert page.evaluate('state.referenceV9Preview') is None
                page.locator('#reference-preview').click()
                page.wait_for_function('() => Boolean(state.referenceV9Preview)')
                page.locator('#reference-ask').click()
                wait_idle()
                assert len(calls) == 1
                saved = client.get(f"/api/projects/{project['id']}/reference-results").json()
                # The list is a public envelope; do not infer the result from visible text alone.
                saved_items = saved.get('items', saved.get('results', [])) if isinstance(saved, dict) else saved
                assert saved_items, saved
                checks.append('Reference v9 named Low preview and answer reach the real API and save a result')

                page.locator('#project-question-profile').select_option('PRO')
                page.locator('#project-question').fill('What approved color applies to this ordinary question?')
                page.locator('#project-preview').click()
                page.wait_for_function('() => Boolean(state.projectV9Preview)')
                page.locator('#ask-submit').click()
                wait_idle()
                assert len(calls) == 2
                asks = [body for method, path, body in requests if method == 'POST' and path.endswith('/questions-v3')]
                assert asks[-1]['profile_id'] == 'PRO'
                assert asks[-1]['run_id'] == initial['id']
                assert asks[-1]['preview_proof']['selector_version'] == 'literal-page-selector-9'
                checks.append('ordinary v9 question works using Reference knowledge without a legacy analysis')

                page.locator('#reference-evaluation-profile').select_option('FLASH_NONE')
                page.locator('#reference-evaluation-name').fill('Synthetic v9 browser evaluation')
                page.locator('#reference-evaluation-questions').fill('What approved color applies in this evaluation?')
                page.locator('#reference-evaluation-create').click()
                wait_idle()
                created = [body for method, path, body in requests if method == 'POST' and path.endswith('/reference-evaluations')][-1]
                assert created['selector_version'] == 'literal-page-selector-9'
                assert created['profile_id'] == 'FLASH_NONE'
                assert len(calls) == 2
                checks.append('evaluation creation freezes v9 and its named profile without calling a model')
                evaluation_card = page.locator('#reference-evaluations .reference-evaluation-card').first
                assert evaluation_card.get_by_role('button', name='Clone for selected profile').is_disabled()
                evaluation_card.get_by_test_id('reference-v9-clone-profile').select_option('PRO')
                evaluation_card.get_by_role('button', name='Clone for selected profile').click()
                wait_idle()
                clone = [body for method, path, body in requests if method == 'POST' and path.endswith('/clone')][-1]
                assert clone == {'profile_id': 'PRO'} and len(calls) == 2
                assert page.evaluate('state.referenceEvaluations[0].profile.profile_id') == 'PRO'
                assert page.evaluate('state.referenceEvaluations[0].selector_version') == 'literal-page-selector-9'
                checks.append('v9 evaluation clones to an explicitly selected named Pro profile with zero model calls')

                card = page.locator('#reference-results .reference-result-card').filter(has_text='What approved color applies?')
                card.get_by_role('button', name='Send to human follow-up').click()
                page.wait_for_function('() => Boolean(state.referenceCaseSelected)')
                case_id = page.evaluate('state.referenceCaseSelected.case_id')
                original_result_id = page.evaluate('state.referenceCaseSelected.source.result_id')
                original = client.get('/api/reference-results/' + original_result_id).json()
                # This temporary fixture is generated test input, not a customer file.
                attachment = Path(temporary) / 'field-confirmation.txt'
                attachment.write_text('The approved color is blue.', encoding='utf-8')
                flow = page.locator('#reference-case-detail .reference-case-followup-flow')
                flow.locator('input[type=file]').set_input_files(str(attachment))
                flow.get_by_role('button', name='Upload and attach supplement').click()
                page.wait_for_function('() => state.referenceCaseSelected.attachments.length===1 && !state.caseFlowBusy')
                flow.get_by_role('button', name='Prepare new Reference run').click()
                page.wait_for_function('() => Boolean(state.referenceKnowledge.active_run_id)')
                new_run_id = page.evaluate('state.referenceKnowledge.active_run_id')
                app.state.runner.process(new_run_id)
                flow.get_by_role('button', name='Refresh available runs').click()
                wait_idle()
                # Profile picker is the only select containing canonical profile IDs.
                picker = flow.get_by_test_id('reference-case-v9-profile')
                picker.select_option('FLASH_NONE')
                flow.get_by_role('button', name='Preview pages for the new run').click()
                page.wait_for_function("() => !document.querySelector('#reference-case-detail .reference-case-followup-flow button:last-child').disabled")
                before = len(requests)
                flow.get_by_role('button', name='Answer and link this run').click()
                wait_idle()
                current = client.get('/api/reference-cases/' + case_id).json()
                assert current['status'] == 'IN_REVIEW' and current['resolution'] == ''
                assert len(current['followups']) == 1, current
                assert current['followups'][0]['run_id'] == new_run_id
                assert client.get('/api/reference-results/' + original_result_id).json() == original
                assert len(calls) == 3
                assert not any(method == 'POST' and path.endswith('/questions-v3/preview') for method, path, _ in requests[before:])
                checks.append('v9 supplement uploads, parses, previews once, answers and links while preserving original result and human state')

                for slot in ('project', 'reference'):
                    for change in ('profile', 'run'):
                        profile_selector = f'#{slot}-question-profile'
                        question_selector = f'#{slot}-question'
                        preview_selector = '#project-preview' if slot == 'project' else '#reference-preview'
                        ask_selector = '#ask-submit' if slot == 'project' else '#reference-ask'
                        proof_name = 'projectV9Preview' if slot == 'project' else 'referenceV9Preview'
                        question = f'What approved color applies to {slot} guard {change}?'
                        page.locator(profile_selector).select_option('FLASH_NONE')
                        page.locator(question_selector).fill(question)
                        page.locator(preview_selector).click()
                        page.wait_for_function(f'() => Boolean(state.{proof_name})')
                        hold_response('/questions-v3')
                        page.locator(ask_selector).click()
                        page.wait_for_function("() => typeof window.__v9Release==='function'")
                        assert page.locator(profile_selector).is_disabled()
                        if change == 'profile':
                            # Inject the same late event a queued UI update could deliver.
                            page.locator(profile_selector).evaluate("node=>{node.value='PRO';node.dispatchEvent(new Event('change'));}")
                        else:
                            advance_reference_scope()
                        output_selector = '#question-results' if slot == 'project' else '#reference-results'
                        before_output = page.locator(output_selector).inner_text()
                        release_response()
                        assert page.locator(question_selector).input_value() == question
                        assert page.locator(output_selector).inner_text() == before_output
                        assert page.evaluate(f'state.{proof_name}') is None
                        checks.append(f'pending {slot} answer does not render or clear a draft after {change} changes')

                page.locator('#reference-evaluation-profile').select_option('FLASH_NONE')
                page.locator('#reference-evaluation-name').fill('Pending evaluation draft')
                page.locator('#reference-evaluation-questions').fill('What approved color applies to the pending task?')
                evaluations_before = page.evaluate('state.referenceEvaluations.length')
                hold_response('/reference-evaluations')
                page.locator('#reference-evaluation-create').click()
                page.wait_for_function("() => typeof window.__v9Release==='function'")
                assert page.locator('#reference-evaluation-profile').is_disabled()
                page.locator('#reference-evaluation-profile').evaluate("node=>{node.value='PRO';node.dispatchEvent(new Event('change'));}")
                release_response()
                assert page.locator('#reference-evaluation-name').input_value() == 'Pending evaluation draft'
                assert page.evaluate('state.referenceEvaluations.length') == evaluations_before
                checks.append('pending evaluation creation keeps the changed-profile draft and does not attach stale UI state')

                page.locator('#project-select').select_option(other['id'])
                page.wait_for_function(f"() => state.project==='{other['id']}'")
                assert page.evaluate('state.projectV9Preview') is None
                assert page.evaluate('state.referenceV9Preview') is None
                checks.append('project switch clears both ordinary and Reference preview identities')
                assert page.evaluate('document.documentElement.scrollWidth') <= 390
                assert not errors, errors
                checks.append('390px production layout and browser runtime have no page errors')
            finally:
                context.close()
                browser.close()
    result = {'checks': checks, 'model_calls': len(calls), 'external_calls': 0,
              'boundary': 'Synthetic TestClient/MockTransport only; not live quality or phone/network acceptance.'}
    output = report_path or ROOT / 'reports/local/reference-v9-browser-smoke-2026-10-04.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS {len(checks)} production Chromium checks; synthetic calls={len(calls)}; report={output}')


if __name__ == '__main__':
    main()
