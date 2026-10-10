"""Display-only addition: original application contracts remain unchanged."""
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings, ROOT
from scripts.build_cloudflare import build

def test_followup_flow_has_explicit_frozen_preview_contract():
    """The browser supplement flow keeps preview and request scopes explicit."""
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    for token in ('caseFlowByCase', 'selectedIds', 'preparedRunId', 'selectionId',
                  'sameOpenCase', 'caseFlowBlocked', 'caseFlowLinkUnknown', 'renderPreparedRun'):
        assert token in source or token in translations
    assert 'questions-v3/preview' in source
    assert "questions-v3`,'POST'" in source
    assert "follow-up-results`,'POST'" in source
    assert "memory.selectedIds.every" in source
    assert 'caseFlowTitle' in translations


def test_case_writes_and_refresh_are_runtime_guarded():
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    assert "state.caseFlowBusy=true;save.disabled=true" in source
    assert "state.caseFlowBusy=true;link.disabled=true" in source
    assert "if(!state.project||caseFlowBlocked())return;" in source
    assert "if(caseFlowBlocked())return;state.caseFlowBusy=true;updateReferenceAvailability();const apply=" in source
    assert "Promise.all([api(`/projects/${project}/analysis-runs`),api(`/projects/${project}/reference-knowledge`)])" in source
    assert "freshSelection.selection_id!==proof.selectionId" in source
    assert "memory.preparedForCaseVersion===currentCase().version" in source
    assert "memory.preparedSelectedIds" in source
    assert "memory.preview=null;sync();const result=await api(`/projects/${project}/questions-v3`" in source
    assert "if(caseFlowBlocked()||!state.project)return;state.caseFlowBusy=true" in source
    assert "state.caseFlowBusy=false;apply.disabled=false;updateReferenceAvailability();if(state.referenceCaseSelected)renderReferenceCaseDetail();" in source
    assert "const attachedIds=new Set((currentCase().attachments||[]).map(value=>value.document_id)),newIds=ids.filter(id=>!attachedIds.has(id))" in source
    assert "attachments:newIds,note:I.t('reference.caseFlowUploadNote')" in source
    assert "attachments:[...attachedIds,...newIds]" not in source
    assert "else toast(I.t('reference.caseFlowAlreadyAttached'))" in source
    assert "(fresh.followups||[]).some(followup=>followup.result_id===result.result_id)" in source
    assert "memory.pendingResultId=result.result_id;memory.pendingRunId=runId;memory.pendingQuestion=item.question;await recoverPendingReferenceResult(caseId,project);" in source
    assert "await recoverPendingReferenceResult(caseId,project);if(!sameOpenCase(project,caseId))return;" in source
    assert "async function recoverPendingReferenceResult" in source
    assert "const saved=await api(`/reference-results/${resultId}`)" in source
    assert "saved.result_id!==resultId||saved.project_id!==project||saved.run_id!==memory.pendingRunId||saved.question!==memory.pendingQuestion" in source
    assert "delete memory.pendingResultId;delete memory.pendingRunId;delete memory.pendingQuestion;" in source
    assert "function upsertReferenceResult(saved)" in source
    assert "state.referenceResults=[result,...state.referenceResults]" not in source
    assert "const project=state.project;if(!project){state.referenceResults=[];renderReferenceResults();return;}" in source
    assert "const data=await api(`/projects/${project}/reference-results?limit=50`);if(state.project!==project)return;" in source
    assert "const project=state.project;if(!project)return;" in source
    assert "knowledge=await api(`/projects/${project}/reference-knowledge`);if(state.project!==project)return;" in source
    assert "runs=await api(`/projects/${project}/analysis-runs`);if(state.project!==project)return;await loadReferenceResults();if(state.project!==project)return;" in source
    assert "const item=state.referenceCaseSelected,renderedProject=state.project;if(!item)return;" in source
    assert "state.referenceCaseSelected=data;renderReferenceCaseDetail();await recoverPendingReferenceResult(caseId,project);" in source
    assert source.count("if(!sameOpenCase(project,caseId)||") >= 6
    assert "state.referenceCaseSelected=fresh;if((fresh.followups||[]).some(followup=>followup.result_id===result.result_id))linkedCase=fresh;else{renderReferenceCaseDetail();toast(I.t('reference.caseFlowLinkUnknown'));return;}" in source
    assert "reference.caseFlowLinkedRefreshPending" in source
    assert "reference.caseFlowAlreadyAttached" in translations
    assert "reference.caseFlowLinkUnknown" in translations
    assert "existing saved-result picker" not in translations
    for identifier in ('start','reference-prepare','reference-preview','reference-evaluation-run-managed',
                       'question-form','reference-evaluation-run-form','reference-evaluation-form',
                       "$('reference-evaluations').addEventListener('click'"):
        assert identifier in source
    assert "if(state.caseFlowBusy){event.preventDefault();event.stopImmediatePropagation();}" in source
    assert "if(state.caseFlowBusy||state.referenceEvaluationBusy)return;" in source
    assert "if(state.caseFlowBusy||state.referenceEvaluationJobStarting)return;" in source


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.items = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


