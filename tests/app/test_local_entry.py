"""本机启动不变量。合成配置，不调用真实模型。"""
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
    with pytest.raises(ValueError,match='真实 API 尚未配置'):local_settings(live=True)


def test_live_uses_existing_config_but_binds_local(tmp_path,monkeypatch):
    s=Settings(tmp_path,provider='deepseek',live_enabled=True,prices_confirmed=True,
               api_key='synthetic-key',input_rate=Decimal(1),output_rate=Decimal(2))
    monkeypatch.setattr(Settings,'from_env',lambda:s)
    result=local_settings(live=True)
    assert result.data_dir==tmp_path and result.allowed_hosts==LOCAL_HOSTS and result.api_key=='synthetic-key'


@pytest.mark.parametrize('value',[0,80,1023,65536,-1])
def test_invalid_ports(value):
    with pytest.raises(ArgumentTypeError):port_number(str(value))


def test_port_busy_never_terminates_owner():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));sock.listen()
        with pytest.raises(ValueError,match='不会关闭已有程序'):check_port(sock.getsockname()[1])
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
    with pytest.raises(RuntimeError,match='未启动服务'):local_deploy.ensure_environment(root=tmp_path)
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
