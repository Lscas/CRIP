"""受保护的单用户远程预览；不充当生产身份系统。"""
from __future__ import annotations
import base64
import binascii
import hashlib
import hmac
import threading
import time
from collections import deque
from starlette.responses import JSONResponse


def secret_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(hashlib.sha256(left.encode()).digest(), hashlib.sha256(right.encode()).digest())


class PreviewAccess:
    """每个进程的失败限流；正确高熵密码不受失败队列阻断。"""
    def __init__(self, settings):
        self.settings = settings
        self.failures: deque[float] = deque()
        self.lock = threading.Lock()

    def authorized(self, request) -> bool:
        s = self.settings
        origin_key = request.headers.get('x-cirp-origin-token', '')
        if request.url.path.startswith('/api/') and s.origin_token and secret_equal(origin_key, s.origin_token):
            return True
        header = request.headers.get('authorization', '')
        if len(header) > 2048:
            return False
        scheme, _, encoded = header.partition(' ')
        if scheme.lower() != 'basic':
            return False
        try:
            decoded = base64.b64decode(encoded, validate=True).decode('utf-8')
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return False
        return secret_equal(decoded, s.preview_user + ':' + s.preview_password)

    def reject(self):
        current = time.monotonic()
        with self.lock:
            while self.failures and current - self.failures[0] > 60:
                self.failures.popleft()
            limited = len(self.failures) >= 30
            if not limited:
                self.failures.append(current)
        headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                   'X-Robots-Tag': 'noindex, nofollow', 'Referrer-Policy': 'no-referrer'}
        if limited:
            headers['Retry-After'] = '60'
            return JSONResponse({'detail': '验证请求过多，请稍后重试。'}, 429, headers=headers)
        headers['WWW-Authenticate'] = 'Basic realm="CIRP remote preview", charset="UTF-8"'
        return JSONResponse({'detail': '远程测试需要访问账号和临时密码。'}, 401, headers=headers)