def test_localization_resource_served_in_original_application(tmp_path):
    with TestClient(create_app(Settings(tmp_path, start_worker=False))) as client:
        home = client.get('/')
        js = client.get('/assets/i18n.js')
        assert home.status_code == js.status_code == 200
        assert 'CIRPI18n' in js.text
        assert home.text.index('/assets/i18n.js') < home.text.index('/assets/app.js')
        assert "script-src 'self'" in js.headers['content-security-policy']


def test_display_switch_does_not_add_backend_locale_fields(tmp_path):
    with TestClient(create_app(Settings(tmp_path, start_worker=False)), headers={'X-CIRP-Client': 'browser'}) as client:
        settings = client.get('/api/settings').json()
        assert not {'language', 'locale', 'ui_language'} & settings.keys()
        project = client.post('/api/projects', json={'name': '未指定 / Project original'}).json()
        assert project['name'] == '未指定 / Project original'
        assert client.post('/api/projects', json={'name': 'x', 'ui_language': 'en'}).status_code == 422
        assert client.get('/api/settings').json() == settings


def test_language_controls_are_accessible_and_do_not_submit_form_values():
    elements = Elements((ROOT/'web/index.html').read_text(encoding='utf-8')).items
    selectors = [attrs for tag, attrs in elements if tag == 'select' and 'data-ui-language' in attrs]
    assert {x['id'] for x in selectors} == {'ui-language', 'drawer-language', 'dialog-language'}
    assert all(x['data-i18n-aria-label'] == 'language.label' and 'name' not in x for x in selectors)
    labels = {attrs.get('for') for tag, attrs in elements if tag == 'label'}
    assert {x['id'] for x in selectors} <= labels


def test_original_actions_and_input_constraints_remain_present():
    elements = Elements((ROOT/'web/index.html').read_text(encoding='utf-8')).items
    byid = {attrs['id']: (tag, attrs) for tag, attrs in elements if 'id' in attrs}
    expected = ('project-select', 'new-project', 'start', 'pause', 'resume', 'file-input', 'folder-input',
                'filter', 'run-select', 'export-json', 'export-xlsx', 'drawer', 'project-form',
                'reconciliation-panel', 'unresolved-calls', 'reconcile-dialog', 'reconcile-form',
                'reconcile-resolution', 'reconcile-amount', 'reconcile-confirm', 'local-workers',
                'performance',
                'analysis-progress', 'progress-bar', 'progress-summary')
    assert all(id_ in byid for id_ in expected)
    assert byid['project-input'][1]['maxlength'] == '150'
    assert byid['file-input'][1]['type'] == 'file' and 'multiple' in byid['file-input'][1]
    assert 'webkitdirectory' in byid['folder-input'][1]
    assert byid['project-form'][0] == 'form'
    assert byid['reconcile-confirm'][1]['required'] == ''
    assert byid['reconcile-note'][1]['maxlength'] == '1000'
    assert byid['resume'][1]['data-i18n-title'] == 'reconcile.resumePolicy'
    assert byid['local-workers'][0] == 'select'
    assert not {'budget-limit','save-budget','project-budget','cost'} & byid.keys()
    assert byid['analysis-progress'][1]['role'] == 'progressbar'
    assert byid['analysis-progress'][1]['aria-valuemax'] == '100'


