"""Local startup invariants using synthetic configuration and no live model calls."""
from argparse import Namespace, ArgumentTypeError
from dataclasses import replace
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
import app.local_entry as local_entry
from app.local_entry import LOCAL_HOSTS, check_port, local_settings, port_number
from app.main import create_app
from app.model_configuration import configured_settings, save_active_configuration
from app.settings import Settings
from scripts import local_deploy


def args(**kw):
    return Namespace(**dict({'port':None,'remote':False,'live':False,'data_dir':None,'no_browser':True},**kw))


def test_mock_ignores_env_and_does_not_read_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv('CIRP_PROVIDER','deepseek')
    monkeypatch.setenv('CIRP_LIVE_API_ENABLED','true')
    monkeypatch.setenv('CIRP_API_KEY','not-a-real-key')
    monkeypatch.setattr(Settings,'from_env',Mock(side_effect=AssertionError('must not load dotenv')))
    s=local_settings(tmp_path)
    assert s.provider=='mock' and not s.live_enabled and s.api_key==''
    assert s.data_dir==tmp_path and s.allowed_hosts==LOCAL_HOSTS
    assert s.project_bytes==10_000_000_000 and not s.remote_enabled


@pytest.mark.parametrize('value,expected', [(None, False), (True, True), (False, False)])
def test_normal_reference_layout_override_never_reads_environment(tmp_path, monkeypatch, value, expected):
    monkeypatch.setenv('CIRP_REFERENCE_LAYOUT_ENABLED', 'true')
    monkeypatch.setattr(Settings, 'from_env', Mock(side_effect=AssertionError('must not load dotenv')))
    result = local_settings(tmp_path, use_saved=False, reference_layout_enabled=value)
    assert result.provider == 'mock' and result.reference_layout_enabled is expected


@pytest.mark.parametrize('value,expected', [(None, False), (True, True), (False, False)])
def test_saved_profile_stub_replaces_only_reference_layout_flag(tmp_path, monkeypatch, value, expected):
    saved = Settings(tmp_path, provider='deepseek', live_enabled=True, api_key='synthetic-key',
                     cheap_model='deepseek-v4-flash', reference_reasoning_effort='low',
                     allowed_hosts=('saved-host',), project_bytes=123, reference_layout_enabled=True)
    monkeypatch.setattr('app.local_entry.load_active_configuration', lambda base: saved)
    monkeypatch.setattr(Settings, 'from_env', Mock(side_effect=AssertionError('must not load dotenv')))
    result = local_settings(tmp_path, reference_layout_enabled=value)
    assert result == replace(saved, reference_layout_enabled=expected)


def test_saved_temp_profile_bytes_do_not_persist_reference_layout_setting(tmp_path, monkeypatch):
    base = Settings(tmp_path, start_worker=False, allowed_hosts=LOCAL_HOSTS)
    selected = configured_settings(base, provider='custom', api_base_url='http://127.0.0.1:11434/v1',
                                   model='qwen3:8b')
    save_active_configuration(selected)
    profile = tmp_path / 'credentials' / 'active-model-profile.json'
    profile_bytes = profile.read_bytes()
    assert b'reference_layout' not in profile_bytes
    monkeypatch.setattr(Settings, 'from_env', Mock(side_effect=AssertionError('must not load dotenv')))
    for value, expected in ((None, False), (True, True), (False, False), (None, False)):
        result = local_settings(tmp_path, reference_layout_enabled=value)
        assert result.provider == selected.provider and result.reference_layout_enabled is expected
        assert profile.read_bytes() == profile_bytes


@pytest.mark.parametrize('value', ['true', 1, object()])
def test_reference_layout_override_requires_real_boolean(tmp_path, value):
    with pytest.raises(ValueError, match='bool or None'):
        local_settings(tmp_path, use_saved=False, reference_layout_enabled=value)


def test_live_requires_complete_configuration(tmp_path,monkeypatch):
    monkeypatch.setattr(Settings,'from_env',lambda:Settings(tmp_path))
    with pytest.raises(ValueError,match='Live API is not configured'):local_settings(live=True)


