"""标准库启动器：隔离安装、依赖探测、启动本机或已有受保护的远程测试入口。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROBE = 'import app.main, uvicorn; print("dependencies-ok")'


def venv_python(root: Path = ROOT) -> Path:
    return root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def requirements_digest(root: Path = ROOT) -> str:
    return hashlib.sha256((root / 'requirements-app.txt').read_bytes()).hexdigest()


def dependency_probe(python: Path, root: Path = ROOT) -> bool:
    try:
        result = subprocess.run([str(python), '-c', PROBE], cwd=root,
                                capture_output=True, timeout=60, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_environment(*, use_current: bool = False, root: Path = ROOT) -> Path:
    if use_current:
        python = Path(sys.executable)
        if not dependency_probe(python, root):
            raise RuntimeError('当前解释器缺少兼容依赖；请取消 --use-current-env 使用项目虚拟环境。')
        return python
    python = venv_python(root)
    if not python.exists():
        print('[SETUP] 正在项目目录创建 .venv；不修改全局 Python。', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(root / '.venv')], cwd=root, check=True)
    marker = root / '.venv' / 'cirp-dependencies.json'
    expected = {'requirements_sha256': requirements_digest(root)}
    try:
        installed = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        installed = {}
    if installed == expected and dependency_probe(python, root):
        print('[CHECK] 依赖已就绪，跳过网络安装。', flush=True)
        return python
    log_dir = root / '.local' / 'launcher'
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / 'install.log'
    print('[SETUP] 首次或依赖变更时安装应用依赖；需要访问软件包源。', flush=True)
    with log.open('wb') as stream:
        result = subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                                 '-r', 'requirements-app.txt'], cwd=root,
                                stdout=stream, stderr=subprocess.STDOUT, check=False)
    if result.returncode or not dependency_probe(python, root):
        raise RuntimeError('依赖安装/验证失败；未启动服务。检查本机日志：' + str(log))
    marker.write_text(json.dumps(expected), encoding='utf-8')
    return python


def make_command(python: Path, args: argparse.Namespace) -> list[str]:
    if args.remote and args.live:
        raise ValueError('远程测试仍只允许模拟模式；真实 API 只从本机 --live 启动。')
    if args.remote and args.data_dir:
        raise ValueError('远程测试使用独立 .local/preview-data，不能改为正式资料目录。')
    port = args.port if args.port is not None else (8001 if args.remote else 8000)
    if not 1024 <= port <= 65535:
        raise ValueError('端口必须在 1024 至 65535 之间。')
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
    parser = argparse.ArgumentParser(description='CIRP 一键本机部署。默认不读取密钥、不调用模型。')
    parser.add_argument('--port', type=int)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-dir')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--remote', action='store_true', help='使用已有 Cloudflare Quick Tunnel 模拟预览；需安装 cloudflared')
    parser.add_argument('--use-current-env', action='store_true', help='开发/测试专用，不安装依赖')
    parser.add_argument('--install-only', action='store_true')
    parser.add_argument('--check', action='store_true', help='只检查，不安装、不启动、不访问模型')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        print('[STOP] 需要 Python 3.11 或更高版本。', flush=True)
        return 2
    try:
        # 在安装或其他副作用前校验参数。
        make_command(Path(sys.executable), args)
        if args.remote:
            sys.path.insert(0, str(ROOT))
            from scripts.remote_preview import find_cloudflared
            if not find_cloudflared():
                raise RuntimeError('缺少 cloudflared。Windows：winget install --id Cloudflare.cloudflared -e')
        if args.check:
            python = Path(sys.executable) if args.use_current_env else venv_python()
            ready = python.exists() and dependency_probe(python)
            print('[CHECK] Python >= 3.11；应用依赖：' + ('可用' if ready else '待首次安装'), flush=True)
            print('没有读取 .env、安装软件、启动服务或调用模型。', flush=True)
            return 0 if ready else 2
        python = ensure_environment(use_current=args.use_current_env)
        if args.install_only:
            print('[READY] 应用依赖已就绪，尚未启动服务。', flush=True)
            return 0
        env = os.environ.copy()
        env['PYTHONUTF8'] = '1'
        env['PYTHONUNBUFFERED'] = '1'
        process = subprocess.Popen(make_command(python, args), cwd=ROOT, env=env)
        try:
            return process.wait()
        except KeyboardInterrupt:
            # 同一终端会将 Ctrl+C 同时传给子进程，先允许其关闭数据库/任务。
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
        # 子进程处于同一前台进程组，收到终端 Ctrl+C；不按不可信PID杀进程。
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('[STOP] ' + str(exc), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
