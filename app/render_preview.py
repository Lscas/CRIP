"""Render免费测试入口。独立临时数据，不加载.env，不启动付费模型。"""
from __future__ import annotations
import os
import re
from collections.abc import Mapping
from pathlib import Path
from app.settings import Settings

PREVIEW_BYTES = 100 * 1024 * 1024

def preview_settings(env: Mapping[str, str] | None = None, *, data_dir: Path | None = None) -> Settings:
    values = os.environ if env is None else env
    host = values.get('RENDER_EXTERNAL_HOSTNAME', '').strip().lower()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.onrender\.com', host):
        raise ValueError('缺少有效的Render服务域名；不猜测或使用通配Host。')
    # 明确创建Settings，避免继承本机.env、模型Key、生产数据路径或源站密钥。
    return Settings(
        data_dir=data_dir or Path('/tmp/cirp-render-preview'),
        provider='mock', api_key='', live_enabled=False, prices_confirmed=False,
        remote_enabled=True, preview_user='engineer',
        preview_password=values.get('CIRP_PREVIEW_PASSWORD', ''), origin_token='',
        allowed_hosts=(host, '127.0.0.1', 'localhost'),
        project_bytes=PREVIEW_BYTES, parser_timeout=60, render_free_preview=True,
    )

def listen_port(env: Mapping[str, str] | None = None) -> int:
    values = os.environ if env is None else env
    try:
        port = int(values.get('PORT', '10000'))
    except (TypeError, ValueError):
        raise ValueError('PORT必须为有效端口。') from None
    if port < 1024 or port > 65535 or port in {18012,18013,19099}:
        raise ValueError('PORT不是允许的Render端口。')
    return port

def main() -> None:
    import uvicorn
    from app.main import create_app
    settings = preview_settings()
    app = create_app(settings)
    # Render边缘处理HTTPS；应用不依赖客户端可伪造的转发头进行身份判断。
    uvicorn.run(app, host='0.0.0.0', port=listen_port(), workers=1,
                proxy_headers=False, access_log=False)

if __name__ == '__main__':
    main()