def test_live_uses_existing_config_but_binds_local(tmp_path,monkeypatch):
    s=Settings(tmp_path,provider='deepseek',live_enabled=True,api_key='synthetic-key')
    monkeypatch.setattr(Settings,'from_env',lambda:s)
    result=local_settings(live=True)
    assert result.data_dir==tmp_path and result.allowed_hosts==LOCAL_HOSTS and result.api_key=='synthetic-key'
    assert result.public()['request_limits']=={
        'text_input_utf8_bytes':32000,'text_extraction_output_tokens':8000,
        'project_answer_input_utf8_bytes':64000,
        'vision_input_utf8_bytes':6000,'vision_output_tokens':2000,
        'verification_input_utf8_bytes':6000,'verification_output_tokens':1400}


@pytest.mark.parametrize('env_value,override', [
    (env_value, override) for env_value in (False, True) for override in (None, True, False)
])
def test_live_reference_layout_preserves_env_value_unless_explicitly_overridden(
        tmp_path, monkeypatch, env_value, override):
    configured = Settings(tmp_path, provider='deepseek', live_enabled=True, api_key='synthetic-key',
                          cheap_model='deepseek-v4-flash', reference_reasoning_effort='low',
                          allowed_hosts=('environment-host',), reference_layout_enabled=env_value)
    monkeypatch.setattr(Settings, 'from_env', lambda: configured)
    monkeypatch.setattr('app.local_entry.remember_enabled', lambda *_args: False)
    expected = env_value if override is None else override
    result = local_settings(live=True, reference_layout_enabled=override)
    assert result == replace(configured, allowed_hosts=LOCAL_HOSTS, reference_layout_enabled=expected)


def test_live_saves_key_only_after_explicit_remember_marker(tmp_path,monkeypatch):
    s=Settings(tmp_path,provider='deepseek',live_enabled=True,api_key='synthetic-key')
    saved=[]
    monkeypatch.setattr(Settings,'from_env',lambda:s)
    monkeypatch.setattr('app.local_entry.remember_enabled',lambda *a:True)
    monkeypatch.setattr('app.local_entry.save_api_key',lambda *a:saved.append(a))
    local_settings(live=True)
    assert saved==[(tmp_path,'deepseek','synthetic-key')]


def test_live_accepts_official_gemini_configuration(tmp_path,monkeypatch):
    s=Settings(tmp_path,provider='gemini',live_enabled=True,
               api_base_url='https://generativelanguage.googleapis.com/v1beta/openai',
               cheap_model='gemini-3.6-flash',api_key='synthetic-key')
    monkeypatch.setattr(Settings,'from_env',lambda:s)
    result=local_settings(live=True)
    assert result.provider=='gemini' and result.public()['thinking']=='minimal'


def test_live_accepts_loopback_openai_compatible_model_without_key_or_price(tmp_path,monkeypatch):
    s=Settings(tmp_path,provider='custom-0123456789abcdef',live_enabled=True,
               api_base_url='http://127.0.0.1:11434/v1',cheap_model='qwen3:8b')
    monkeypatch.setattr(Settings,'from_env',lambda:s)
    result=local_settings(live=True)
    assert result.live_errors()==[] and result.is_local_model()
    assert result.public()['mode']=='Local model mode' and result.inference_parameters()=={}


def test_standard_local_start_reuses_explicitly_saved_local_profile(tmp_path):
    base=Settings(tmp_path,start_worker=False,allowed_hosts=LOCAL_HOSTS)
    selected=configured_settings(
        base,provider='custom',api_base_url='http://127.0.0.1:11434/v1',
        model='qwen3:8b')
    save_active_configuration(selected)
    loaded=local_settings(tmp_path)
    assert loaded.provider==selected.provider and loaded.is_local_model()
    assert local_settings(tmp_path,use_saved=False).provider=='mock'


@pytest.mark.parametrize('base_url',[
    'http://remote.example/v1','http://127.0.0.1.example/v1','http://2130706433/v1',
])
def test_custom_remote_endpoint_cannot_use_insecure_or_ambiguous_loopback_url(tmp_path,base_url):
    s=Settings(tmp_path,provider='custom-0123456789abcdef',live_enabled=True,
               api_base_url=base_url,cheap_model='model',api_key='synthetic-valid-key')
    assert s.live_errors()


