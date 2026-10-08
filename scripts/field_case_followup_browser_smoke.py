"""Offline Chromium acceptance of the production supplemental-case Web flow.

Real production assets are served by FastAPI TestClient against synthetic data.
No external server, customer database or model/provider is contacted.
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


def main(report_path: Path | None = None) -> None:
    from fastapi.testclient import TestClient
    from playwright.sync_api import sync_playwright
    from app.main import create_app
    from app.settings import Settings
    from tests.app.conftest import upload
    from tests.app.test_evidence_loop import _empty_answer, _provider_answer
    from tests.app.test_reference_case_upload_flow import QUESTION, _reference_run, _ask
    from tests.app.test_reference_profile_comparisons import _channel

    checks=[]; errors=[]; api_requests=[]
    with tempfile.TemporaryDirectory(prefix='cirp-stage-ui-') as tmp:
        app=create_app(Settings(Path(tmp),start_worker=False,allowed_hosts=('case-followup.test','testserver')))
        with TestClient(app,headers={'X-CIRP-Client':'browser'}) as client, sync_playwright() as playwright:
            first=client.post('/api/projects',json={'name':'Synthetic staged project'}).json()
            second=client.post('/api/projects',json={'name':'Synthetic switched project'}).json()
            upload(client,first['id'],'initial.txt',b'No approved color appears in this initial file.')
            initial_run=_reference_run(client,first['id'])
            def decision(_payload,content):
                if not any('approved color is blue' in item['text'] for item in content['evidence']):
                    return {'status':'NEED_USER_INPUT','reason_code':'MISSING_PROJECT_FILE',
                            'missing_facts':['A field confirmation document is required.'],
                            'requests':[],'answer':_empty_answer()}
                supplied=next(item for item in content['evidence'] if 'approved color is blue' in item['text'])
                return _provider_answer(supplied['evidence_ref'])
            calls=_channel(client,decision)
            conflict={'armed':True,'changed_case':None}
            preview_change={'armed':False}
            link_fault={'mode':None,'case_id':None,'post_seen':False,'refresh_failures':0}
            initial=_ask(client,first['id'],initial_run['id'])
            assert initial['status']=='NEED_USER_INPUT' and len(calls)==1
            alternate_response=client.post(f"/api/projects/{first['id']}/questions-v3",json={
                'run_id':initial_run['id'],'question':'Alternate local case'})
            assert alternate_response.status_code==200,alternate_response.text
            alternate_result=alternate_response.json()
            assert alternate_result['status']=='NEED_USER_INPUT' and len(calls)==2
            alternate=client.post(f"/api/projects/{first['id']}/reference-cases",json={
                'run_id':initial_run['id'],'question':'Alternate local case',
                'result_id':alternate_result['result_id'],'note':'Synthetic switch target.'}).json()
            assert alternate.get('case_id'),alternate
            supplement=Path(tmp)/'field-confirmation.txt'
            supplement.write_text('The approved color is blue.',encoding='utf-8')
            candidates=(shutil.which('chromium'),shutil.which('google-chrome'),
                        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
            executable=next((str(Path(path)) for path in candidates if path and Path(path).is_file()),None)
            browser=playwright.chromium.launch(**({'executable_path':executable} if executable else {}))
            context=browser.new_context(viewport={'width':390,'height':844})
            def route(route):
                request=route.request; parsed=urlsplit(request.url)
                if parsed.hostname!='case-followup.test':
                    route.abort(); return
                if parsed.path.startswith('/api/'):
                    api_requests.append((request.method,parsed.path))
                if request.method=='POST' and parsed.path.endswith('/follow-up-results') and conflict['armed']:
                    conflict['armed']=False
                    case_id=parsed.path.split('/')[-2]
                    current=client.get(f'/api/reference-cases/{case_id}').json()
                    changed=client.post(f'/api/reference-cases/{case_id}/update',json={
                        'expected_version':current['version'],'note':'Concurrent human note in the synthetic browser test.'})
                    assert changed.status_code==200,changed.text
                    conflict['changed_case']=changed.json()
                headers={key:value for key,value in request.headers.items() if key.lower() not in {'host','content-length'}}
                response=client.request(request.method,request.url,headers=headers,content=request.post_data_buffer or b'')
                if request.method=='GET' and parsed.path.startswith('/api/reference-results/') and link_fault['mode'] in {'envelope_failed','unknown_without_envelope'}:
                    link_fault['refresh_failures']+=1
                    route.fulfill(status=503,content_type='application/json',body='{"detail":"Synthetic saved result unavailable"}'); return
                if request.method=='POST' and parsed.path==f"/api/reference-cases/{link_fault['case_id']}/follow-up-results":
                    assert response.status_code==200,response.text
                    link_fault['post_seen']=True
                    if link_fault['mode'] in {'lost_response','unknown_response','unknown_without_envelope'}:
                        route.abort('failed'); return
                if request.method=='GET' and link_fault['post_seen']:
                    is_case_read=parsed.path==f"/api/reference-cases/{link_fault['case_id']}"
                    is_list_read=parsed.path.endswith('/reference-cases') or parsed.path.endswith('/reference-results')
                    if (link_fault['mode']=='refresh_failed' and is_list_read) or (link_fault['mode'] in {'unknown_response','unknown_without_envelope'} and (is_case_read or is_list_read)):
                        link_fault['refresh_failures']+=1
                        route.fulfill(status=503,content_type='application/json',body='{"detail":"Synthetic refresh unavailable"}'); return
                if request.method=='POST' and parsed.path.endswith('/questions-v3/preview') and preview_change['armed']:
                    preview_change['armed']=False
                    changed=response.json()
                    selection=changed.get('page_selection',changed)
                    selection['selection_id']='SYNTHETIC_CHANGED_SELECTION'
                    route.fulfill(status=200,content_type='application/json',body=json.dumps(changed)); return
                route.fulfill(status=response.status_code,headers={key:value for key,value in response.headers.items() if key.lower() not in {'content-length','content-encoding'}},body=response.content)
            context.route('**/*',route)
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            page.set_default_timeout(5000)
            try:
                page.goto('https://case-followup.test/')
                print('stage: page loaded',flush=True)
                page.wait_for_function("() => document.querySelector('#version').textContent.startsWith('v')")
                page.evaluate('window.__cirpTestState=state')
                page.locator('#project-select').select_option(first['id'])
                print('stage: project selected',flush=True)
                page.wait_for_function(f"() => window.__cirpTestState.project==='{first['id']}'")
                # The production assets, established its guarded flow state.
                assert page.evaluate("'caseFlowByCase' in window.__cirpTestState")
                assert page.evaluate("typeof window.caseFlowBlocked==='function'")
                checks.append('production assets load through real Chromium and expose guarded per-case flow state')
                card=page.locator('#reference-results .reference-result-card').filter(has_text=QUESTION)
                card.get_by_role('button',name='Send to human follow-up').click()
                print('stage: case create clicked',flush=True)
                page.wait_for_function('() => window.__cirpTestState.referenceCaseSelected!==null')
                flow=page.locator('#reference-case-detail .reference-case-followup-flow')
                assert flow.get_by_role('button',name='Answer and link this run').is_disabled()
                checks.append('Answer is disabled before a frozen preview')
                flow.locator('input[type=file]').set_input_files(str(supplement))
                flow.get_by_role('button',name='Upload and attach supplement').click()
                print('stage: upload clicked',flush=True)
                page.wait_for_function("() => window.__cirpTestState.referenceCaseSelected.attachments.length===1")
                page.wait_for_function("() => document.querySelector('#reference-case-detail .reference-case-followup-flow')")
                flow=page.locator('#reference-case-detail .reference-case-followup-flow')
                # No analysis/model call has happened during upload and attachment.
                assert len(calls)==2
                duplicate_case=page.evaluate('state.referenceCaseSelected.case_id')
                duplicate_before=client.get(f'/api/reference-cases/{duplicate_case}').json()
                update_count=sum(method=='POST' and path==f'/api/reference-cases/{duplicate_case}/update' for method,path in api_requests)
                flow.locator('input[type=file]').set_input_files(str(supplement))
                flow.get_by_role('button',name='Upload and attach supplement').click()
                page.wait_for_function('() => !state.caseFlowBusy && !state.uploading')
                assert client.get(f'/api/reference-cases/{duplicate_case}').json()==duplicate_before
                assert sum(method=='POST' and path==f'/api/reference-cases/{duplicate_case}/update' for method,path in api_requests)==update_count
                assert page.evaluate('state.caseFlowByCase[state.referenceCaseSelected.case_id].selectedIds')==[duplicate_before['attachments'][0]['document_id']]
                assert len(calls)==2
                checks.append('re-uploading an already attached duplicate preserves the exact case, version and history and makes no additive update or model call')
                flow.get_by_role('button',name='Prepare new Reference run').click()
                print('stage: prepare clicked',flush=True)
                page.wait_for_function("() => Boolean(window.__cirpTestState.referenceKnowledge.active_run_id)")
                followup_id=page.evaluate('window.__cirpTestState.referenceKnowledge.active_run_id')
                app.state.runner.process(followup_id)
                print('stage: processed',flush=True)
                flow.get_by_role('button',name='Refresh available runs').click()
                print('stage: refreshed runs',flush=True)
                page.wait_for_function("() => window.__cirpTestState.referenceRuns.some(run=>run.id==='"+followup_id+"'&&['PARTIAL','COMPLETED'].includes(run.status))")
                page.wait_for_function('() => !state.caseFlowBusy')
                assert flow.locator('select').nth(1).input_value()==followup_id
                assert flow.locator('select').nth(1).locator('option').count()==1
                checks.append('refresh and re-render preserve the prepared run option and selection')
                flow.get_by_role('button',name='Preview pages for the new run').click()
                print('stage: preview clicked',flush=True)
                page.wait_for_function("() => !document.querySelector('#reference-case-detail .reference-case-followup-flow button:last-child').disabled")
                assert len(calls)==2
                # Exercise the reverse direction of the lock with a real pending
                # browser fetch. No model configuration or legacy run is changed:
                # the pending request receives a synthetic network failure.
                for entry,event_type,path in [('model-settings-form','submit','/api/model-settings'),('start','click',f"/api/projects/{first['id']}/analysis-runs")]:
                    case_lock_before=client.get(f'/api/reference-cases/{duplicate_case}').json()
                    request_index=len(api_requests)
                    page.evaluate('''([id,eventType,heldPath]) => {
                        window.__stageOriginalFetch=window.fetch;
                        window.__stageOperationHeld=false;window.__stageOperationWrites=[];
                        window.fetch=async (...args) => {
                            const method=(args[1]?.method||'GET').toUpperCase(),path=String(args[0]);
                            if(!['GET','HEAD'].includes(method))window.__stageOperationWrites.push({path,method});
                            if(method==='POST'&&path===heldPath){
                                window.__stageOperationHeld=true;
                                await new Promise(resolve=>window.__releaseStageOperation=resolve);
                                return new Response(JSON.stringify({detail:'Synthetic operation unavailable'}),{status:503,headers:{'Content-Type':'application/json'}});
                            }
                            return window.__stageOriginalFetch(...args);
                        };
                        const target=$(id);target.disabled=false;
                        target.dispatchEvent(new Event(eventType,{bubbles:true,cancelable:true}));
                    }''',[entry,event_type,path])
                    page.wait_for_function('() => window.__stageOperationHeld')
                    assert page.evaluate('state.caseFlowBusy'),entry
                    # All of these controls have valid inputs/a valid frozen preview;
                    # only the in-flight operation should prevent their writes.
                    flow.locator('input[type=file]').set_input_files(str(supplement))
                    page.evaluate('''() => {
                        const flow=document.querySelector('#reference-case-detail .reference-case-followup-flow');
                        for(const button of flow.querySelectorAll('button')){
                            button.disabled=false;button.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
                        }
                        for(const form of document.querySelectorAll('#reference-case-detail form')){
                            form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
                        }
                    }''')
                    assert page.evaluate('window.__stageOperationWrites')==[{'path':path,'method':'POST'}]
                    assert not [(method,url) for method,url in api_requests[request_index:] if method not in ('GET','HEAD')]
                    assert client.get(f'/api/reference-cases/{duplicate_case}').json()==case_lock_before
                    assert len(calls)==2
                    page.evaluate('window.__releaseStageOperation()')
                    page.wait_for_function('() => !state.caseFlowBusy')
                    page.evaluate('window.fetch=window.__stageOriginalFetch')
                    assert flow.get_by_role('button',name='Answer and link this run').is_enabled()
                    checks.append(f'{entry} holds its lock while its browser request is pending, prevents all valid case actions, and releases the lock on failure')
                flow.get_by_role('button',name='Answer and link this run').click()
                print('stage: answer clicked',flush=True)
                page.wait_for_function("() => window.__cirpTestState.referenceResults.length===3")
                print(f'stage: calls after rejected link={len(calls)}',flush=True)
                page.wait_for_function('() => !state.caseFlowBusy && !state.uploading')
                assert len(calls)==3
                assert conflict['changed_case'] is not None
                assert page.evaluate('state.referenceCaseSelected.version')==conflict['changed_case']['version']
                assert not page.evaluate('state.referenceCaseSelected.followups')
                assert flow.get_by_role('button',name='Answer and link this run').is_disabled()
                page.evaluate('renderReferenceResults()')
                assert page.locator('#reference-results .reference-result-card').count()==3
                checks.append('the conflict recovery result remains a complete saved-result object that can be rendered normally')
                paid_requests_before=sum(method=='POST' and path.endswith('/questions-v3') for method,path in api_requests)
                # A failed link leaves the just-saved answer in the existing manual recovery picker.
                manual=page.locator('#reference-case-detail .reference-case-followup-form')
                manual.locator('select').nth(0).select_option(index=0)
                manual.locator('select').nth(1).select_option(index=0)
                manual.get_by_role('button',name='Link saved new answer').click()
                page.wait_for_function('() => window.__cirpTestState.referenceCaseSelected.followups.length===1')
                page.wait_for_function('() => !state.caseFlowBusy && !state.uploading')
                assert len(calls)==3
                assert sum(method=='POST' and path.endswith('/questions-v3') for method,path in api_requests)==paid_requests_before
                checks.append('a real case-version conflict refreshes the latest case, disables Answer, and recovers by manual link without another model call')
                checks.append('browser upload, local Reference run, frozen preview, MockTransport V3 answer, and link follow the staged chain without a fabricated follow-up result')
                # Seed only an incomplete upload record.  The browser owns the actual
                # resumed chunk/complete request and must return its document ID.
                resumed=client.post(f"/api/projects/{first['id']}/uploads",json={
                    'name':'resume.txt','size':len(b'resume bytes')}).json()
                resumed_id=page.evaluate('''async ([project,uploadId]) => {
                    const bytes=new TextEncoder().encode('resume bytes');
                    const file=new File([bytes],'resume.txt',{type:'text/plain',lastModified:1700000000000});
                    const key=['cirp-upload',project,file.name,file.size,file.lastModified].join(':');
                    localStorage.setItem(key,uploadId);
                    const result=await uploadFiles([file],{projectId:project,refreshAfter:false});
                    return result.map(item=>item.document_id);
                }''',[first['id'],resumed['id']])
                assert len(resumed_id)==1 and resumed_id[0]
                # A finished resumable record is returned without a second chunk or model call.
                complete_id=page.evaluate('''async ([project,uploadId]) => {
                    const bytes=new TextEncoder().encode('resume bytes');
                    const file=new File([bytes],'resume.txt',{type:'text/plain',lastModified:1700000000000});
                    localStorage.setItem(['cirp-upload',project,file.name,file.size,file.lastModified].join(':'),uploadId);
                    const result=await uploadFiles([file],{projectId:project,refreshAfter:false});
                    return result.map(item=>item.document_id);
                }''',[first['id'],resumed['id']])
                assert complete_id==resumed_id and len(calls)==3
                # A same-content second browser upload exercises the API's duplicate completion branch.
                duplicate_id=page.evaluate('''async project => {
                    const file=new File([new TextEncoder().encode('resume bytes')],'duplicate.txt',{type:'text/plain',lastModified:1700000000001});
                    const result=await uploadFiles([file],{projectId:project,refreshAfter:false});
                    return result.map(item=>item.document_id);
                }''',first['id'])
                assert duplicate_id==resumed_id and len(calls)==3
                checks.append('browser resumes an incomplete upload and returns document IDs for COMPLETE and DUPLICATE recovery paths without a model call')
                active_case=page.evaluate('window.__cirpTestState.referenceCaseSelected.case_id')
                case_before=client.get(f'/api/reference-cases/{active_case}').json()
                page.evaluate(r'''() => {
                  const original=window.fetch.bind(window);
                  window.__stageWrites=[];window.__stageChunkHeld=false;window.__stageHoldArmed=false;
                  window.fetch=async (...args) => {const path=String(args[0]);
                    const method=(args[1]?.method||'GET').toUpperCase();
                    if (!['GET','HEAD'].includes(method)) window.__stageWrites.push({path,method});
                    const response=await original(...args);
                    if (window.__stageHoldArmed && /\/api\/uploads\/[^/]+\/chunk/.test(path)) {
                      window.__stageHoldArmed=false;window.__stageChunkHeld=true;
                      await new Promise(resolve=>window.__releaseStageChunk=resolve);
                    }
                    return response;
                  };
                }''')
                def hold_next_chunk():
                    page.evaluate('''() => {
                        window.__stageWrites=[];window.__stageChunkHeld=false;window.__stageHoldArmed=true;
                    }''')
                def assert_cancelled_upload():
                    page.wait_for_function('() => !state.caseFlowBusy && !state.uploading')
                    writes=page.evaluate('window.__stageWrites')
                    assert len(writes)==2,writes
                    assert writes[0]=={'path':f"/api/projects/{first['id']}/uploads",'method':'POST'},writes
                    assert writes[1]['method']=='PUT' and '/chunk?offset=0' in writes[1]['path'],writes
                    assert not any(any(part in write['path'] for part in (
                        '/complete','/reference-cases/','/analysis-runs','/questions-v3','/follow-up-results'))
                        for write in writes),writes
                    assert len(calls)==3
                # The chunk endpoint has already run when its response is deliberately
                # held. Switch to a different case before releasing that response.
                hold_next_chunk()
                flow=page.locator('#reference-case-detail .reference-case-followup-flow')
                switch_file=Path(tmp)/'switch-case.txt';switch_file.write_text('case switch bytes',encoding='utf-8')
                flow.locator('input[type=file]').set_input_files(str(switch_file))
                flow.get_by_role('button',name='Upload and attach supplement').click()
                page.wait_for_function('() => window.__stageChunkHeld')
                page.evaluate("caseId => void openReferenceCase(caseId)",alternate['case_id'])
                page.wait_for_function(f"() => window.__cirpTestState.referenceCaseSelected?.case_id==='{alternate['case_id']}'")
                page.evaluate('window.__releaseStageChunk()')
                assert_cancelled_upload()
                case_after=client.get(f'/api/reference-cases/{active_case}').json()
                assert case_after==case_before
                assert page.locator('#reference-case-detail .reference-case-followup-flow input[type=file]').is_enabled()
                checks.append('a held in-flight upload followed by case switch performs no old-case attach, complete, prepare, paid question, or link write')
                # Repeat with a real project switch during an already-dispatched chunk.
                page.evaluate("caseId => void openReferenceCase(caseId)",active_case)
                page.wait_for_function(f"() => window.__cirpTestState.referenceCaseSelected?.case_id==='{active_case}'")
                hold_next_chunk()
                flow=page.locator('#reference-case-detail .reference-case-followup-flow')
                project_file=Path(tmp)/'switch-project.txt';project_file.write_text('project switch bytes',encoding='utf-8')
                flow.locator('input[type=file]').set_input_files(str(project_file))
                flow.get_by_role('button',name='Upload and attach supplement').click()
                page.wait_for_function('() => window.__stageChunkHeld')
                page.evaluate("project => void selectProject(project)",second['id'])
                page.wait_for_function(f"() => window.__cirpTestState.project==='{second['id']}'")
                page.evaluate('window.__releaseStageChunk()')
                assert_cancelled_upload()
                assert client.get(f'/api/reference-cases/{active_case}').json()==case_before
                checks.append('a held in-flight upload followed by project switch performs no old-project complete or case write')
                # A separate ordinary success path must display its follow-up immediately.
                page.evaluate('async project => await selectProject(project)',first['id'])
                page.evaluate('async caseId => await openReferenceCase(caseId)',alternate['case_id'])
                normal=Path(tmp)/'normal-confirmation.txt'
                normal.write_text('For the alternate local case, the approved color is blue.',encoding='utf-8')
                flow=page.locator('#reference-case-detail .reference-case-followup-flow')
                flow.locator('input[type=file]').set_input_files(str(normal))
                flow.get_by_role('button',name='Upload and attach supplement').click()
                page.wait_for_function('() => !state.caseFlowBusy && !state.uploading && state.referenceCaseSelected.attachments.length===1')
                flow.get_by_role('button',name='Prepare new Reference run').click()
                page.wait_for_function('() => !state.caseFlowBusy && Boolean(state.referenceKnowledge.active_run_id)')
                normal_run=page.evaluate('state.referenceKnowledge.active_run_id')
                app.state.runner.process(normal_run)
                flow.get_by_role('button',name='Refresh available runs').click()
                page.wait_for_function("() => !state.referenceKnowledge.active_update")
                flow.get_by_role('button',name='Preview pages for the new run').click()
                page.wait_for_function("() => !document.querySelector('#reference-case-detail .reference-case-followup-flow button:last-child').disabled")
                preview_change['armed']=True
                paid_requests_before=sum(method=='POST' and path.endswith('/questions-v3') for method,path in api_requests)
                flow.get_by_role('button',name='Answer and link this run').click()
                page.wait_for_function('() => !state.caseFlowBusy')
                assert not preview_change['armed'] and len(calls)==3
                assert sum(method=='POST' and path.endswith('/questions-v3') for method,path in api_requests)==paid_requests_before
                assert flow.get_by_role('button',name='Answer and link this run').is_disabled()
                assert not client.get(f"/api/reference-cases/{alternate['case_id']}").json()['followups']
                checks.append('changed selection during the final preview prevents the paid question request')
                flow.get_by_role('button',name='Preview pages for the new run').click()
                page.wait_for_function("() => !document.querySelector('#reference-case-detail .reference-case-followup-flow button:last-child').disabled")
                flow.get_by_role('button',name='Answer and link this run').click()
                page.wait_for_function('() => !state.caseFlowBusy && state.referenceCaseSelected.followups.length===1')
                assert len(calls)==4
                normal_case=client.get(f"/api/reference-cases/{alternate['case_id']}").json()
                assert normal_case['status']=='IN_REVIEW' and not normal_case['resolution']
                assert page.locator('#reference-case-detail .reference-case-history summary').filter(has_text='Linked new answers (1)').count()==1
                assert page.locator('#toast').inner_text()=='The new answer is linked and still requires human review.'
                assert flow.get_by_role('button',name='Answer and link this run').is_disabled()
                assert flow.locator('input[type=file]').is_enabled()
                assert flow.get_by_role('button',name='Prepare new Reference run').is_enabled()
                checks.append('normal automatic link immediately displays follow-up history and neutral success text, keeps human review pending, and consumes Answer preview')
                evaluation_response=client.post(f"/api/projects/{first['id']}/reference-evaluations",json={
                    'run_id':normal_run,'name':'Synthetic busy-entry checks','questions':[QUESTION]})
                assert evaluation_response.status_code==201,evaluation_response.text
                evaluation=evaluation_response.json()
                page.evaluate('async () => await loadReferenceEvaluations()')
                evaluation_card=page.locator('#reference-evaluations .reference-evaluation-card').filter(has_text='Synthetic busy-entry checks')
                assert evaluation_card.get_by_role('button',name='Run question',exact=True).count()==1
                requests_before=len(api_requests)
                blocked=page.evaluate('''async evaluationId => {
                    state.caseFlowBusy=true;
                    state.referenceEvaluationPendingBatch=evaluationId;
                    $('reference-evaluation-run-confirm').checked=true;
                    const entries=[['start','click'],['reference-prepare','click'],['reference-preview','click'],
                        ['reference-evaluation-run-managed','click'],['question-form','submit'],
                        ['reference-question-form','submit'],['reference-evaluation-run-form','submit'],
                        ['reference-evaluation-form','submit'],['model-settings-form','submit']];
                    const outcomes=[];
                    for (const [id,type] of entries) {
                        const target=$(id);target.disabled=false;
                        const event=new Event(type,{bubbles:true,cancelable:true});
                        target.dispatchEvent(event);outcomes.push({id,prevented:event.defaultPrevented});
                    }
                    const target=[...$('reference-evaluations').querySelectorAll('button')].find(button=>button.textContent==='Run question');
                    target.disabled=false;const event=new MouseEvent('click',{bubbles:true,cancelable:true});
                    target.dispatchEvent(event);outcomes.push({id:'single-evaluation',prevented:event.defaultPrevented});
                    await runReferenceEvaluationBatch(evaluationId);
                    await startManagedReferenceEvaluationJob(evaluationId);
                    state.caseFlowBusy=false;updateQuestionAvailability();updateReferenceAvailability();
                    return outcomes;
                }''',evaluation['evaluation_id'])
                assert all(item['prevented'] for item in blocked),blocked
                assert not [(method,path) for method,path in api_requests[requests_before:] if method not in ('GET','HEAD')]
                assert len(calls)==4
                assert client.get(f"/api/reference-evaluations/{evaluation['evaluation_id']}").json()['items'][0]['state']=='PENDING'
                checks.append('busy scope blocks actual DOM events for ordinary/reference QA, single/batch/managed evaluation, Prepare, analysis start and model restart even with stale enabled controls')
                # Every recovery scenario starts with genuine uploaded sources and
                # saved MockTransport results. The fault is injected only AFTER
                # the real follow-up POST commits, never by fabricating a result.
                for fault_mode in ('refresh_failed','lost_response','unknown_response','envelope_failed','unknown_without_envelope'):
                    fault_project=client.post('/api/projects',json={'name':f'Synthetic link fault {fault_mode}'}).json()
                    upload(client,fault_project['id'],'initial.txt',b'No approved color appears in this initial file.')
                    fault_initial_run=_reference_run(client,fault_project['id'])
                    fault_initial=_ask(client,fault_project['id'],fault_initial_run['id'])
                    original_result=client.get(f"/api/reference-results/{fault_initial['result_id']}").json()
                    fault_case=client.post(f"/api/projects/{fault_project['id']}/reference-cases",json={
                        'run_id':fault_initial_run['id'],'question':QUESTION,'result_id':fault_initial['result_id']}).json()
                    page.evaluate('async projectId => await projects(projectId)',fault_project['id'])
                    page.evaluate('async caseId => await openReferenceCase(caseId)',fault_case['case_id'])
                    fault_flow=page.locator('#reference-case-detail .reference-case-followup-flow')
                    fault_flow.locator('input[type=file]').set_input_files(str(supplement))
                    fault_flow.get_by_role('button',name='Upload and attach supplement').click()
                    page.wait_for_function('() => !state.caseFlowBusy && !state.uploading && state.referenceCaseSelected.attachments.length===1')
                    fault_flow.get_by_role('button',name='Prepare new Reference run').click()
                    page.wait_for_function('() => !state.caseFlowBusy && Boolean(state.referenceKnowledge.active_run_id)')
                    fault_run=page.evaluate('state.referenceKnowledge.active_run_id')
                    app.state.runner.process(fault_run)
                    fault_flow.get_by_role('button',name='Refresh available runs').click()
                    page.wait_for_function('() => !state.caseFlowBusy && !state.referenceKnowledge.active_update')
                    fault_flow.get_by_role('button',name='Preview pages for the new run').click()
                    page.wait_for_function("() => !document.querySelector('#reference-case-detail .reference-case-followup-flow button:last-child').disabled")
                    link_fault.update(mode=fault_mode,case_id=fault_case['case_id'],post_seen=False,refresh_failures=0)
                    fault_calls_before=len(calls)
                    questions_before=sum(method=='POST' and path.endswith('/questions-v3') for method,path in api_requests)
                    links_before=sum(method=='POST' and path.endswith('/follow-up-results') for method,path in api_requests)
                    fault_flow.get_by_role('button',name='Answer and link this run').click()
                    page.wait_for_function('() => !state.caseFlowBusy')
                    assert link_fault['post_seen']
                    saved_case=client.get(f"/api/reference-cases/{fault_case['case_id']}").json()
                    assert len(saved_case['followups'])==1
                    assert saved_case['status']=='IN_REVIEW' and not saved_case['resolution']
                    assert len(calls)==fault_calls_before+1
                    assert sum(method=='POST' and path.endswith('/questions-v3') for method,path in api_requests)==questions_before+1
                    assert sum(method=='POST' and path.endswith('/follow-up-results') for method,path in api_requests)==links_before+1
                    assert client.get(f"/api/reference-results/{fault_initial['result_id']}").json()==original_result
                    toast=page.locator('#toast').inner_text()
                    assert 'not linked' not in toast.lower() and 'not yet linked' not in toast.lower(),toast
                    if fault_mode in {'lost_response','envelope_failed'}:
                        assert page.evaluate('state.referenceCaseSelected.followups.length')==1
                        assert toast=='The new answer is linked and still requires human review.',toast
                        if fault_mode=='envelope_failed':assert link_fault['refresh_failures']>0
                    else:
                        assert link_fault['refresh_failures']>0
                        assert 'refresh' in toast.lower() or 'confirm' in toast.lower(),toast
                    assert fault_flow.get_by_role('button',name='Answer and link this run').is_disabled()
                    page.evaluate('renderReferenceResults()')
                    assert page.locator('#reference-results .reference-result-card').count()==(1 if fault_mode=='unknown_without_envelope' else 2)
                    link_fault.update(mode=None,case_id=None,post_seen=False)
                    page.evaluate('async caseId => {await loadReferenceResults();await openReferenceCase(caseId);}',fault_case['case_id'])
                    assert page.evaluate('state.referenceCaseSelected.followups.length')==1
                    assert page.locator('#reference-results .reference-result-card').count()==2
                    assert len(calls)==fault_calls_before+1
                    assert sum(method=='POST' and path.endswith('/questions-v3') for method,path in api_requests)==questions_before+1
                    assert sum(method=='POST' and path.endswith('/follow-up-results') for method,path in api_requests)==links_before+1
                    checks.append(f'{fault_mode}: real committed link survives response/refresh failure, reports only confirmed state and recovers without repeating a question or link')
                for switch_scope in ('case','project'):
                    switch_project=client.post('/api/projects',json={'name':f'Synthetic envelope switch {switch_scope}'}).json()
                    upload(client,switch_project['id'],'initial.txt',b'No approved color appears in this initial file.')
                    switch_initial_run=_reference_run(client,switch_project['id'])
                    switch_initial=_ask(client,switch_project['id'],switch_initial_run['id'])
                    switch_case=client.post(f"/api/projects/{switch_project['id']}/reference-cases",json={
                        'run_id':switch_initial_run['id'],'question':QUESTION,'result_id':switch_initial['result_id']}).json()
                    other_question='Other case during saved answer recovery'
                    other_answer=client.post(f"/api/projects/{switch_project['id']}/questions-v3",json={
                        'run_id':switch_initial_run['id'],'question':other_question}).json()
                    other_case=client.post(f"/api/projects/{switch_project['id']}/reference-cases",json={
                        'run_id':switch_initial_run['id'],'question':other_question,'result_id':other_answer['result_id']}).json()
                    page.evaluate('async projectId => await projects(projectId)',switch_project['id'])
                    page.evaluate('async caseId => await openReferenceCase(caseId)',switch_case['case_id'])
                    switch_flow=page.locator('#reference-case-detail .reference-case-followup-flow')
                    switch_flow.locator('input[type=file]').set_input_files(str(supplement))
                    switch_flow.get_by_role('button',name='Upload and attach supplement').click()
                    page.wait_for_function('() => !state.caseFlowBusy && !state.uploading && state.referenceCaseSelected.attachments.length===1')
                    switch_flow.get_by_role('button',name='Prepare new Reference run').click()
                    page.wait_for_function('() => !state.caseFlowBusy && Boolean(state.referenceKnowledge.active_run_id)')
                    switch_run=page.evaluate('state.referenceKnowledge.active_run_id')
                    app.state.runner.process(switch_run)
                    switch_flow.get_by_role('button',name='Refresh available runs').click()
                    page.wait_for_function('() => !state.caseFlowBusy && !state.referenceKnowledge.active_update')
                    switch_flow.get_by_role('button',name='Preview pages for the new run').click()
                    page.wait_for_function("() => !document.querySelector('#reference-case-detail .reference-case-followup-flow button:last-child').disabled")
                    switch_before=client.get(f"/api/reference-cases/{switch_case['case_id']}").json()
                    calls_before=len(calls);request_start=len(api_requests)
                    def hold_saved_envelope():
                        page.evaluate('''() => {
                            const original=window.fetch;window.__restoreEnvelopeFetch=original;
                            window.__envelopeHeld=false;
                            window.fetch=async (...args) => {
                                const response=await original(...args);
                                if(String(args[0]).startsWith('/api/reference-results/')&&!window.__envelopeHeld){
                                    window.__envelopeHeld=true;
                                    await new Promise(resolve=>window.__releaseEnvelope=resolve);
                                }
                                return response;
                            };
                        }''')
                    hold_saved_envelope()
                    switch_flow.get_by_role('button',name='Answer and link this run').click()
                    page.wait_for_function('() => window.__envelopeHeld')
                    if switch_scope=='case':
                        page.evaluate('async caseId => await openReferenceCase(caseId)',other_case['case_id'])
                    else:
                        page.evaluate('async projectId => await selectProject(projectId)',second['id'])
                    page.evaluate('window.__releaseEnvelope()')
                    page.wait_for_function('() => !state.caseFlowBusy')
                    page.evaluate('window.fetch=window.__restoreEnvelopeFetch')
                    assert client.get(f"/api/reference-cases/{switch_case['case_id']}").json()==switch_before
                    writes=[(method,path) for method,path in api_requests[request_start:] if method not in ('GET','HEAD')]
                    assert writes==[('POST',f"/api/projects/{switch_project['id']}/questions-v3/preview"),('POST',f"/api/projects/{switch_project['id']}/questions-v3")],writes
                    assert len(calls)==calls_before+1
                    checks.append(f'switching {switch_scope} during the saved-envelope GET cancels the old case link, preserves the case and never repeats the already saved answer')
                    if switch_scope=='project':
                        # The pending ID remains after the project switch. Opening
                        # that case must replace the previously displayed form before
                        # awaiting the pending saved-envelope recovery.
                        page.evaluate('async projectId => await selectProject(projectId)',switch_project['id'])
                        page.evaluate('async caseId => await openReferenceCase(caseId)',other_case['case_id'])
                        old_form_case=client.get(f"/api/reference-cases/{other_case['case_id']}").json()
                        page.evaluate("window.__oldCaseWriteNodes=[...document.querySelectorAll('#reference-case-detail button,#reference-case-detail form')]")
                        hold_saved_envelope();old_request_start=len(api_requests)
                        page.evaluate('''caseId => {
                            window.__caseOpenDone=false;
                            void openReferenceCase(caseId).finally(()=>window.__caseOpenDone=true);
                        }''',switch_case['case_id'])
                        page.wait_for_function('() => window.__envelopeHeld')
                        assert page.locator('#reference-case-detail h3').inner_text()==QUESTION
                        assert page.evaluate('window.__oldCaseWriteNodes.every(node=>!document.contains(node))')
                        page.evaluate('''() => {for(const node of window.__oldCaseWriteNodes){
                            node.disabled=false;node.dispatchEvent(new Event(node.tagName==='FORM'?'submit':'click',{bubbles:true,cancelable:true}));
                        }}''')
                        page.evaluate('window.__releaseEnvelope()')
                        page.wait_for_function('() => window.__caseOpenDone')
                        page.evaluate('window.fetch=window.__restoreEnvelopeFetch')
                        assert not [(method,path) for method,path in api_requests[old_request_start:] if method not in ('GET','HEAD')]
                        assert client.get(f"/api/reference-cases/{other_case['case_id']}").json()==old_form_case
                        assert len(calls)==calls_before+1
                        checks.append('opening a pending-recovery case removes the previous writable form before the held GET and stale detached controls cannot write')
                    page.evaluate('async projectId => await selectProject(projectId)',switch_project['id'])
                    page.evaluate('async caseId => await openReferenceCase(caseId)',switch_case['case_id'])
                    assert page.locator('#reference-case-detail .reference-case-followup-form select').nth(0).input_value()
                    assert len(calls)==calls_before+1
                # Restore the final fault case for the cross-project read checks.
                page.evaluate('async projectId => await selectProject(projectId)',fault_project['id'])
                page.evaluate('async caseId => await openReferenceCase(caseId)',fault_case['case_id'])
                current_project=page.evaluate('state.project')
                page.evaluate('''project => {
                    const original=window.fetch;window.__restoreResultFetch=original;
                    window.__resultReadHeld=false;window.__resultReadDone=false;
                    window.fetch=async (...args) => {
                        const response=await original(...args);
                        if(String(args[0])===`/api/projects/${project}/reference-results?limit=50`&&!window.__resultReadHeld){
                            window.__resultReadHeld=true;
                            await new Promise(resolve=>window.__releaseResultRead=resolve);
                        }
                        return response;
                    };
                    void loadReferenceResults().finally(()=>window.__resultReadDone=true);
                }''',current_project)
                page.wait_for_function('() => window.__resultReadHeld')
                page.evaluate('async project => await selectProject(project)',second['id'])
                page.evaluate('window.__releaseResultRead()')
                page.wait_for_function('() => window.__resultReadDone')
                assert page.evaluate('state.project')==second['id']
                assert page.evaluate('state.referenceResults')==[]
                assert page.locator('#reference-results .reference-result-card').count()==0
                page.evaluate('window.fetch=window.__restoreResultFetch')
                checks.append('a results-list response held across a real project switch cannot populate the new project with the old project answers')
                page.evaluate('async project => await selectProject(project)',current_project)
                page.evaluate('async caseId => await openReferenceCase(caseId)',fault_case['case_id'])
                for endpoint in ('reference-knowledge','analysis-runs'):
                    # Set only the poll precondition; all knowledge and run responses
                    # still come from the real temporary-project API records.
                    page.evaluate('''([project,endpoint]) => {
                        const original=window.fetch;window.__restoreWorkspaceFetch=original;
                        window.__workspaceReadHeld=false;window.__workspaceReadDone=false;
                        window.fetch=async (...args) => {
                            const response=await original(...args);
                            if(String(args[0])===`/api/projects/${project}/${endpoint}`&&!window.__workspaceReadHeld){
                                window.__workspaceReadHeld=true;
                                await new Promise(resolve=>window.__releaseWorkspaceRead=resolve);
                            }
                            return response;
                        };
                        state.referenceKnowledge={...state.referenceKnowledge,active_update:true};
                        void refreshReferenceWorkspace().finally(()=>window.__workspaceReadDone=true);
                    }''',[current_project,endpoint])
                    page.wait_for_function('() => window.__workspaceReadHeld')
                    page.evaluate('async project => await selectProject(project)',second['id'])
                    switched=page.evaluate('({knowledge:state.referenceKnowledge,runs:state.referenceRuns,results:state.referenceResults})')
                    page.evaluate('window.__releaseWorkspaceRead()')
                    page.wait_for_function('() => window.__workspaceReadDone')
                    assert page.evaluate('state.project')==second['id']
                    assert page.evaluate('({knowledge:state.referenceKnowledge,runs:state.referenceRuns,results:state.referenceResults})')==switched
                    page.evaluate('window.fetch=window.__restoreWorkspaceFetch')
                    checks.append(f'a held workspace {endpoint} response cannot change the switched project knowledge, runs or answers')
                    page.evaluate('async project => await selectProject(project)',current_project)
                    page.evaluate('async caseId => await openReferenceCase(caseId)',fault_case['case_id'])
                page.locator('#reference-question').fill('Preserve this synthetic draft across language changes.')
                before=page.locator('#reference-question').input_value()
                page.evaluate("CIRPI18n.setLanguage('zh-CN')")
                assert page.locator('#reference-question').input_value()==before
                page.evaluate("CIRPI18n.setLanguage('en')")
                checks.append('language switch preserves current draft without rebuilding forms')
                page.locator('#project-select').select_option(second['id'])
                page.wait_for_function(f"() => window.__cirpTestState.project==='{second['id']}'")
                assert page.locator('#reference-case-detail').inner_text()==''
                checks.append('project switch clears active case surface before any later stage write')
                assert page.evaluate('document.documentElement.scrollWidth')<=390
                checks.append('production page has no document-level overflow at 390px')
                assert not errors,errors
            finally:
                context.close();browser.close()
    report=report_path or ROOT/'reports/field_case_followup_browser_validation_2026-10-04.md'
    report.write_text('# Supplemental case Web browser acceptance\n\n- PASS: '+str(len(checks))+' checks; external network: 0; synthetic MockTransport model calls: '+str(len(calls))+'.\n\n'+'\n'.join('- '+check for check in checks)+'\n\n## Boundary\n\n- Chromium requests were routed only to an in-process FastAPI TestClient and temporary SQLite database. All V3 decisions used MockTransport; no provider, local service, or customer file was contacted. Pending model-setting and legacy-start browser requests received a synthetic failure without changing configuration or starting a legacy run. Link recovery faults were injected after the actual follow-up POST committed.\n',encoding='utf-8')
    print(f'PASS {len(checks)} production Chromium checks; report={report}')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,help='Save a separate report without replacing a historical checkpoint.')
    main(parser.parse_args().report)
