"""Local startup invariants using synthetic configuration and no live model calls."""
from argparse import Namespace, ArgumentTypeError
from decimal import Decimal
from pathlib import Path
import socket
import sys
from unittest.mock import Mock

import pytest
from app.local_entry import LOCAL_HOSTS, check_port, local_settings, port_number
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


def test_live_requires_complete_configuration(tmp_path,monkeypatch):
    monkeypatch.setattr(Settings,'from_env',lambda:Settings(tmp_path))
    with pytest.raises(ValueError,match='Live API is not configured'):local_settings(live=True)


def test_live_uses_existing_config_but_binds_local(tmp_path,monkeypatch):
    s=Settings(tmp_path,provider='deepseek',live_enabled=True,prices_confirmed=True,
               api_key='synthetic-key',input_rate=Decimal(1),output_rate=Decimal(2))
    monkeypatch.setattr(Settings,'from_env',lambda:s)
    result=local_settings(live=True)
    assert result.data_dir==tmp_path and result.allowed_hosts==LOCAL_HOSTS and result.api_key=='synthetic-key'
    assert result.public()['request_limits']=={
        'text_input_utf8_bytes':32000,'text_extraction_output_tokens':8000,
        'vision_input_utf8_bytes':6000,'vision_output_tokens':2000,
        'verification_input_utf8_bytes':6000,'verification_output_tokens':1400}


def test_live_saves_key_only_after_explicit_remember_marker(tmp_path,monkeypatch):
    s=Settings(tmp_path,provider='deepseek',live_enabled=True,prices_confirmed=True,
               api_key='synthetic-key',input_rate=Decimal(1),output_rate=Decimal(2))
    saved=[]
    monkeypatch.setattr(Settings,'from_env',lambda:s)
    monkeypatch.setattr('app.local_entry.remember_enabled',lambda *a:True)
    monkeypatch.setattr('app.local_entry.save_api_key',lambda *a:saved.append(a))
    local_settings(live=True)
    assert saved==[(tmp_path,'deepseek','synthetic-key')]


def test_live_accepts_official_gemini_configuration(tmp_path,monkeypatch):
    s=Settings(tmp_path,provider='gemini',live_enabled=True,prices_confirmed=True,
               api_base_url='https://generativelanguage.googleapis.com/v1beta/openai',
               cheap_model='gemini-3.6-flash',api_key='synthetic-key',
               input_rate=Decimal('7.5'),output_rate=Decimal('37.5'))
    monkeypatch.setattr(Settings,'from_env',lambda:s)
    result=local_settings(live=True)
    assert result.provider=='gemini' and result.public()['thinking']=='minimal'


def test_gemini_env_selects_official_defaults_without_dotenv(monkeypatch):
    monkeypatch.setattr('app.settings.load_dotenv',lambda *a,**kw:None)
    for name in ('CIRP_API_BASE_URL','CIRP_CHEAP_MODEL','CIRP_DATA_DIR',
                 'CIRP_INPUT_LIMIT_BYTES','CIRP_OUTPUT_LIMIT_TOKENS'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('CIRP_PROVIDER','gemini')
    monkeypatch.setenv('CIRP_LIVE_API_ENABLED','true')
    monkeypatch.setenv('CIRP_API_KEY','synthetic-key')
    monkeypatch.setenv('CIRP_PRICES_CONFIRMED','true')
    monkeypatch.setenv('CIRP_INPUT_CNY_PER_MILLION','7.5')
    monkeypatch.setenv('CIRP_OUTPUT_CNY_PER_MILLION','37.5')
    configured=Settings.from_env()
    assert configured.api_base_url=='https://generativelanguage.googleapis.com/v1beta/openai'
    assert configured.cheap_model=='gemini-3.6-flash' and configured.live_errors()==[]
    assert configured.min_request_interval_seconds==15
    assert configured.input_limit==32000 and configured.output_limit==8000


@pytest.mark.parametrize('interval',[float('nan'),-1,301])
def test_invalid_request_interval_blocks_live_configuration(tmp_path,interval):
    s=Settings(tmp_path,provider='deepseek',live_enabled=True,prices_confirmed=True,
               api_key='synthetic-key',input_rate=Decimal(1),output_rate=Decimal(2),
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


def test_remote_command_uses_separate_port():
    cmd=local_deploy.make_command(Path(sys.executable),args(remote=True))
    assert cmd[1:]==['scripts/remote_preview.py','--port','8001']


@pytest.mark.parametrize('kw',[{'remote':True,'live':True},{'remote':True,'data_dir':'x'},{'port':80}])
def test_bad_combination_rejected_before_install(kw):
    with pytest.raises(ValueError):local_deploy.make_command(Path(sys.executable),args(**kw))


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
    assert local_settings().data_dir==ROOT/'.local'
