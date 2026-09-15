"""Environment settings; credentials never enter API responses, the repository, or model prompts."""
from __future__ import annotations
import os
import re
import math
import ipaddress
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text(encoding="utf-8").strip()
LIVE_PROVIDERS = ('deepseek', 'gemini')
DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
DEEPSEEK_MODEL = 'deepseek-v4-flash'
DEEPSEEK_VISION_MODEL = 'deepseek-v4-flash-vision-exp'
GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai'
GEMINI_MODEL = 'gemini-3.6-flash'

def flag(name: str, default: str = 'false') -> bool:
    return os.getenv(name, default).strip().lower() in {'1', 'true', 'yes'}

def custom_provider(provider: str) -> bool:
    return bool(re.fullmatch(r'custom-[0-9a-f]{16}',provider))

def live_provider(provider: str) -> bool:
    return provider in LIVE_PROVIDERS or custom_provider(provider)

@dataclass(frozen=True)
class Settings:
    data_dir: Path
    provider: str = 'mock'
    api_base_url: str = DEEPSEEK_BASE_URL
    api_key: str = field(default='', repr=False)
    cheap_model: str = DEEPSEEK_MODEL
    vision_enabled: bool = False
    vision_model: str = DEEPSEEK_VISION_MODEL
    live_enabled: bool = False
    prices_confirmed: bool = False
    input_rate: Decimal = Decimal('0')
    output_rate: Decimal = Decimal('0')
    # Conservative UTF-8 byte envelope, not a claim about exact provider tokens.
    input_limit: int = 32000
    # Text extraction may use 8k; vision and verification retain lower task caps.
    output_limit: int = 8000
    min_request_interval_seconds: float = 0
    project_bytes: int = 10_000_000_000
    chunk_bytes: int = 4 * 1024 * 1024
    # Large local PDFs use bounded parsing with periodic checkpoints.
    parser_timeout: int = 300
    deadline_seconds: int = 86400
    start_worker: bool = True

    render_free_preview: bool = False
    remote_enabled: bool = False
    preview_user: str = 'engineer'
    preview_password: str = field(default='', repr=False)
    origin_token: str = field(default='', repr=False)
    allowed_hosts: tuple[str, ...] = ('127.0.0.1', 'localhost', 'testserver', '[::1]')

    def __post_init__(self):
        if self.render_free_preview and (not self.remote_enabled or self.origin_token or self.project_bytes > 100 * 1024 * 1024):
            raise ValueError('The free Render preview requires authentication, isolated origin credentials, and the test upload limit.')
        if self.remote_enabled:
            if len(self.preview_password) < 24 or not self.preview_user or ':' in self.preview_user:
                raise ValueError('Remote preview requires a separate account and a random password of at least 24 characters.')
            if self.origin_token and len(self.origin_token) < 24:
                raise ValueError('The origin credential must contain at least 24 characters.')
            if not self.allowed_hosts or any(not re.fullmatch(r'[A-Za-z0-9.\[\]:-]+', host) for host in self.allowed_hosts):
                raise ValueError('Remote preview requires an explicit host allowlist; wildcards are not allowed.')
            if self.live_enabled or self.provider != 'mock':
                raise ValueError('This remote preview is mock-only. Live API access requires separate approval and validation.')

    @classmethod
    def from_env(cls) -> 'Settings':
        load_dotenv(ROOT / '.env', override=False)
        provider = os.getenv('CIRP_PROVIDER', 'mock').strip().lower()
        default_base_url = GEMINI_BASE_URL if provider == 'gemini' else DEEPSEEK_BASE_URL if provider == 'deepseek' else ''
        default_model = GEMINI_MODEL if provider == 'gemini' else DEEPSEEK_MODEL if provider == 'deepseek' else ''
        return cls(
            data_dir=Path(os.getenv('CIRP_DATA_DIR', str(ROOT / '.local'))).resolve(),
            provider=provider,
            api_base_url=os.getenv('CIRP_API_BASE_URL', default_base_url).rstrip('/'),
            api_key=os.getenv('CIRP_API_KEY', ''),
            cheap_model=os.getenv('CIRP_CHEAP_MODEL', default_model),
            vision_enabled=flag('CIRP_VISION_ENABLED'),
            vision_model=os.getenv('CIRP_VISION_MODEL', DEEPSEEK_VISION_MODEL),
            live_enabled=flag('CIRP_LIVE_API_ENABLED'),
            prices_confirmed=flag('CIRP_PRICES_CONFIRMED'),
            input_rate=Decimal(os.getenv('CIRP_INPUT_CNY_PER_MILLION', '0')),
            output_rate=Decimal(os.getenv('CIRP_OUTPUT_CNY_PER_MILLION', '0')),
            input_limit=int(os.getenv('CIRP_INPUT_LIMIT_BYTES','32000')),
            output_limit=int(os.getenv('CIRP_OUTPUT_LIMIT_TOKENS','8000')),
            min_request_interval_seconds=float(os.getenv(
                'CIRP_MIN_REQUEST_INTERVAL_SECONDS','15' if provider=='gemini' else '0')),
            remote_enabled=flag('CIRP_REMOTE_ENABLED'),
            preview_user=os.getenv('CIRP_PREVIEW_USER', 'engineer'),
            preview_password=os.getenv('CIRP_PREVIEW_PASSWORD', ''),
            origin_token=os.getenv('CIRP_ORIGIN_TOKEN', ''),
            allowed_hosts=tuple(x.strip() for x in os.getenv('CIRP_ALLOWED_HOSTS', '127.0.0.1,localhost,testserver,[::1]').split(',') if x.strip()),
        )

    def is_local_model(self) -> bool:
        if not custom_provider(self.provider): return False
        hostname=urlsplit(self.api_base_url).hostname
        if not hostname: return False
        if hostname.lower()=='localhost': return True
        try:return ipaddress.ip_address(hostname).is_loopback
        except ValueError:return False

    def live_errors(self) -> list[str]:
        errors = []
        if not live_provider(self.provider): errors.append('CIRP_PROVIDER must be deepseek, gemini, or a valid custom profile')
        if not self.live_enabled: errors.append('Live API switch is disabled')
        if not self.api_key and not self.is_local_model(): errors.append('API key is not configured')
        if not self.prices_confirmed: errors.append('Actual API prices have not been confirmed')
        for name, rate in [('Input', self.input_rate), ('Output', self.output_rate)]:
            if not rate.is_finite() or rate < 0 or (not self.is_local_model() and rate == 0):
                errors.append(f'{name} price must be a finite non-negative number and positive for remote APIs')
        u = urlsplit(self.api_base_url)
        if (not u.hostname or u.query or u.fragment or u.username is not None or u.password is not None
                or (u.scheme != 'https' and not (self.is_local_model() and u.scheme == 'http'))):
            errors.append('API base URL must use HTTPS, except HTTP is allowed for a loopback local model; credentials and query parameters are forbidden')
        if not isinstance(self.cheap_model,str) or not re.fullmatch(r'[\x21-\x7e]{1,160}',self.cheap_model):
            errors.append('Model name must be 1-160 printable ASCII characters without spaces')
        if self.provider == 'gemini':
            if self.api_base_url != GEMINI_BASE_URL:
                errors.append('The Gemini key may be sent only to the official Google OpenAI-compatible endpoint')
            if self.cheap_model != GEMINI_MODEL:
                errors.append('This release requires Gemini model gemini-3.6-flash')
            if self.vision_enabled:
                errors.append('This release does not configure a Gemini vision route')
        if self.provider == 'deepseek':
            if self.api_base_url != DEEPSEEK_BASE_URL:
                errors.append('The DeepSeek key may be sent only to the official DeepSeek endpoint')
            if self.cheap_model != DEEPSEEK_MODEL:
                errors.append('This release requires DeepSeek model deepseek-v4-flash')
            if self.vision_enabled and self.vision_model != DEEPSEEK_VISION_MODEL:
                errors.append('This release requires DeepSeek vision model deepseek-v4-flash-vision-exp')
        if type(self.input_limit) is not int or not 1 <= self.input_limit <= 32000:
            errors.append('Text-input byte limit must be between 1 and 32000')
        if type(self.output_limit) is not int or not 1 <= self.output_limit <= 8000:
            errors.append('Text-extraction output limit must be between 1 and 8000')
        if not math.isfinite(self.min_request_interval_seconds) or not 0 <= self.min_request_interval_seconds <= 300:
            errors.append('Minimum model-request interval must be between 0 and 300 seconds')
        return errors

    def inference_parameters(self) -> dict:
        if self.provider == 'gemini':
            return {'reasoning_effort': 'minimal'}
        if self.provider == 'deepseek':
            return {'thinking': {'type': 'disabled'}}
        return {}

    def inference_mode(self) -> str:
        return ('minimal' if self.provider == 'gemini' else
                'provider default' if custom_provider(self.provider) else 'disabled')

    def public(self) -> dict:
        from app.cad import cad_available, dwg_converter
        from app.visual_pipeline import local_ocr_available
        ocr_ready=local_ocr_available();dxf_ready=cad_available();converter=dwg_converter();dwg_ready=dxf_ready and converter is not None
        vision_ready=(self.provider=='deepseek' and self.vision_enabled and not self.live_errors())
        return {
            'version': VERSION, 'provider': self.provider,
            'mode': ('Mock mode: demo examples only' if self.provider == 'mock' else
                     'Local model mode' if self.is_local_model() else 'Live API mode'),
            'model': self.cheap_model if self.provider != 'mock' else 'mock-no-network',
            'live_ready': not self.live_errors(), 'live_blockers': self.live_errors(),
            'budget_cny': '300.00', 'deadline_hours': 24, 'max_active_projects': 1,
            'upload_capacity_bytes': self.project_bytes, 'upload_chunk_bytes': self.chunk_bytes,
            'local_single_user': not self.remote_enabled, 'remote_preview': self.remote_enabled,
            'thinking': self.inference_mode(),
            'min_request_interval_seconds': self.min_request_interval_seconds,
            'request_limits': {
                'text_input_utf8_bytes': min(32000,self.input_limit),
                'text_extraction_output_tokens': min(8000,self.output_limit),
                'vision_input_utf8_bytes': min(6000,self.input_limit),
                'vision_output_tokens': min(2000,self.output_limit),
                'verification_input_utf8_bytes': min(6000,self.input_limit),
                'verification_output_tokens': min(1400,self.output_limit),
            },
            'render_free_preview': self.render_free_preview,
            'storage_warning': ('Free cloud preview: mock analysis only. Data is lost after sleep, restart, or deployment. Limit: 100 MiB per project. Upload test copies and export results promptly; keep production files and operation on the local computer.' if self.render_free_preview else ''),
            'capabilities': {
                'txt': 'Text with line numbers',
                'pdf': 'Text layer, word coordinates, local OCR, page vision, and source-vector audit',
                'docx': 'Body text and tables; embedded-image vision remains separate',
                'images': 'Local OCR and page vision' if vision_ready else 'Local OCR; vision route is disabled',
                'ocr': {'ready': ocr_ready, 'engine': 'RapidOCR/ONNX running locally' if ocr_ready else None},
                'vision': {'ready': vision_ready, 'model': self.vision_model if vision_ready else None,
                           'billing': 'Shares the project-wide cumulative ¥300 model-cost gate'},
                'dxf': {'ready': dxf_ready, 'level': 'OBJECT_METADATA' if dxf_ready else 'UNAVAILABLE'},
                'dwg': {'ready': dwg_ready, 'level': 'OBJECT_METADATA' if dwg_ready else 'UNAVAILABLE',
                        'converter':converter[0] if converter else None,
                        'requirement': None if dwg_ready else 'Requires local GNU LibreDWG or ODA File Converter; cloud CAD pricing and credentials are not separately approved'},
                'geometric_takeoff': {'ready': dxf_ready, 'policy': 'CAD object counts and known-unit geometry may support takeoff; unmapped PDF vectors never become material quantities'},
            },
        }
