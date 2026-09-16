"""One-use loopback Gemini setup page; no real API calls."""
from argparse import ArgumentTypeError
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from scripts import gemini_local_setup as setup


def test_live_child_env_scrubs_inherited_secrets(monkeypatch):
    monkeypatch.setenv("CIRP_API_KEY", "inherited")
    secret_names = (
        "OTHER_TOKEN", "AWS_ACCESS_KEY_ID", "SSH_PRIVATE_KEY",
        "PROXY_AUTHORIZATION", "DB_PASSWD", "SERVICE_CREDENTIAL",
    )
    for name in secret_names:
        monkeypatch.setenv(name, "inherited")
    monkeypatch.setenv("UNRELATED", "kept")
    env = setup.live_child_env("synthetic-new-key")
    assert env["CIRP_API_KEY"] == "synthetic-new-key"
    assert not set(secret_names) & set(env) and env["UNRELATED"] == "kept"
    assert env["CIRP_PROVIDER"] == "gemini"
    assert env["CIRP_CHEAP_MODEL"] == "gemini-3.6-flash"
    assert env["CIRP_LIVE_API_ENABLED"] == "true"
    assert env["CIRP_MIN_REQUEST_INTERVAL_SECONDS"] == "15"
    assert env["CIRP_INPUT_LIMIT_BYTES"] == "32000"
    assert env["CIRP_OUTPUT_LIMIT_TOKENS"] == "8000"
    assert env["CIRP_VISION_ENABLED"] == "false"


def test_deepseek_v4_flash_profile_uses_official_endpoint_and_conservative_rates():
    env = setup.live_child_env("synthetic-new-key", setup.DEEPSEEK_PROFILE)
    assert env["CIRP_PROVIDER"] == "deepseek"
    assert env["CIRP_API_BASE_URL"] == "https://api.deepseek.com"
    assert env["CIRP_CHEAP_MODEL"] == "deepseek-v4-flash"
    assert env["CIRP_INPUT_CNY_PER_MILLION"] == "4.40"
    assert env["CIRP_OUTPUT_CNY_PER_MILLION"] == "13.20"
    assert env["CIRP_MIN_REQUEST_INTERVAL_SECONDS"] == "1"
    assert env["CIRP_INPUT_LIMIT_BYTES"] == "32000"
    assert env["CIRP_OUTPUT_LIMIT_TOKENS"] == "8000"
    assert env["CIRP_VISION_ENABLED"] == "true"
    assert env["CIRP_VISION_MODEL"] == "deepseek-v4-flash-vision-exp"
    rendered = setup.page("synthetic-csrf", profile=setup.DEEPSEEK_PROFILE).decode("utf-8")
    assert "DeepSeek V4 Flash" in rendered and "type=password" in rendered
    assert "full-page PNG derivatives and parsed text are sent to the official DeepSeek API" in rendered
    assert "authorize the stated text/image transfer" in rendered


def test_custom_profile_accepts_loopback_without_key_and_rejects_insecure_remote():
    fields={"base_url":["http://127.0.0.1:11434/v1"],"model":["qwen3:8b"],
            "input_rate":["0"],"output_rate":["0"]}
    profile=setup.custom_profile(fields,"")
    env=setup.live_child_env("",profile)
    assert profile.local and env["CIRP_PROVIDER"].startswith("custom-")
    assert env["CIRP_API_BASE_URL"]=="http://127.0.0.1:11434/v1"
    assert env["CIRP_CHEAP_MODEL"]=="qwen3:8b" and env["CIRP_API_KEY"]==""
    remote=setup.custom_profile({**fields,"base_url":["https://api.example/v1"],
                                 "input_rate":["1"],"output_rate":["2"]},"synthetic-valid-key")
    assert not remote.local and remote.provider != profile.provider
    with pytest.raises(ValueError,match="HTTPS"):
        setup.custom_profile({**fields,"base_url":["http://remote.example/v1"],
                              "input_rate":["1"],"output_rate":["2"]},"synthetic-valid-key")
    rendered=setup.page("synthetic-csrf",profile=setup.CUSTOM_PROFILE).decode("utf-8")
    assert "OpenAI-compatible text endpoint" in rendered and "name=base_url" in rendered
    assert "optional for a loopback local model" in rendered and "name=remember" in rendered
    assert "for this endpoint/model" in rendered


def test_deepseek_powershell_launcher_discloses_visual_data_transfer_before_key_prompt():
    source = (setup.ROOT / "start-deepseek-live.ps1").read_text(encoding="utf-8")
    disclosure = "eligible full-page PNG derivatives and parsed text are sent to the official DeepSeek API"
    assert disclosure in source and "Windows DPAPI current-user encryption" in source
    assert "scripts\\deepseek_local_setup.py" in source and "Read-Host" not in source


@pytest.mark.parametrize("value", ["", "short", "with space", "密钥不是ASCII", "x" * 257])
def test_invalid_key_shapes_are_rejected(value):
    assert not setup.valid_api_key(value)


def test_setup_page_uses_password_post_and_no_key_storage_language():
    rendered = setup.page("synthetic-csrf").decode("utf-8")
    assert "type=password" in rendered and "method=post" in rendered
    assert "synthetic-csrf" in rendered
    assert "does not write the key to a URL, application log, or plaintext .env" in rendered
    assert "Windows DPAPI" in rendered and "name=remember" in rendered
    assert "user-selected cumulative limit" in rendered


@pytest.mark.parametrize("value", ["80", "1023", "65536"])
def test_invalid_ports(value):
    with pytest.raises(ArgumentTypeError):
        setup.port_number(value)


