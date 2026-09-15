"""Standard-library launcher for isolated setup, dependency checks, and local or protected preview startup."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.security import environment_without_secrets  # noqa: E402

PROBE = 'import app.main, uvicorn; print("dependencies-ok")'


def child_env_without_secrets() -> dict[str, str]:
    return environment_without_secrets()


def application_child_env(*, live: bool, remote: bool) -> dict[str, str]:
    """Build runtime env; a local live child receives only the selected CIRP key."""
    if remote:
        # The dedicated preview launcher generates its own short-lived preview
        # credentials.  Do not make a direct --remote invocation a path for
        # unrelated shell credentials to reach that launcher.
        return environment_without_secrets()
    api_key = os.environ.get('CIRP_API_KEY', '') if live else ''
    env = environment_without_secrets()
    if live and api_key:
        env['CIRP_API_KEY'] = api_key
    return env


def venv_python(root: Path = ROOT) -> Path:
    return root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def requirements_digest(root: Path = ROOT) -> str:
    return hashlib.sha256((root / 'requirements-app.txt').read_bytes()).hexdigest()


def dependency_probe(python: Path, root: Path = ROOT) -> bool:
    try:
        result = subprocess.run([str(python), '-c', PROBE], cwd=root, env=child_env_without_secrets(),
                                capture_output=True, timeout=60, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_environment(*, use_current: bool = False, root: Path = ROOT) -> Path:
    if use_current:
        python = Path(sys.executable)
        if not dependency_probe(python, root):
            raise RuntimeError('The current interpreter lacks compatible dependencies. Remove --use-current-env to use the project virtual environment.')
        return python
    python = venv_python(root)
    if not python.exists():
        print('[SETUP] Creating .venv in the project directory; global Python is unchanged.', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(root / '.venv')], cwd=root,
                       env=child_env_without_secrets(), check=True)
    marker = root / '.venv' / 'cirp-dependencies.json'
    expected = {'requirements_sha256': requirements_digest(root)}
    try:
        installed = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        installed = {}
    if installed == expected and dependency_probe(python, root):
        print('[CHECK] Dependencies are ready; network installation is skipped.', flush=True)
        return python
    log_dir = root / '.local' / 'launcher'
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / 'install.log'
    print('[SETUP] Installing application dependencies for first use or a dependency change; package-index access is required.', flush=True)
    with log.open('wb') as stream:
        result = subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                                 '-r', 'requirements-app.txt'], cwd=root,
                                env=child_env_without_secrets(), stdout=stream,
                                stderr=subprocess.STDOUT, check=False)
    if result.returncode or not dependency_probe(python, root):
        raise RuntimeError('Dependency installation or validation failed; the service was not started. Review the local log: ' + str(log))
    marker.write_text(json.dumps(expected), encoding='utf-8')
    return python


def make_command(python: Path, args: argparse.Namespace) -> list[str]:
    if args.remote and args.live:
        raise ValueError('Remote preview remains mock-only. Live API mode starts locally with --live.')
    if args.remote and args.data_dir:
        raise ValueError('Remote preview must use isolated .local/preview-data, not the production data directory.')
    port = args.port if args.port is not None else (8001 if args.remote else 8000)
    if not 1024 <= port <= 65535:
        raise ValueError('Port must be between 1024 and 65535.')
    target = ['scripts/remote_preview.py'] if args.remote else ['-m', 'app.local_entry']
    command = [str(python), *target, '--port', str(port)]
    if not args.remote:
        if args.no_browser:
            command.append('--no-browser')
        if args.live:
            command.append('--live')
        if args.data_dir:
            command.extend(['--data-dir', str(Path(args.data_dir).resolve())])
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='One-command local CIRP deployment. API keys are not read and models are not called by default.')
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-dir')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--remote', action='store_true', help='Use an existing Cloudflare Quick Tunnel for mock preview; cloudflared is required')
    parser.add_argument('--use-current-env', action='store_true', help='Development and test only; do not install dependencies')
    parser.add_argument('--install-only', action='store_true')
    parser.add_argument('--check', action='store_true', help='Check only; do not install, start, or access a model')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        print('[STOP] Python 3.11 or later is required.', flush=True)
        return 2
    try:
        # Validate arguments before installation or other side effects.
        make_command(Path(sys.executable), args)
        if args.remote:
            sys.path.insert(0, str(ROOT))
            from scripts.remote_preview import find_cloudflared
            if not find_cloudflared():
                raise RuntimeError('cloudflared is missing. On Windows: winget install --id Cloudflare.cloudflared -e')
        if args.check:
            python = Path(sys.executable) if args.use_current_env else venv_python()
            ready = python.exists() and dependency_probe(python)
            print('[CHECK] Python >= 3.11; application dependencies: ' + ('ready' if ready else 'first-time installation required'), flush=True)
            print('No .env was read; no software was installed, no service was started, and no model was called.', flush=True)
            return 0 if ready else 2
        python = ensure_environment(use_current=args.use_current_env)
        if args.install_only:
            print('[READY] Application dependencies are ready; the service has not been started.', flush=True)
            return 0
        env = application_child_env(live=args.live, remote=args.remote)
        env['PYTHONUTF8'] = '1'
        env['PYTHONUNBUFFERED'] = '1'
        process = subprocess.Popen(make_command(python, args), cwd=ROOT, env=env)
        try:
            return process.wait()
        except KeyboardInterrupt:
            # The terminal forwards Ctrl+C to the child; allow it to close database work first.
            try:
                process.wait(timeout=145)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            return 130
    except KeyboardInterrupt:
        # The child shares the foreground process group and receives Ctrl+C; do not kill an untrusted PID.
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('[STOP] ' + str(exc), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
