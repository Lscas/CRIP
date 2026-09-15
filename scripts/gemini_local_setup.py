"""One-use loopback form for starting CIRP with an in-memory provider key."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.security import environment_without_secrets  # noqa: E402
from app.local_credentials import (  # noqa: E402
    LocalCredentialError,
    enable_remember,
    forget_api_key,
    load_api_key,
    remember_enabled,
    save_api_key,
)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_MODEL = "gemini-3.6-flash"
MAX_FORM_BYTES = 1024


@dataclass(frozen=True)
class ProviderProfile:
    provider: str
    display_name: str
    base_url: str
    model: str
    input_rate: str
    output_rate: str
    min_interval: str


GEMINI_PROFILE = ProviderProfile(
    "gemini", "Gemini 3.6 Flash", GEMINI_BASE_URL, GEMINI_MODEL,
    "7.50", "37.50", "15",
)
DEEPSEEK_PROFILE = ProviderProfile(
    "deepseek", "DeepSeek V4 Flash", "https://api.deepseek.com", "deepseek-v4-flash",
    "4.40", "13.20", "1",
)
PROFILES = {profile.provider: profile for profile in (GEMINI_PROFILE, DEEPSEEK_PROFILE)}


def live_child_env(api_key: str, profile: ProviderProfile = GEMINI_PROFILE) -> dict[str, str]:
    """Build the live child environment without inheriting unrelated secrets."""
    env = environment_without_secrets()
    env.update(
        {
            "CIRP_PROVIDER": profile.provider,
            "CIRP_LIVE_API_ENABLED": "true",
            "CIRP_API_BASE_URL": profile.base_url,
            "CIRP_API_KEY": api_key,
            "CIRP_CHEAP_MODEL": profile.model,
            "CIRP_VISION_ENABLED": "true" if profile.provider == "deepseek" else "false",
            "CIRP_VISION_MODEL": "deepseek-v4-flash-vision-exp",
            "CIRP_PRICES_CONFIRMED": "true",
            "CIRP_INPUT_CNY_PER_MILLION": profile.input_rate,
            "CIRP_OUTPUT_CNY_PER_MILLION": profile.output_rate,
            "CIRP_INPUT_LIMIT_BYTES": "32000",
            "CIRP_OUTPUT_LIMIT_TOKENS": "8000",
            "CIRP_MIN_REQUEST_INTERVAL_SECONDS": profile.min_interval,
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def valid_api_key(value: str) -> bool:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return 16 <= len(encoded) <= 256 and not any(char.isspace() for char in value)


def page(
    csrf_token: str, *, message: str = "", profile: ProviderProfile = GEMINI_PROFILE,
    remember: bool = False,
) -> bytes:
    notice = f"<p role=alert>{message}</p>" if message else ""
    checked = " checked" if remember else ""
    return f"""<!doctype html>
<html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
  <title>Secure CIRP {profile.display_name} startup</title>
  <h1>Secure CIRP {profile.display_name} startup</h1>
  <p>This tool listens only on local address 127.0.0.1 and does not write the key to a URL, application log, or plaintext .env. Do not let the browser save the key.</p>
  <p>When Save is selected, the key is written only to a Windows DPAPI current-user encrypted file. Other Windows users and copies moved to another computer cannot decrypt it directly.</p>
  <p>The budget ledger uses conservative upper-bound rates: input ¥{profile.input_rate}/million tokens and output ¥{profile.output_rate}/million tokens. The cumulative project limit remains ¥300.</p>
  {"<p><strong>Data disclosure:</strong> After Start analysis is clicked, eligible full-page PNG derivatives and parsed text are sent to the official DeepSeek API. Page images and text calls share the same cumulative budget gate.</p>" if profile.provider == "deepseek" else ""}
{notice}
<form method=post action=/start autocomplete=off>
  <input type=hidden name=csrf value="{csrf_token}">
  <p><label>New {profile.display_name} API key<br><input type=password name=api_key required autofocus size=72></label></p>
  <p><label><input type=checkbox name=remember value=yes{checked}> Encrypt and save for the current user with Windows DPAPI for future automatic startup</label></p>
  <p><label><input type=checkbox name=approved value=yes required> I confirm the budget upper bounds and authorize the stated text/image transfer and API charges after Start analysis is clicked</label></p>
  <button type=submit>Start CIRP securely</button>