def test_model_api_settings_are_customer_visible_and_never_render_a_saved_key():
    elements=Elements((ROOT/'web/index.html').read_text(encoding='utf-8')).items
    byid={attrs['id']:(tag,attrs) for tag,attrs in elements if 'id' in attrs}
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    for item in ('model-settings-open','model-settings','model-settings-form','model-provider',
                 'model-base-url','model-name','model-deepseek-model','model-deepseek-option',
                 'model-api-key','model-vision-enabled',
                 'model-reasoning-effort','model-reasoning-option',
                 'model-structured-output','model-structured-option',
                 'model-remember','model-approved','apply-model-settings'):
        assert item in byid
    assert byid['model-settings'][0]=='section'
    assert byid['model-settings-form'][0]=='form'
    assert not {'model-settings-dialog','close-model-settings'} & byid.keys()
    assert any(tag=='a' and attrs.get('href')=='#model-settings' and attrs.get('data-i18n')=='nav.settings'
               for tag,attrs in elements)
    assert byid['model-api-key'][1]['type']=='password'
    assert byid['model-api-key'][1]['autocomplete']=='new-password'
    assert 'disabled' not in byid['model-api-key'][1] and 'readonly' not in byid['model-api-key'][1]
    assert 'value' not in byid['model-api-key'][1]
    assert "const data=await api('/model-settings')" in source
    assert "await api('/model-settings','POST'" in source
    assert 'await loadModelSettings()' in source
    assert "$('model-settings').scrollIntoView" in source
    assert 'model-settings-dialog' not in source
    assert "$('model-api-key').value=''" in source
    assert 'data.service_instance!==previousInstance' in source
    assert 'current.service_instance!==state.settings.service_instance' in source
    assert "setInterval(reloadForServiceChange,4000)" in source
    assert "visionSupported=custom||openai||(deepseek&&deepseekModel==='deepseek-flash')" in source
    assert "structuredSupported=custom||openai" in source
    assert "$('model-structured-option').hidden=!structuredSupported" in source
    assert any(tag=='option' and attrs.get('value')=='openai' for tag,attrs in elements)
    assert "structured_output_mode:$('model-structured-output').value" in source
    assert "reasoning_effort:provider==='deepseek'?$('model-reasoning-effort').value:'none'" in source
    assert "model:provider==='deepseek'?$('model-deepseek-model').value" in source
    assert byid['model-deepseek-model'][0]=='select'
    assert any(tag=='option' and attrs.get('value')=='deepseek-flash' for tag,attrs in elements)
    assert any(tag=='option' and attrs.get('value')=='deepseek-v4-pro' for tag,attrs in elements)
    assert any(tag=='option' and attrs.get('value')=='deepseek-v4-pro' and 'disabled' not in attrs
               for tag,attrs in elements)
    assert 'innerHTML' not in source


def test_project_question_ui_keeps_answers_as_text_and_sources_inline():
    elements = Elements((ROOT/'web/index.html').read_text(encoding='utf-8')).items
    byid = {attrs['id']: (tag, attrs) for tag, attrs in elements if 'id' in attrs}
    source = (ROOT/'web/app.js').read_text(encoding='utf-8')
    for item in ('ask-project','question-form','project-question','ask-submit','question-results'):
        assert item in byid
    assert byid['question-form'][0] == 'form'
    assert byid['project-question'][1]['maxlength'] == '1000'
    assert "api(`/projects/${project}/questions`,'POST'" in source
    assert 'state.questionRequestVersion===requestVersion' in source
    assert "state.knowledge=await api(`/projects/${state.project}/knowledge`)" in source
    assert "'POST',{question}" in source
    assert "'POST',{run_id:state.run,question}" not in source
    assert "source.append(quote,location,open);return source" in source
    assert "showEvidence(item.evidence_id,{start:item.start,end:item.end},data.run_id)" in source
    assert "const findings=data.source_findings||[]" in source
    assert "const workflowStatuses=[...(data.workflow_statuses||[]),...(data.workflow_conflicts||[])]" in source
    assert "a.href='/api/documents/'+source.document_id+'/file'" in source
    assert "workflowCodeLabel('status',status.status)" in source
    assert "workflowClassification.source.'+source.classification_source" in source
    assert "if(source.citation)row.append(questionCitation(source.citation,data))" in source
    assert "finding.append(questionCitation(citation,data))" in source
    assert "questionSourceLabels[item.source_type]" in source
    assert "if(data.answer_basis==='MODEL_PROJECT_EVIDENCE')return data.cached?'ask.basis.modelCached':'ask.basis.modelLive'" in source
    assert "ANALYSIS_INCOMPLETE:'ask.basis.analysisIncomplete'" in source
    assert "if(basisKey)article.append(elT('div',basisKey,{},'question-basis'))" in source
    assert "body.append(el('pre',JSON.stringify(e.locator,null,2)))" not in source
    assert "citationLocation({file_name:null,locator:e.locator})" in source
    assert 'innerHTML' not in source


