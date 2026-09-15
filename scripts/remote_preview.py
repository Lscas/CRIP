"""在用户电脑前台运行Cloudflare远程预览；无凭据也可用Quick Tunnel。
--pages另需用户本机Wrangler登录。不会把Python后端迁移进Pages。
"""
from __future__ import annotations
import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.security import environment_without_secrets
from scripts.build_cloudflare import build


def cloudflare_credentials(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return the narrow Cloudflare auth set needed by publishing children.

    This deliberately does not return an arbitrary parent environment.  The
    API-token form is preferred, while the legacy global-key form needs its
    matching email.  Account ID is retained because Wrangler/API calls may use
    it to select the intended account.
    """
    parent = os.environ if source is None else source
    names = ('CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_API_KEY', 'CLOUDFLARE_EMAIL',
             'CLOUDFLARE_ACCOUNT_ID')
    return {name: parent[name] for name in names if parent.get(name)}


def publishing_environment(credentials: dict[str, str] | None = None,
                           source: dict[str, str] | None = None) -> dict[str, str]:
    """Build a sanitized child environment and explicitly restore CF auth only."""
    env = environment_without_secrets(source)
    env.update(cloudflare_credentials(credentials or {}))
    return env


def remove_secrets_from_self(source: dict[str, str] | None = None,
                             target: dict[str, str] | None = None) -> dict[str, str]:
    """Strip inherited secrets from this preview process after saving scoped auth.

    A direct invocation need not go through local_deploy, so the entry point
    also removes secrets from its own environment.  Cloudflare credentials are
    returned to the caller as an in-memory mapping and are passed only to the
    corresponding publishing child/API request.
    """
    parent = dict(os.environ if source is None else source)
    credentials = cloudflare_credentials(parent)
    target = os.environ if target is None else target
    target.clear()
    target.update(environment_without_secrets(parent))
    return credentials


def tunnel_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Quick Tunnel does not require the application's or shell's secrets."""
    return environment_without_secrets(source)


def find_cloudflared() -> str | None:
    configured = os.getenv('CIRP_CLOUDFLARED')
    if configured:
        return str(Path(configured).resolve()) if Path(configured).is_file() else None
    found = shutil.which('cloudflared')
    if found:
        return found
    if os.name == 'nt':
        for variable in ['ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA']:
            root = Path(os.getenv(variable, 'C:/missing'))
            for suffix in ['cloudflared/cloudflared.exe', 'Microsoft/WinGet/Links/cloudflared.exe']:
                path = root / suffix
                if path.is_file():
                    return str(path)
    return None


def tunnel_url(text: str) -> str | None:
    match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com(?=[\s/|]|$)', text)
    return match.group(0) if match else None


def child_environment(hostname: str, password: str, origin_token: str,
                      source: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for the mock origin; only generated preview values are added."""
    env = environment_without_secrets(source)
    env.update({
        'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8',
        'CIRP_REMOTE_ENABLED': 'true', 'CIRP_PREVIEW_USER': 'engineer',
        'CIRP_PREVIEW_PASSWORD': password, 'CIRP_ORIGIN_TOKEN': origin_token,
        'CIRP_ALLOWED_HOSTS': 'localhost,127.0.0.1,' + hostname,
        'CIRP_PROVIDER': 'mock', 'CIRP_LIVE_API_ENABLED': 'false',
        'CIRP_API_KEY': '', 'CIRP_PRICES_CONFIRMED': 'false',
        'CIRP_DATA_DIR': str(ROOT / '.local/preview-data'),
    })
    return env


def verify_endpoint(base: str, password: str, timeout: float = 12) -> dict:
    import httpx
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        locked = client.get(base + '/api/settings')
        if locked.status_code != 401:
            raise RuntimeError(f'未登录保护检查未通过（HTTP {locked.status_code}）。不宣布部署成功。')
        authenticated = client.get(base + '/api/settings', auth=('engineer', password))
        authenticated.raise_for_status()
        body = authenticated.json()
        if body.get('provider') != 'mock' or not body.get('remote_preview'):
            raise RuntimeError('源站不是受保护的mock预览，停止发布。')
        return {'unauthenticated_status': 401, 'authenticated_status': authenticated.status_code,
                'provider': body['provider'], 'remote_preview': True}


def wrangler(npx: str, args: list[str], log: Path, secret_input: dict | None = None,
             credentials: dict[str, str] | None = None) -> str:
    # 凭据只经stdin传递，不出现在命令行或版本库。
    result = subprocess.run([npx, '--yes', 'wrangler@4', *args], cwd=ROOT,
        input=json.dumps(secret_input) if secret_input is not None else None,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300,
        env=publishing_environment(credentials))
    text = result.stdout + result.stderr
    if secret_input:
        for value in secret_input.values():
            text = text.replace(value, '[REDACTED]')
    with log.open('a', encoding='utf-8') as f:
        f.write('\n$ wrangler ' + ' '.join(args) + '\n' + text)
    if result.returncode:
        raise RuntimeError('Wrangler命令失败；请检查本机登录及部署日志：' + str(log))
    return text


def configure_fail_closed(project: str, credentials: dict[str, str] | None = None):
    """OAuth CLI用户在控制台确认；环境Token用户只修改本次新建项目。"""
    import httpx
    credentials = cloudflare_credentials() if credentials is None else cloudflare_credentials(credentials)
    token = credentials.get('CLOUDFLARE_API_TOKEN', '')
    account = credentials.get('CLOUDFLARE_ACCOUNT_ID', '')
    if token and account:
        if not re.fullmatch(r'[a-fA-F0-9]{32}', account):
            raise RuntimeError('CLOUDFLARE_ACCOUNT_ID格式无效。')
        url = f'https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{project}'
        with httpx.Client(timeout=25, follow_redirects=False, headers={'Authorization': 'Bearer ' + token}) as client:
            reply = client.patch(url, json={'deployment_configs': {
                'production': {'fail_open': False}, 'preview': {'fail_open': False}}})
            reply.raise_for_status()
            if not reply.json().get('success'):
                raise RuntimeError('Cloudflare未接受Fail closed设置。')
            check = client.get(url); check.raise_for_status()
            settings = check.json().get('result', {}).get('deployment_configs', {})
            if any(settings.get(k, {}).get('fail_open') is not False for k in ['production','preview']):
                raise RuntimeError('未确认Fail closed，不继续发布。')
    else:
        print('Pages安全设置：在Cloudflare项目 ' + project + ' 的 Settings > Runtime > Fail open / closed 选择 Fail closed。')
        print('该设置避免免费Functions配额耗尽后绕过网页验证；尚未配置真实项目数据。')
        if not sys.stdin.isatty() or input('完成控制台设置后输入 CLOSED 继续，其他输入停止：').strip() != 'CLOSED':
            raise RuntimeError('未确认Fail closed，停止部署；也可配置账户级Pages编辑Token自动设置。')


def wait_verify(base: str, password: str) -> dict:
    end = time.monotonic() + 60
    last = None
    while time.monotonic() < end:
        try:
            return verify_endpoint(base, password, 8)
        except Exception as exc:
            last = type(exc).__name__
            time.sleep(2)
    raise RuntimeError('公网HTTP验证未通过（' + str(last) + '）；未确认上线。')


def publish_pages(npx: str, run_dir: Path, origin: str, password: str, origin_token: str,
                  credentials: dict[str, str] | None = None) -> str:
    project = 'cirp-preview-' + secrets.token_hex(4)
    assets = build(run_dir / 'pages-assets')
    log = run_dir / 'wrangler.log'
    wrangler(npx, ['whoami'], log, credentials=credentials)
    wrangler(npx, ['pages', 'project', 'create', project, '--production-branch', 'main'], log,
             credentials=credentials)
    (run_dir / 'pages-project.json').write_text(json.dumps({'project': project}), encoding='utf-8')
    deploy_args = ['pages', 'deploy', str(assets), '--project-name', project, '--branch', 'main', '--commit-dirty=true']
    # 首次是锁定部署；缺少secrets时Worker返回503，不公开数据。
    wrangler(npx, deploy_args, log, credentials=credentials)
    configure_fail_closed(project, credentials)
    wrangler(npx, ['pages', 'secret', 'bulk', '--project-name', project], log,
             {'PREVIEW_USER': 'engineer', 'PREVIEW_PASSWORD': password,
              'ORIGIN_TOKEN': origin_token, 'ORIGIN_URL': origin}, credentials)
    output = wrangler(npx, deploy_args, log, credentials=credentials)
    urls = re.findall(r'https://[a-z0-9.-]+\.pages\.dev(?=[\s/|]|$)', output)
    if not urls:
        raise RuntimeError('没有从Wrangler响应中读到实际部署URL；不拼接或猜测网址。查看部署日志。')
    return urls[-1]


def stop(process: subprocess.Popen | None):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=5)


def main() -> int:
    # Capture the tiny Cloudflare publishing set before removing every secret
    # inherited from the user's shell.  The mapping never becomes this
    # process's environment and is handed only to Wrangler/the CF API path.
    credentials = remove_secrets_from_self()
    p = argparse.ArgumentParser(description='受保护的Cloudflare远程测试。Ctrl+C停止本次源站和隧道。')
    p.add_argument('--pages', action='store_true', help='同时创建新的独立Pages项目；需本机Wrangler已登录')
    p.add_argument('--port', type=int, default=8000)
    p.add_argument('--check', action='store_true', help='仅检查工具是否存在，不启动服务、不创建云资源')
    args = p.parse_args()
    cloudflared = find_cloudflared()
    npx = shutil.which('npx.cmd' if os.name == 'nt' else 'npx')
    if not cloudflared:
        print('缺少cloudflared。Windows先运行：winget install --id Cloudflare.cloudflared -e')
        print('安装后重开终端，或设置CIRP_CLOUDFLARED为官方cloudflared.exe完整路径。')
        return 2
    if args.pages and not npx:
        print('Pages模式需要Node.js/npm以及本机Wrangler登录；Tunnel模式不需要。')
        return 2
    if args.check:
        print('本机工具已找到。尚未验证Cloudflare账户、网络或公网网址。')
        return 0
    if not 1024 <= args.port <= 65535:
        p.error('端口应在1024至65535之间')
    with socket.socket() as sock:
        try: sock.bind(('127.0.0.1', args.port))
        except OSError:
            print('端口已占用；请停止本机旧服务或指定另一个--port。'); return 2
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3)
    run_dir = ROOT / '.local/remote-preview' / stamp
    run_dir.mkdir(parents=True)
    password = secrets.token_urlsafe(24); origin_token = secrets.token_urlsafe(32)
    tunnel = server = None
    tunnel_log = (run_dir / 'tunnel.log').open('w', encoding='utf-8')
    server_log = (run_dir / 'server.log').open('w', encoding='utf-8')
    try:
        tunnel = subprocess.Popen([cloudflared, 'tunnel', '--no-autoupdate', '--protocol', 'http2',
            '--url', f'http://127.0.0.1:{args.port}'], stdout=tunnel_log, stderr=subprocess.STDOUT,
            env=tunnel_environment())
        end = time.monotonic() + 65; origin = None
        while time.monotonic() < end:
            origin = tunnel_url((run_dir / 'tunnel.log').read_text(encoding='utf-8', errors='replace'))
            if origin: break
            if tunnel.poll() is not None: break
            time.sleep(.4)
        if not origin:
            raise RuntimeError('未取得Quick Tunnel地址。请检查网络及tunnel.log；没有完成公网部署。')
        env = child_environment(urlsplit(origin).hostname, password, origin_token)
        server = subprocess.Popen([sys.executable, '-m', 'app', '--port', str(args.port)], cwd=ROOT,
                                  env=env, stdout=server_log, stderr=subprocess.STDOUT)
        end = time.monotonic() + 25
        while True:
            if server.poll() is not None: raise RuntimeError('Python源站启动失败，查看server.log。')
            try:
                verify_endpoint(f'http://127.0.0.1:{args.port}', password, 2)
                break
            except Exception:
                if time.monotonic() >= end: raise RuntimeError('本地鉴权验收失败，未发布。')
                time.sleep(.5)
        wait_verify(origin, password)
        address = publish_pages(npx, run_dir, origin, password, origin_token, credentials) if args.pages else origin
        checks = wait_verify(address, password)
        (run_dir / 'verified-deployment.json').write_text(json.dumps({
            'url': address, 'origin_url': origin, 'type': 'pages-with-tunnel' if args.pages else 'quick-tunnel',
            'checks': checks, 'notice': '源站依赖此电脑和终端；不代表24小时托管或施工精度验证。'}, ensure_ascii=False, indent=2), encoding='utf-8')
        print('\n受保护的远程入口已通过HTTP检查：' + address)
        print('账号：engineer\n临时密码：' + password)
        print('请将密码私下保存。不要把密码或API Key提交到Git或放进网址。')
        print('默认mock，真实资料只做已有基础解析；完整图纸分析尚未实现。')
        print('本机和此终端必须持续运行。Ctrl+C关闭本次源站和Tunnel。日志：' + str(run_dir))
        while True:
            if server.poll() is not None or tunnel.poll() is not None:
                raise RuntimeError('源站或Tunnel已退出，远程入口不再保证可用。')
            time.sleep(1)
    except KeyboardInterrupt:
        print('\n正在关闭本次测试入口。')
        return 0
    except Exception as exc:
        print('部署未完成：' + str(exc))
        print('日志位置：' + str(run_dir))
        return 1
    finally:
        stop(server); stop(tunnel); server_log.close(); tunnel_log.close()

if __name__ == '__main__':
    raise SystemExit(main())