</form>
""".encode("utf-8")


def start_live_child(api_key: str, profile: ProviderProfile, app_port: int) -> subprocess.Popen[bytes]:
    env = live_child_env(api_key, profile)
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "app.local_entry", "--live", "--port", str(app_port), "--no-browser"],
            cwd=ROOT,
            env=env,
        )
    finally:
        env.pop("CIRP_API_KEY", None)


class SetupServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], app_port: int, profile: ProviderProfile = GEMINI_PROFILE,
        *, data_dir: Path = ROOT / ".local", initial_message: str = "",
    ):
        super().__init__(address, SetupHandler)
        self.app_port = app_port
        self.profile = profile
        self.data_dir = data_dir.resolve()
        self.remember = remember_enabled(self.data_dir, profile.provider)
        self.initial_message = initial_message
        self.csrf_token = secrets.token_hex(24)
        self.child: subprocess.Popen[bytes] | None = None
        self.start_lock = threading.Lock()

    def handle_error(self, request, client_address) -> None:  # pragma: no cover - defensive silence
        print("[SETUP] A local setup request failed; no request data was logged.", flush=True)


class SetupHandler(BaseHTTPRequestHandler):
    server: SetupServer

    def log_message(self, format: str, *args) -> None:
        return

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlsplit(self.path).path != "/":
            self._send(404, b"Not found")
            return
        self._send(200, page(
            self.server.csrf_token, message=self.server.initial_message,
            profile=self.server.profile, remember=self.server.remember,
        ))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlsplit(self.path).path != "/start":
            self._send(404, b"Not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 1 <= length <= MAX_FORM_BYTES:
            self._send(400, page(
                self.server.csrf_token, message="The submission is invalid. Try again.",
                profile=self.server.profile, remember=self.server.remember,
            ))
            return
        body = self.rfile.read(length)
        try:
            fields = parse_qs(body.decode("ascii"), strict_parsing=True, max_num_fields=4)
        except (UnicodeDecodeError, ValueError):
            self._send(400, page(
                self.server.csrf_token, message="The submission is invalid. Try again.",
                profile=self.server.profile, remember=self.server.remember,
            ))
            return
        api_key = fields.get("api_key", [""])[0].strip()
        csrf = fields.get("csrf", [""])[0]
        approved = fields.get("approved", [""])[0]
        remember = fields.get("remember", [""])[0] == "yes"
        if not hmac.compare_digest(csrf, self.server.csrf_token) or approved != "yes" or not valid_api_key(api_key):
            fields["api_key"] = [""]
            api_key = ""
            self._send(400, page(
                self.server.csrf_token, message="Enter a new key and confirm the budget.",
                profile=self.server.profile, remember=self.server.remember,
            ))
            return
        with self.server.start_lock:
            if self.server.child is not None:
                self._send(409, b"Already started")
                return
            try:
                if remember:
                    enable_remember(self.server.data_dir, self.server.profile.provider)
                    save_api_key(self.server.data_dir, self.server.profile.provider, api_key)
                else:
                    forget_api_key(self.server.data_dir, self.server.profile.provider)
                child = start_live_child(api_key, self.server.profile, self.server.app_port)
            except (LocalCredentialError, OSError):
                fields["api_key"] = [""]
                api_key = ""
                self._send(500, page(
                    self.server.csrf_token, message="Windows encrypted key storage failed; the service was not started.",
                    profile=self.server.profile, remember=remember,
                ))
                return
            fields["api_key"] = [""]
            api_key = ""
            self.server.remember = remember
            self.server.child = child
        target = f"http://127.0.0.1:{self.server.app_port}/"
        success = (
            "<!doctype html><html lang=zh-CN><meta charset=utf-8>"
            f"<title>CIRP {self.server.profile.display_name} is starting</title><h1>CIRP {self.server.profile.display_name} is starting</h1>"
            f"<p>The key was passed to this process {'and encrypted for the current user with Windows DPAPI' if remember else 'without being saved'}. Open <a href=\"{target}\">{target}</a> shortly.</p>"
        ).encode("utf-8")
        self._send(202, success)
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def port_number(value: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def main(argv: list[str] | None = None, *, forced_provider: str | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-use loopback live API key entry for CIRP")
    parser.add_argument("--setup-port", type=port_number, default=8011)
    parser.add_argument("--app-port", type=port_number, default=8010)
    parser.add_argument("--replace-key", action="store_true", help="Ignore the saved key and show the key-entry page")
    parser.add_argument("--forget-key", action="store_true", help="Delete the current provider's encrypted saved key and exit")
    if forced_provider is None:
        parser.add_argument("--provider", choices=tuple(PROFILES), default="gemini")
    args = parser.parse_args(argv)
    if args.setup_port == args.app_port:
        parser.error("setup and app ports must differ")
    profile = PROFILES[forced_provider or args.provider]
    data_dir = Path(os.getenv("CIRP_DATA_DIR", str(ROOT / ".local"))).resolve()
    if args.forget_key:
        try:
            forget_api_key(data_dir, profile.provider)
        except LocalCredentialError as exc:
            print("[STOP] " + str(exc), flush=True)
            return 2
        print(f"[DONE] Removed the saved {profile.display_name} credential for this Windows user.", flush=True)
        return 0
    initial_message = ""
    if remember_enabled(data_dir, profile.provider) and not args.replace_key:
        try:
            api_key = load_api_key(data_dir, profile.provider)
        except LocalCredentialError:
            api_key = None
            initial_message = "The saved key cannot be decrypted by the current Windows user. Enter it again or delete it with --forget-key."
        if api_key and valid_api_key(api_key):
            print(f"[READY] Starting {profile.display_name} with the Windows-current-user encrypted credential.", flush=True)
            child = start_live_child(api_key, profile, args.app_port)
            api_key = ""
            return child.wait()
        if api_key is not None:
            api_key = ""
            initial_message = "The saved key format is invalid. Enter it again."
    server = SetupServer(
        ("127.0.0.1", args.setup_port), args.app_port, profile,
        data_dir=data_dir, initial_message=initial_message,
    )
    print(f"[READY] Open http://127.0.0.1:{args.setup_port}/ to enter a new {profile.display_name} key locally.", flush=True)
    try:
        server.serve_forever()
        if server.child is None:
            return 2
        return server.child.wait()
    except KeyboardInterrupt:
        if server.child is not None and server.child.poll() is None:
            server.child.terminate()
        return 130
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
