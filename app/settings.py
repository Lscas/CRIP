"""环境配置；密钥不进入 API 响应、仓库或模型提示。"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text(encoding="utf-8").strip()

def flag(name: str, default: str = 'false') -> bool:
    return os.getenv(name, default).strip().lower() in {'1', 'true', 'yes'}

@dataclass(frozen=True)
class Settings:
    data_dir: Path
    provider: str = 'mock'
    api_base_url: str = 'https://api.deepseek.com'
    api_key: str = field(default='', repr=False)
    cheap_model: str = 'deepseek-v4-flash'
    live_enabled: bool = False
    prices_confirmed: bool = False
    input_rate: Decimal = Decimal('0')
    output_rate: Decimal = Decimal('0')
    input_limit: int = 6000
    output_limit: int = 2000
    project_bytes: int = 10_000_000_000
    chunk_bytes: int = 4 * 1024 * 1024
    parser_timeout: int = 120
    deadline_seconds: int = 86400
    start_worker: bool = True

    remote_enabled: bool = False
    preview_user: str = 'engineer'
    preview_password: str = field(default='', repr=False)
    origin_token: str = field(default='', repr=False)
    allowed_hosts: tuple[str, ...] = ('127.0.0.1', 'localhost', 'testserver', '[::1]')

    def __post_init__(self):
        if self.remote_enabled:
            if len(self.preview_password) < 24 or not self.preview_user or ':' in self.preview_user:
                raise ValueError('远程预览必须配置独立账号和至少24字符的随机密码。')
            if self.origin_token and len(self.origin_token) < 24:
                raise ValueError('源站密钥必须至少24字符。')
            if not self.allowed_hosts or any(not re.fullmatch(r'[A-Za-z0-9.\[\]:-]+', host) for host in self.allowed_hosts):
                raise ValueError('远程预览必须使用明确Host名单，不能使用通配符。')
            if self.live_enabled or self.provider != 'mock':
                raise ValueError('此远程预览版本只允许mock；真实API开通另行确认和验证。')

    @classmethod
    def from_env(cls) -> 'Settings':
        load_dotenv(ROOT / '.env', override=False)
        return cls(
            data_dir=Path(os.getenv('CIRP_DATA_DIR', str(ROOT / '.local'))).resolve(),
            provider=os.getenv('CIRP_PROVIDER', 'mock'),
            api_base_url=os.getenv('CIRP_API_BASE_URL', 'https://api.deepseek.com').rstrip('/'),
            api_key=os.getenv('CIRP_API_KEY', ''),
            cheap_model=os.getenv('CIRP_CHEAP_MODEL', 'deepseek-v4-flash'),
            live_enabled=flag('CIRP_LIVE_API_ENABLED'),
            prices_confirmed=flag('CIRP_PRICES_CONFIRMED'),
            input_rate=Decimal(os.getenv('CIRP_INPUT_CNY_PER_MILLION', '0')),
            output_rate=Decimal(os.getenv('CIRP_OUTPUT_CNY_PER_MILLION', '0')),
            remote_enabled=flag('CIRP_REMOTE_ENABLED'),
            preview_user=os.getenv('CIRP_PREVIEW_USER', 'engineer'),
            preview_password=os.getenv('CIRP_PREVIEW_PASSWORD', ''),
            origin_token=os.getenv('CIRP_ORIGIN_TOKEN', ''),
            allowed_hosts=tuple(x.strip() for x in os.getenv('CIRP_ALLOWED_HOSTS', '127.0.0.1,localhost,testserver,[::1]').split(',') if x.strip()),
        )

    def live_errors(self) -> list[str]:
        errors = []
        if self.provider != 'deepseek': errors.append('CIRP_PROVIDER 未设为 deepseek')
        if not self.live_enabled: errors.append('付费 API 开关未开启')
        if not self.api_key: errors.append('未配置 API 密钥')
        if not self.prices_confirmed: errors.append('未确认实际 API 单价')
        for name, rate in [('输入', self.input_rate), ('输出', self.output_rate)]:
            if not rate.is_finite() or rate <= 0: errors.append(f'{name}单价须为有限正数')
        u = urlsplit(self.api_base_url)
        if u.scheme != 'https' or not u.hostname or u.query or u.fragment or u.username:
            errors.append('API 地址须为无凭证和查询参数的 HTTPS 基址')
        if not 1 <= self.output_limit <= 2000: errors.append('简单任务输出上限须为1至2000')
        return errors

    def public(self) -> dict:
        return {
            'version': VERSION, 'provider': self.provider,
            'mode': '模拟模式：仅演示样例' if self.provider == 'mock' else '真实API模式',
            'model': self.cheap_model if self.provider != 'mock' else 'mock-no-network',
            'live_ready': not self.live_errors(), 'live_blockers': self.live_errors(),
            'budget_cny': '300.00', 'deadline_hours': 24, 'max_active_projects': 1,
            'upload_capacity_bytes': self.project_bytes, 'upload_chunk_bytes': self.chunk_bytes,
            'local_single_user': True, 'remote_preview': self.remote_enabled, 'thinking': 'disabled',
            'capabilities': {'txt': '文本与行号', 'pdf': '文字层与坐标；图形未审',
                             'docx': '正文与表格；图片与修订未审', 'images': '已接收，视觉未接入',
                             'dwg': '已接收，CAD转换未接入', 'geometric_takeoff': False},
        }