def test_gemini_env_selects_official_defaults_without_dotenv(monkeypatch):
    monkeypatch.setattr('app.settings.load_dotenv',lambda *a,**kw:None)
    for name in ('CIRP_API_BASE_URL','CIRP_CHEAP_MODEL','CIRP_DATA_DIR',
                 'CIRP_INPUT_LIMIT_BYTES','CIRP_OUTPUT_LIMIT_TOKENS'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('CIRP_PROVIDER','gemini')
    monkeypatch.setenv('CIRP_LIVE_API_ENABLED','true')
    monkeypatch.setenv('CIRP_API_KEY','synthetic-key')
    configured=Settings.from_env()
    assert configured.api_base_url=='https://generativelanguage.googleapis.com/v1beta/openai'
    assert configured.cheap_model=='gemini-3.6-flash' and configured.live_errors()==[]
    assert configured.min_request_interval_seconds==15
    assert configured.input_limit==64000 and configured.output_limit==8000


@pytest.mark.parametrize('interval',[float('nan'),-1,301])
def test_invalid_request_interval_blocks_live_configuration(tmp_path,interval):
    s=Settings(tmp_path,provider='deepseek',live_enabled=True,api_key='synthetic-key',
               min_request_interval_seconds=interval)
    assert any('Minimum model-request interval' in error for error in s.live_errors())


def test_gemini_launcher_keeps_key_out_of_files_and_arguments():
    source=Path('start-gemini-live.ps1').read_text(encoding='utf-8')
    assert 'start-local.cmd" --install-only' in source
    assert 'scripts\\gemini_local_setup.py" --provider gemini' in source
    assert 'Windows DPAPI current-user encryption' in source
    assert 'Read-Host' not in source and '--api-key' not in source.lower() and 'Out-File' not in source


def test_deepseek_launcher_uses_official_v4_flash_without_key_arguments():
    source=Path('start-deepseek-live.ps1').read_text(encoding='utf-8')
    assert 'scripts\\deepseek_local_setup.py" --setup-port $SetupPort --app-port $Port' in source
    assert 'Windows DPAPI current-user encryption' in source
    assert 'Read-Host' not in source and '--api-key' not in source.lower() and 'Out-File' not in source


def test_custom_launcher_uses_loopback_setup_page_without_key_arguments():
    source=Path('start-custom-model.ps1').read_text(encoding='utf-8')
    assert 'gemini_local_setup.py" --provider custom' in source
    assert 'saved only when Windows DPAPI storage is selected' in source
    assert 'Read-Host' not in source and '--api-key' not in source.lower()


def test_dependency_children_do_not_inherit_secrets(monkeypatch,tmp_path):
    seen=[]
    def run(*args,**kwargs):
        seen.append(kwargs['env']);return Namespace(returncode=0)
    monkeypatch.setenv('CIRP_API_KEY','synthetic-key')
    monkeypatch.setenv('ANOTHER_SECRET','synthetic-secret')
    monkeypatch.setenv('UNRELATED_SETTING','kept')
    monkeypatch.setattr(local_deploy.subprocess,'run',run)
    assert local_deploy.dependency_probe(Path(sys.executable),tmp_path)
    assert seen[0]['UNRELATED_SETTING']=='kept'
    assert seen[0]['PYTHONUTF8']=='1'
    assert 'CIRP_API_KEY' not in seen[0] and 'ANOTHER_SECRET' not in seen[0]


def test_local_live_application_child_only_keeps_selected_cirp_key(monkeypatch):
    monkeypatch.setenv('CIRP_API_KEY','selected-key')
    for name in ('AWS_ACCESS_KEY_ID','SSH_PRIVATE_KEY','PROXY_AUTHORIZATION','DB_PASSWD','SERVICE_CREDENTIAL'):
        monkeypatch.setenv(name,'must-not-reach-app')
    monkeypatch.setenv('CIRP_PROVIDER','deepseek')
    env=local_deploy.application_child_env(live=True,remote=False)
    assert env['CIRP_API_KEY']=='selected-key' and env['CIRP_PROVIDER']=='deepseek'
    assert all('must-not-reach-app' not in value for value in env.values())


def test_remote_application_child_never_inherits_shell_secrets(monkeypatch):
    for name in ('CIRP_API_KEY', 'AWS_ACCESS_KEY_ID', 'DB_PASSWORD', 'SSH_PRIVATE_KEY',
                 'UNRELATED_TOKEN', 'SERVICE_CREDENTIAL'):
        monkeypatch.setenv(name, 'must-not-reach-remote-preview')
    monkeypatch.setenv('UNRELATED_SETTING', 'kept')
    env = local_deploy.application_child_env(live=False, remote=True)
    assert env['UNRELATED_SETTING'] == 'kept'
    assert all('must-not-reach-remote-preview' not in value for value in env.values())


@pytest.mark.parametrize('value',[0,80,1023,65536,-1])
def test_invalid_ports(value):
    with pytest.raises(ArgumentTypeError):port_number(str(value))


def test_port_busy_never_terminates_owner():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));sock.listen()
        with pytest.raises(ValueError,match='No existing process was stopped'):check_port(sock.getsockname()[1])
        assert sock.fileno()>=0