def test_outlook_msg_is_selectable_and_uses_email_attachment_review():
    elements=Elements((ROOT/'web/index.html').read_text(encoding='utf-8')).items
    byid={attrs['id']:attrs for _,attrs in elements if 'id' in attrs}
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    assert all('.msg' in byid[name]['accept'].split(',') for name in ('file-input','folder-input'))
    assert '/\\.(?:eml|msg)$/i.test(u.name)' in source


def test_reconciliation_ui_requires_provider_check_and_never_inserts_diagnostic_html():
    source = (ROOT/'web/app.js').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    assert "confirmation:'PROVIDER_BILLING_CHECKED'" in source
    assert "resolution==='BILLED'?'reconcile.savedBilled':'reconcile.savedNotBilled'" in source
    assert "state.unresolvedCalls.length>0" in source
    assert "!manifest.documents.length||state.unresolvedCalls.length>0" in source
    assert "call.provider_request_id||d.provider_request_id" in source
    assert '保存不会自动重试' in translations and '每个任务族累计最多 3 次' in translations
    assert 'Saving never retries automatically' in translations and 'at most three calls per task family' in translations
    assert 'innerHTML' not in source


def test_pages_bundle_includes_localization_without_private_files(tmp_path):
    output = build(tmp_path/'pages')
    assert (output/'assets/i18n.js').read_bytes() == (ROOT/'web/i18n.js').read_bytes()
    assert not any(p.name in ('.env', '.local', 'cirp.sqlite3') for p in output.rglob('*'))
    assert '/assets/i18n.js' in (output/'index.html').read_text(encoding='utf-8')


@pytest.mark.parametrize('needle', ["headers={'X-CIRP-Client':'browser'}", "'X-CIRP-Client':'browser'", '/exports/${fmt}'])
def test_existing_request_headers_and_export_path_are_preserved(needle):
    source = (ROOT/'web/app.js').read_text(encoding='utf-8')
    assert needle in source
    assert 'Accept-Language' not in source
    assert 'ui_language' not in source


