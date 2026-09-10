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
                'filter', 'run-select', 'export-json', 'export-xlsx', 'drawer', 'project-form')
    assert all(id_ in byid for id_ in expected)
    assert byid['project-input'][1]['maxlength'] == '150'
    assert byid['file-input'][1]['type'] == 'file' and 'multiple' in byid['file-input'][1]
    assert 'webkitdirectory' in byid['folder-input'][1]
    assert byid['project-form'][0] == 'form'


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
        fields = list(version_fields(json.loads((ROOT/'spec/schemas'/name).read_text())))
        assert fields and all(version in values and '0.2.4' in values for values in fields)