def test_local_command_safe_defaults():
    cmd=local_deploy.make_command(Path(sys.executable),args())
    assert cmd[1:]==['-m','app.local_entry','--port','8000','--no-browser']
    assert '--live' not in cmd


def test_local_command_can_explicitly_bypass_saved_profile():
    cmd=local_deploy.make_command(Path(sys.executable),args(mock=True))
    assert cmd[-1]=='--mock' and '--live' not in cmd


@pytest.mark.parametrize('value,flag', [(True, '--reference-layout'), (False, '--no-reference-layout')])
def test_local_launcher_forwards_explicit_reference_layout_switch(value, flag):
    command = local_deploy.make_command(Path(sys.executable), args(reference_layout=value))
    assert flag in command


def test_legacy_launcher_namespace_without_reference_layout_remains_compatible():
    command = local_deploy.make_command(Path(sys.executable), args())
    assert '--reference-layout' not in command and '--no-reference-layout' not in command


def test_remote_command_uses_separate_port():
    cmd=local_deploy.make_command(Path(sys.executable),args(remote=True))
    assert cmd[1:]==['scripts/remote_preview.py','--port','8001']


@pytest.mark.parametrize('kw',[{'remote':True,'live':True},{'remote':True,'data_dir':'x'},
                               {'remote':True,'reference_layout':True},{'remote':True,'reference_layout':False},{'port':80}])
def test_bad_combination_rejected_before_install(kw):
    with pytest.raises(ValueError):local_deploy.make_command(Path(sys.executable),args(**kw))


@pytest.mark.parametrize('flag', ['--reference-layout', '--no-reference-layout'])
def test_remote_reference_layout_is_rejected_before_any_install_or_start(flag, monkeypatch):
    monkeypatch.setattr(local_deploy, 'ensure_environment', Mock(side_effect=AssertionError('no install')))
    monkeypatch.setattr(local_deploy.subprocess, 'Popen', Mock(side_effect=AssertionError('no start')))
    probe = Mock(side_effect=AssertionError('no dependency probe'))
    cloudflared = Mock(side_effect=AssertionError('no cloudflared lookup'))
    monkeypatch.setattr(local_deploy, 'dependency_probe', probe)
    monkeypatch.setattr('scripts.remote_preview.find_cloudflared', cloudflared)
    assert local_deploy.main(['--remote', flag]) == 2
    probe.assert_not_called()
    cloudflared.assert_not_called()


@pytest.mark.parametrize('argv,expected', [
    (['--no-browser'], None),
    (['--no-browser', '--reference-layout'], True),
    (['--no-browser', '--no-reference-layout'], False),
])
def test_local_entry_cli_wires_reference_layout_tristate_before_start(argv, expected, monkeypatch):
    seen = []
    def stop_after_parse(*_args, **kwargs):
        seen.append(kwargs['reference_layout_enabled'])
        raise ValueError('synthetic stop')
    monkeypatch.setattr(local_entry, 'local_settings', stop_after_parse)
    monkeypatch.setattr(local_entry, 'check_port', lambda _: None)
    assert local_entry.main(argv) == 2
    assert seen == [expected]


@pytest.mark.parametrize('argv,expected', [
    (['--check'], None),
    (['--check', '--reference-layout'], True),
    (['--check', '--no-reference-layout'], False),
])
def test_launcher_cli_wires_reference_layout_tristate_before_side_effects(argv, expected, monkeypatch):
    seen = []
    monkeypatch.setattr(local_deploy, 'make_command', lambda _python, parsed: seen.append(parsed.reference_layout) or [])
    monkeypatch.setattr(local_deploy, 'venv_python', lambda: Path(sys.executable))
    monkeypatch.setattr(local_deploy, 'dependency_probe', lambda _python: True)
    assert local_deploy.main(argv) == 0
    assert seen == [expected]