def test_current_version_is_supported_by_record_and_change_metadata():
    import json
    version = (ROOT/'VERSION').read_text().strip()

    def version_fields(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'spec_version' and isinstance(child, dict) and 'enum' in child:
                    yield child['enum']
                else:
                    yield from version_fields(child)
        elif isinstance(value, list):
            for child in value:
                yield from version_fields(child)

    for name in ('common.schema.json', 'change-record.schema.json'):
        fields = list(version_fields(json.loads((ROOT/'spec/schemas'/name).read_text(encoding='utf-8'))))
        assert fields and all(version in values and '0.2.4' in values for values in fields)


def test_reviewer_scope_contains_only_material_and_qa_tabs():
    source = (ROOT/'web/app.js').read_text(encoding='utf-8')
    html = (ROOT/'web/index.html').read_text(encoding='utf-8')
    assert "const primaryKind=['MATERIAL','INSPECTION'].find" in source
    assert 'data-kind="MATERIAL"' in html
    assert 'data-kind="INSPECTION"' in html
    assert 'data-kind="CONFLICT"' not in html
    assert 'data-kind="MISSING"' not in html


def test_active_run_polling_is_serialized_and_defers_full_record_loading():
    source = (ROOT/'web/app.js').read_text(encoding='utf-8')
    assert 'recordPagination:null,recordsRun:null,recordLoadPromise:null' in source
    assert 'if(state.refreshPromise){await state.refreshPromise;if(!forceRecords)return;}' in source
    assert "if(forceRecords||(!active&&state.recordsRun!==rid))" in source
    assert "api(`/analysis-runs/${rid}/record-summaries?limit=1`)" in source
    assert "api(`/analysis-runs/${rid}/record-summaries?kind=${encodeURIComponent(kind)}&offset=${offset}&limit=100`)" in source
    assert 'state.records=previous.concat(page.items.filter(item=>!seen.has(item.record.meta.record_id)));' in source
    assert "$('results-load-more').onclick" in source
    assert 'await api(`/records/${row.record.meta.record_id}`)' in source
    assert 'await refreshRun(true)' in source


def test_record_pagination_has_bilingual_visible_controls_and_kind_reset():
    html=(ROOT/'web/index.html').read_text(encoding='utf-8')
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    assert 'id="results-load-more"' in html and 'id="results-page-status"' in html
    assert '"results.loaded": "当前分类已加载 {loaded} / {total} 条结果"' in translations
    assert '"results.loaded": "Loaded {loaded} of {total} results in this category"' in translations
    assert 'state.records=[];state.recordPagination=null;await loadRecordPage(true);' in source


def test_workflow_pagination_shows_true_total_and_loads_more_without_duplicates():
    html=(ROOT/'web/index.html').read_text(encoding='utf-8')
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    style=(ROOT/'web/style.css').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    assert 'hidden="" id="workflow-load-more"' in html
    assert '[hidden]{display:none!important}' in style
    assert '"workflow.loaded": "已加载 {loaded} / {total} 个工作流关系"' in translations
    assert '"workflow.loaded": "Loaded {loaded} of {total} workflow groups"' in translations
    assert 'workflowPagination:null,workflowsRun:null,workflowLoadPromise:null' in source
    assert 'api(`/analysis-runs/${rid}/workflows?offset=${offset}&limit=500`)' in source
    assert 'state.workflows=previous.concat(page.items.filter(item=>!seen.has(item.group_id)));' in source
    assert "state.workflowPagination?.total??rows.length" in source
    assert "$('workflow-load-more').onclick" in source
    assert 'state.workflows=(await api(`/analysis-runs/${rid}/workflows?limit=500`)).items' not in source


def test_workflow_classification_correction_uses_native_auditable_controls():
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    assert "elT('button','workflowClassification.open'" in source
    assert "['DETECTED','OTHER','RFI','SUBMITTAL']" in source
    assert "expected_version:data.override.version" in source
    assert "does not rewrite parsed text, evidence, or extracted candidates" in translations
    assert "不改写解析文本、证据或候选结果" in translations


def test_live_vision_has_explicit_full_page_data_transfer_disclosure():
    html=(ROOT/'web/index.html').read_text(encoding='utf-8')
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    assert 'id="vision-disclosure"' in html and 'full-page PNG derivatives and parsed text' in html
    assert "state.settings.capabilities?.vision?.ready" in source
    assert 'Live vision sends eligible full-page PNG derivatives' in translations


def test_reference_qa_is_a_separate_managed_workspace():
    elements=Elements((ROOT/'web/index.html').read_text(encoding='utf-8')).items
    byid={attrs['id']:(tag,attrs) for tag,attrs in elements if 'id' in attrs}
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    expected=(
        'reference-qa','reference-prepare','reference-run-status','reference-question-form',
        'reference-question','reference-preview','reference-ask','reference-status',
        'reference-preview-results','reference-results-refresh','reference-export-json',
        'reference-results','reference-compare-baseline','reference-compare-candidate',
        'reference-compare','reference-compare-status','reference-comparison-results',
        'reference-evaluation-form','reference-evaluation-name',
        'reference-evaluation-questions','reference-evaluation-create',
        'reference-evaluation-refresh','reference-evaluation-status','reference-evaluations',
        'reference-evaluation-readiness-status','reference-evaluation-readiness',
        'reference-evaluation-scorecard-status','reference-evaluation-scorecard',
        'reference-evaluation-compare-baseline','reference-evaluation-compare-candidate',
        'reference-evaluation-compare','reference-evaluation-compare-status',
        'reference-evaluation-comparison-results','reference-evaluation-jobs-status',
        'reference-evaluation-jobs',
        'reference-evaluation-run-dialog','reference-evaluation-run-form',
        'reference-evaluation-run-summary','reference-evaluation-run-source',
        'reference-evaluation-run-profile','reference-evaluation-run-confirm',
        'reference-evaluation-run-cancel','reference-evaluation-run-start',
        'reference-evaluation-run-managed')
    assert all(item in byid for item in expected)
    assert byid['reference-question-form'][0]=='form'
    assert byid['reference-question'][1]['maxlength']=='1000'
    assert byid['reference-evaluation-form'][0]=='form'
    assert byid['reference-evaluation-name'][1]['maxlength']=='150'
    assert byid['reference-evaluation-questions'][1]['maxlength']=='50049'
    assert byid['reference-evaluation-run-dialog'][0]=='dialog'
    assert byid['reference-evaluation-run-form'][0]=='form'
    assert byid['reference-evaluation-run-confirm'][1]['type']=='checkbox'
    assert 'required' in byid['reference-evaluation-run-confirm'][1]
    assert 'disabled' in byid['reference-evaluation-run-start'][1]
    assert byid['reference-evaluation-run-managed'][1]['type']=='button'
    assert 'disabled' in byid['reference-evaluation-run-managed'][1]
    assert any(tag=='a' and attrs.get('href')=='#reference-qa'
               and attrs.get('data-i18n')=='nav.reference' for tag,attrs in elements)
    assert "analysis_mode:'REFERENCE_QA'" in source
    assert "api(`/projects/${state.project}/questions-v3/preview`,'POST',{question})" in source
    assert "api(`/projects/${state.project}/questions-v3`,'POST',{question})" in source
    assert "api(`/projects/${project}/reference-results?limit=50`)" in source
    assert "api(`/projects/${state.project}/reference-evaluations?limit=20`)" in source
    assert "api(`/projects/${state.project}/reference-evaluation-jobs?limit=20`)" in source
    assert "const project=state.project,questions=referenceEvaluationQuestions()" in source
    assert "api(`/projects/${project}/reference-evaluations`,'POST'" in source
    assert "api(`/reference-evaluations/${evaluation.evaluation_id}/clone`,'POST',layoutBound?{profile_id:targetProfile}:undefined)" in source
    assert "api(`/reference-evaluations/${evaluation.evaluation_id}/readiness`)" in source
    assert "api(`/reference-evaluations/${evaluation.evaluation_id}/scorecard`)" in source
    assert "api(`/reference-evaluations/${data.evaluation_id}/items/${item.item_id}/adjudication`,'POST'" in source
    assert "api(`/reference-evaluations/${data.evaluation_id}/items/${item.item_id}/adjudication-history?limit=50`)" in source
    assert "api(`/reference-evaluations/${evaluationId}/jobs`,'POST',{confirmed:true})" in source
    assert "api(`/reference-evaluation-jobs/${job.job_id}/stop`,'POST')" in source
    assert "api(`/projects/${state.project}/reference-evaluations/compare?${query}`)" in source
    assert "new URLSearchParams({baseline_evaluation_id:baseline,candidate_evaluation_id:candidate})" in source
    assert 'compatibleReferenceEvaluations(selected,item)' in source
    assert 'first.selector_version!==second.selector_version' in source
    assert 'selector:evaluation.selector_version' in source
    assert "'reference.evaluationReadinessSelector',{selector:data.selector_version}" in source
    assert 'item.question_key===other.question_key&&item.question===other.question' in source
    assert "sameReferenceProfile(evaluation.profile,activeReferenceProfile())" in source
    assert "data.baseline,'reference.evaluationBaseline'" in source
    assert "data.candidate,'reference.evaluationCandidate'" in source
    assert "api(`/reference-evaluations/${evaluation.evaluation_id}/items/${item.item_id}/preview`,'POST')" in source
    assert "api(`/reference-evaluations/${evaluation.evaluation_id}/items/${item.item_id}/execute`,'POST')" in source
    batch=source[source.index('async function runReferenceEvaluationBatch'):source.index('function renderReferenceEvaluations')]
    assert 'for(const item of pending)' in batch
    assert batch.index('state.referenceEvaluationBusy=true') < batch.index('await api(`/reference-evaluations/${evaluationId}`)')
    assert batch.index('unresolved-model-calls') < batch.index("/execute`,'POST')")
    assert 'await api(`/reference-evaluations/${current.evaluation_id}/items/${item.item_id}/execute`' in batch
    assert "current.items.filter(item=>item.state==='PENDING')" in batch
    assert "data.failure?'reference.evaluationBatchFailed':'reference.evaluationBatchSaved'" in batch
    assert 'stopRequested' in batch and 'Promise.all' not in batch and 'setInterval' not in batch
    assert source.count("current.items.filter(item=>item.state==='PENDING')")==1
    assert source.count("evaluation.items.filter(item=>item.state==='PENDING')")==1
    assert "else if(item.failure)" in source
    assert "$('reference-evaluation-run-dialog').showModal()" in source
    assert "$('reference-evaluation-run-confirm').checked" in source
    assert source.count('runReferenceEvaluationBatch(evaluationId)')==2
    assert "api(`/reference-results/${item.result_id}/review`,'POST'" in source
    assert "location.href=`/api/projects/${state.project}/reference-results/export.json`" in source
    assert "api(`/projects/${state.project}/reference-results/compare?${query}`)" in source
    assert "referenceExecutionReceipts(item.result.execution_receipts)" in source
    assert "referenceExecutionReceipts([item.failure.execution_receipt])" in source
    assert "item.failure.validator_category" in source
    assert "reference.evaluationFailureCategory" in source
    assert "receipt.system_text_bytes+receipt.user_text_bytes" in source
    assert "reference.executionReceiptSafe" in source
    assert "source_text_included" not in source
    assert "new URLSearchParams({baseline_run_id:baseline,candidate_run_id:candidate})" in source
    assert "reference.change.'+item.change" in source
    assert "renderComparisonSide(item.baseline_versions,'reference.baseline')" in source
    assert "renderComparisonSide(item.candidate_versions,'reference.candidate')" in source
    assert "(r.capabilities?.analysis_mode||'LEGACY_ANALYSIS')==='LEGACY_ANALYSIS'" in source
    assert 'Preparation and preview are local.' in translations
    assert 'Only an explicit Run question action may call the model.' in translations
    assert 'without another model call' in translations
    assert 'Each question may send locally selected source-page text' in translations
    assert 'possible document transfer and provider charges' in translations
    assert 'There is no automatic retry.' in translations
    assert 'Browser mode requires this page to remain open.' in translations
    assert 'An interrupted service never resumes automatically' in translations
    assert 'does not decide which answer is correct, newer, or controlling' in translations
    assert 'Preserves the same run, snapshot, and question order. This makes no model call.' in translations
    assert 'does not judge correctness or document precedence' in translations
    assert 'The plan contains no source text and makes no model call.' in translations
    assert 'no authorization to run' in translations
    assert 'CIRP records the human verdict and threshold math but never infers correctness.' in translations
    assert 'uses only saved contract state and explicit engineer verdicts' in translations
    assert 'page.file_name' in source and 'page.selected_text_bytes' in source
    assert 'innerHTML' not in source


def test_vision_output_is_never_rendered_as_an_original_quote():
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    assert "e.extraction_method==='VISION'?'evidence.visionTitle':'evidence.title'" in source
    assert "q.text_basis==='MODEL_VISION_OUTPUT'" in source
    assert '视觉模型输出仅作为整页审查提示' in translations


def test_reference_human_follow_up_is_explicit_local_and_uses_uploaded_documents_only():
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    html=(ROOT/'web/index.html').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    assert '/reference-cases' in source
    assert 'referenceCaseButton({run_id:item.run_id,question:item.question,result_id:item.result_id})' in source
    assert 'evaluation_id:evaluation.evaluation_id,question_id:item.item_id' in source
    assert 'state.documents.forEach(document=>' in source
    assert 'reference.caseReopenHelp' in source and 'reference.previewV8Summary' in source
    assert '/follow-up-results' in source and 'reference.caseFollowupHelp' in source
    assert 'reference.caseFollowupProof' in source
    assert 'source_text_clipped' not in source
    assert 'id="reference-cases-panel"' in html
    assert 'reference.casesDescription' in html
    assert 'Human follow-up' in translations and '人工跟进待办' in translations
    assert 'Link saved new answer' in translations and '关联已保存的新答案' in translations