def test_module_never_writes_env_or_logs_requests():
    source = open(setup.__file__, encoding="utf-8").read()
    assert ".write_text(" not in source and ".write_bytes(" not in source
    assert "def log_message" in source
    assert "CIRP_API_KEY" in source


class FakeChild:
    def poll(self):
        return None

    def wait(self):
        return 0


def post(server, fields):
    host, port = server.server_address
    request = Request(
        f"http://{host}:{port}/start",
        data=urlencode(fields).encode("ascii"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return urlopen(request, timeout=2)


@pytest.mark.parametrize("profile", [setup.GEMINI_PROFILE, setup.DEEPSEEK_PROFILE])
def test_loopback_http_rejects_bad_csrf_then_starts_once(monkeypatch, tmp_path, profile):
    calls = []

    def popen(command, **kwargs):
        captured = dict(kwargs)
        captured["env"] = dict(kwargs["env"])
        calls.append((list(command), captured))
        return FakeChild()

    monkeypatch.setattr(setup.subprocess, "Popen", popen)
    monkeypatch.setattr(setup, "enable_remember", lambda *a: None)
    monkeypatch.setattr(setup, "save_api_key", lambda *a: None)
    monkeypatch.setattr(setup, "forget_api_key", lambda *a: None)
    server = setup.SetupServer(("127.0.0.1", 0), 8010, profile, data_dir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.server_address[0] == "127.0.0.1"
        with pytest.raises(HTTPError) as rejected:
            post(server, {"csrf": "wrong", "approved": "yes", "remember": "yes", "api_key": "synthetic-valid-key"})
        assert rejected.value.code == 400 and calls == []
        with post(server, {"csrf": server.csrf_token, "approved": "yes", "remember": "yes", "api_key": "synthetic-valid-key"}) as response:
            assert response.status == 202
        thread.join(timeout=2)
        assert not thread.is_alive() and len(calls) == 1
        command, kwargs = calls[0]
        assert "synthetic-valid-key" not in command
        assert kwargs["env"]["CIRP_API_KEY"] == "synthetic-valid-key"
        assert kwargs["env"]["CIRP_PROVIDER"] == profile.provider
    finally:
        server.server_close()


def test_custom_loopback_form_starts_without_api_key(monkeypatch,tmp_path):
    calls=[]
    def popen(command,**kwargs):
        captured={**kwargs,"env":dict(kwargs["env"])}
        calls.append((command,captured))
        return FakeChild()
    monkeypatch.setattr(setup.subprocess,"Popen",popen)
    server=setup.SetupServer(("127.0.0.1",0),8010,setup.CUSTOM_PROFILE,data_dir=tmp_path)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        fields={"csrf":server.csrf_token,"approved":"yes","remember":"yes","api_key":"",
                "base_url":"http://127.0.0.1:11434/v1","model":"qwen3:8b",
                "input_rate":"0","output_rate":"0"}
        with post(server,fields) as response:assert response.status==202
        thread.join(timeout=2)
        assert len(calls)==1 and calls[0][1]["env"]["CIRP_API_KEY"]==""
        assert calls[0][1]["env"]["CIRP_PROVIDER"].startswith("custom-")
    finally:
        server.server_close()


def test_custom_remote_saved_key_is_scoped_to_endpoint_and_model(monkeypatch,tmp_path):
    calls=[];loaded=[];stored=[]
    def popen(command,**kwargs):
        calls.append((command,{**kwargs,"env":dict(kwargs["env"])}));return FakeChild()
    monkeypatch.setattr(setup.subprocess,"Popen",popen)
    monkeypatch.setattr(setup,"load_api_key",lambda data_dir,provider:loaded.append(provider) or "synthetic-valid-key")
    monkeypatch.setattr(setup,"enable_remember",lambda data_dir,provider:stored.append(provider))
    monkeypatch.setattr(setup,"save_api_key",lambda data_dir,provider,key:stored.append(provider))
    server=setup.SetupServer(("127.0.0.1",0),8010,setup.CUSTOM_PROFILE,data_dir=tmp_path)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        fields={"csrf":server.csrf_token,"approved":"yes","remember":"yes","api_key":"",
                "base_url":"https://api.example/v1","model":"customer-model",
                "input_rate":"1","output_rate":"2"}
        with post(server,fields) as response:assert response.status==202
        thread.join(timeout=2)
        assert len(loaded)==len(calls)==1 and loaded[0].startswith("custom-")
        assert stored==[loaded[0],loaded[0]]
        assert calls[0][1]["env"]["CIRP_API_KEY"]=="synthetic-valid-key"
        assert "synthetic-valid-key" not in calls[0][0]
    finally:
        server.server_close()


def test_existing_child_returns_conflict_without_second_spawn(monkeypatch):
    monkeypatch.setattr(setup.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not spawn"))
    server = setup.SetupServer(("127.0.0.1", 0), 8010)
    server.child = FakeChild()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPError) as rejected:
            post(server, {"csrf": server.csrf_token, "approved": "yes", "api_key": "synthetic-valid-key"})
        assert rejected.value.code == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_saved_current_user_key_starts_without_setup_page(monkeypatch, tmp_path):
    monkeypatch.setenv("CIRP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(setup, "remember_enabled", lambda *a: True)
    monkeypatch.setattr(setup, "load_api_key", lambda *a: "synthetic-valid-key")
    children = []
    monkeypatch.setattr(setup, "start_live_child", lambda *a: children.append(a) or FakeChild())
    assert setup.main(["--provider", "deepseek", "--app-port", "8010"]) == 0
    assert len(children) == 1 and children[0][1] == setup.DEEPSEEK_PROFILE
