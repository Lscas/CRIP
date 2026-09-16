"""Display-only addition: original application contracts remain unchanged."""
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings, ROOT
from scripts.build_cloudflare import build


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
                'budget-limit', 'save-budget', 'project-budget', 'performance',
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
    assert byid['budget-limit'][1]['max'] == byid['project-budget'][1]['max'] == '1000000'
    assert byid['analysis-progress'][1]['role'] == 'progressbar'
    assert byid['analysis-progress'][1]['aria-valuemax'] == '100'


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


def test_live_vision_has_explicit_full_page_data_transfer_disclosure():
    html=(ROOT/'web/index.html').read_text(encoding='utf-8')
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    assert 'id="vision-disclosure"' in html and '整页 PNG 派生图和解析文本发送到 DeepSeek 官方 API' in html
    assert "state.settings.provider==='deepseek'&&state.settings.capabilities?.vision?.ready" in source
    assert 'Live vision sends eligible full-page PNG derivatives' in translations


def test_vision_output_is_never_rendered_as_an_original_quote():
    source=(ROOT/'web/app.js').read_text(encoding='utf-8')
    translations=(ROOT/'web/i18n.js').read_text(encoding='utf-8')
    assert "e.extraction_method==='VISION'?'evidence.visionTitle':'evidence.title'" in source
    assert "q.text_basis==='MODEL_VISION_OUTPUT'" in source
    assert '视觉模型输出仅作为整页审查提示' in translations
