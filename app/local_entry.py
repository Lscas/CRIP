"""仅本机入口：默认模拟模式，不读取 .env；--live 才加载用户配置。"""
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
from app.settings import ROOT, VERSION, Settings

LOCAL_HOSTS = ('127.0.0.1', 'localhost', '[::1]')


def port_number(value: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError('端口必须在 1024 至 65535 之间。')
    return port


def check_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError as exc:
            raise ValueError(f'端口 {port} 已占用；不会关闭已有程序。请使用 --port 8002。') from exc


def local_settings(data_dir: Path | None = None, *, live: bool = False) -> Settings:
    if not live:
        # 不继承外部进程中的付费开关，也不读取 .env 中的任何密钥。
        return Settings(data_dir=(data_dir or ROOT / '.local').resolve(), allowed_hosts=LOCAL_HOSTS)
    settings = Settings.from_env()
    errors = settings.live_errors()
    if errors:
        raise ValueError('真实 API 尚未配置：' + '；'.join(errors))
    if settings.remote_enabled or settings.render_free_preview:
        raise ValueError('本机真实 API 配置不得同时启用远程预览。')
    return replace(settings, data_dir=(data_dir or settings.data_dir).resolve(), allowed_hosts=LOCAL_HOSTS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='CIRP 本机网页、后台任务和 SQLite 一体启动。')
    parser.add_argument('--port', type=port_number, default=8000)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--live', action='store_true', help='显式读取用户的 .env；点击分析后才可能产生模型费用')
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
                print('数据目录：' + str(settings.data_dir), flush=True)
                print('模式：' + ('真实 API（启动本身不发起模型请求）' if args.live else '模拟模式，模型费用为零'), flush=True)
                print('关闭窗口或 Ctrl+C 停止服务；正常重启不删除上传文件、审核记录及预算账本。', flush=True)
                if not args.no_browser:
                    try:
                        webbrowser.open(url)
                    except Exception:
                        print('未能自动打开浏览器，请手动访问上方地址。', flush=True)
                return
            if time.monotonic() >= deadline:
                print('[WAIT] 服务尚未完成启动，请查看终端错误。没有生成公网入口。', flush=True)
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
