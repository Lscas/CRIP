"""Local-only entry point: mock by default; --live loads user configuration."""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import socket
import threading
import time
import webbrowser

import uvicorn
from app.main import create_app
from app.local_credentials import LocalCredentialError, remember_enabled, save_api_key
from app.settings import ROOT, VERSION, Settings

LOCAL_HOSTS = ('127.0.0.1', 'localhost', '[::1]')


def port_number(value: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError('Port must be between 1024 and 65535.')
    return port


def check_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError as exc:
            raise ValueError(f'Port {port} is already in use. No existing process was stopped; use --port 8002.') from exc


def local_settings(data_dir: Path | None = None, *, live: bool = False) -> Settings:
    if not live:
        # Do not inherit paid-mode switches or API keys from the outer process.
        return Settings(data_dir=(data_dir or ROOT / '.local').resolve(), allowed_hosts=LOCAL_HOSTS)
    settings = Settings.from_env()
    errors = settings.live_errors()
    if errors:
        raise ValueError('Live API is not configured: ' + '; '.join(errors))
    if settings.remote_enabled or settings.render_free_preview:
        raise ValueError('Local live API mode cannot be combined with remote preview mode.')
    resolved_data_dir = (data_dir or settings.data_dir).resolve()
    if remember_enabled(resolved_data_dir, settings.provider):
        try:
            save_api_key(resolved_data_dir, settings.provider, settings.api_key)
        except (LocalCredentialError, OSError) as exc:
            raise ValueError('The API key could not be loaded from Windows current-user encrypted storage; the live service was not started.') from exc
    return replace(settings, data_dir=resolved_data_dir, allowed_hosts=LOCAL_HOSTS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Start the local CIRP web app, background worker, and SQLite store.')
    parser.add_argument('--port', type=port_number, default=8000)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--live', action='store_true', help='Explicitly load live-provider settings; model charges are possible only after Start analysis is clicked')
    args = parser.parse_args(argv)
    try:
        check_port(args.port)
        settings = local_settings(args.data_dir, live=args.live)
    except (ValueError, OSError) as exc:
        print('[STOP] ' + str(exc), flush=True)
        return 2

    server = uvicorn.Server(uvicorn.Config(
        create_app(settings), host='127.0.0.1', port=args.port,
        workers=1, proxy_headers=False, access_log=False, log_level='warning',
    ))
    stop_notice = threading.Event()
    url = f'http://127.0.0.1:{args.port}'

    def notify_ready() -> None:
        deadline = time.monotonic() + 45
        while not stop_notice.wait(.1):
            if server.started:
                print(f'[READY] CIRP {VERSION}: {url}', flush=True)
                print('Data directory: ' + str(settings.data_dir), flush=True)
                print('Mode: ' + ('live API (startup itself makes no model request)' if args.live else 'mock; model cost is zero'), flush=True)
                print('Close the window or press Ctrl+C to stop. A normal restart preserves uploads, review history, and the budget ledger.', flush=True)
                if not args.no_browser:
                    try:
                        webbrowser.open(url)
                    except Exception:
                        print('The browser could not be opened automatically. Use the URL shown above.', flush=True)
                return
            if time.monotonic() >= deadline:
                print('[WAIT] The service did not finish starting. Review the terminal error; no public endpoint was created.', flush=True)
                return

    thread = threading.Thread(target=notify_ready, name='cirp-readiness', daemon=True)
    thread.start()
    try:
        server.run()
    finally:
        stop_notice.set()
        thread.join(timeout=2)
    return 0 if server.started else 1


if __name__ == '__main__':
    raise SystemExit(main())