@pytest.mark.parametrize('startup_enabled,candidate_enabled', [(True, False), (False, True)])
def test_model_restart_cannot_change_process_reference_layout_flag(
        tmp_path, monkeypatch, startup_enabled, candidate_enabled):
    startup = Settings(tmp_path, start_worker=False, reference_layout_enabled=startup_enabled)
    candidate = replace(startup, provider='mock', reference_layout_enabled=candidate_enabled)
    applications = []
    class Application:
        def __init__(self, settings):
            self.settings = settings
            self.state = SimpleNamespace()
    class Server:
        count = 0
        def __init__(self, config):
            self.config = config
            self.started = False
            self.should_exit = False
            self.index = Server.count
            Server.count += 1
        def run(self):
            self.started = True
            if self.index == 0:
                self.config.application.state.request_model_restart(candidate)
    def create(settings):
        app = Application(settings)
        applications.append(app)
        return app
    monkeypatch.setattr(local_entry, 'local_settings', lambda *_args, **_kwargs: startup)
    monkeypatch.setattr(local_entry, 'check_port', lambda _: None)
    monkeypatch.setattr(local_entry, 'create_app', create)
    monkeypatch.setattr(local_entry.uvicorn, 'Config', lambda application, **_kwargs: SimpleNamespace(application=application))
    monkeypatch.setattr(local_entry.uvicorn, 'Server', Server)
    assert local_entry.main(['--no-browser']) == 0
    assert [app.settings.reference_layout_enabled for app in applications] == [startup_enabled, startup_enabled]


@pytest.mark.parametrize('enabled', [False, True])
def test_local_settings_capability_reports_the_effective_reference_layout_flag(tmp_path, enabled):
    settings = local_settings(tmp_path, use_saved=False, reference_layout_enabled=enabled)
    with TestClient(create_app(replace(settings, start_worker=False)), base_url='http://127.0.0.1',
                    headers={'X-CIRP-Client': 'browser'}) as client:
        response = client.get('/api/settings')
        assert response.status_code == 200
        assert response.json()['capabilities']['reference_layout_v9']['enabled'] is enabled
        assert client.app.state.db.one('SELECT COUNT(*) AS n FROM model_calls')['n'] == 0


def test_no_implicit_install_in_current_env(monkeypatch,tmp_path):
    monkeypatch.setattr(local_deploy,'dependency_probe',lambda *a:True)
    monkeypatch.setattr(local_deploy.subprocess,'run',Mock(side_effect=AssertionError('no install')))
    assert local_deploy.ensure_environment(use_current=True,root=tmp_path)==Path(sys.executable)


def test_install_failure_not_marked_success(tmp_path,monkeypatch):
    python=local_deploy.venv_python(tmp_path);python.parent.mkdir(parents=True);python.write_text('fake')
    (tmp_path/'requirements-app.txt').write_text('fake-package\n')
    monkeypatch.setattr(local_deploy.subprocess,'run',lambda *a,**kw:Namespace(returncode=1))
    with pytest.raises(RuntimeError,match='service was not started'):local_deploy.ensure_environment(root=tmp_path)
    assert not (tmp_path/'.venv/cirp-dependencies.json').exists()


def test_unchanged_install_skips_pip(tmp_path,monkeypatch):
    import json
    python=local_deploy.venv_python(tmp_path);python.parent.mkdir(parents=True);python.write_text('fake')
    (tmp_path/'requirements-app.txt').write_text('fake-package\n')
    (tmp_path/'.venv/cirp-dependencies.json').write_text(json.dumps({'requirements_sha256':local_deploy.requirements_digest(tmp_path)}))
    monkeypatch.setattr(local_deploy,'dependency_probe',lambda *a:True)
    monkeypatch.setattr(local_deploy.subprocess,'run',Mock(side_effect=AssertionError('no pip')))
    assert local_deploy.ensure_environment(root=tmp_path)==python


def test_default_root_relative_to_script_not_working_directory(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    from app.settings import ROOT
    assert local_settings(use_saved=False).data_dir==ROOT/'.local'
