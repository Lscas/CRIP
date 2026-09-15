"""Windows-current-user encrypted storage for explicitly remembered API keys."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import secrets


_PREFIX = b"CIRP-DPAPI-CURRENT-USER-V1\n"
_MARKER = "dpapi-current-user-v1\n"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")


class LocalCredentialError(RuntimeError):
    """The encrypted credential store is unavailable or invalid."""


class _DataBlob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(wintypes.BYTE)))


def _provider_name(provider: str) -> str:
    value = provider.strip().lower()
    if not _PROVIDER.fullmatch(value):
        raise ValueError("无效的凭证供应商名称")
    return value


def _directory(data_dir: Path) -> Path:
    return Path(data_dir).resolve() / "credentials"


def credential_path(data_dir: Path, provider: str) -> Path:
    return _directory(data_dir) / f"{_provider_name(provider)}.dpapi"


def remember_path(data_dir: Path, provider: str) -> Path:
    return _directory(data_dir) / f"{_provider_name(provider)}.remember"


def _atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(8))
    try:
        with temporary.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _windows_libraries():
    if os.name != "nt":
        raise LocalCredentialError("加密密钥存储仅支持 Windows 当前用户")
    crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    crypt32.CryptProtectData.argtypes = (
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    )
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    )
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    return crypt32, kernel32


def _input_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value, len(value))
    blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE)))
    return blob, buffer


def _protect(value: bytes) -> bytes:
    crypt32, kernel32 = _windows_libraries()
    source, source_buffer = _input_blob(value)
    protected = _DataBlob()
    try:
        if not crypt32.CryptProtectData(
            ctypes.byref(source), "CIRP provider API key", None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(protected),
        ):
            raise LocalCredentialError("Windows DPAPI 无法加密密钥") from ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        ctypes.memset(source_buffer, 0, len(value))
        if protected.pbData:
            ctypes.memset(protected.pbData, 0, protected.cbData)
            kernel32.LocalFree(ctypes.cast(protected.pbData, wintypes.HLOCAL))


def _unprotect(value: bytes) -> bytes:
    crypt32, kernel32 = _windows_libraries()
    source, source_buffer = _input_blob(value)
    plaintext = _DataBlob()
    description = wintypes.LPWSTR()
    try:
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source), ctypes.byref(description), None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(plaintext),
        ):
            raise LocalCredentialError("保存的密钥不能由当前 Windows 用户解密") from ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(plaintext.pbData, plaintext.cbData)
    finally:
        ctypes.memset(source_buffer, 0, len(value))
        if plaintext.pbData:
            ctypes.memset(plaintext.pbData, 0, plaintext.cbData)
            kernel32.LocalFree(ctypes.cast(plaintext.pbData, wintypes.HLOCAL))
        if description:
            kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))


def remember_enabled(data_dir: Path, provider: str) -> bool:
    marker = remember_path(data_dir, provider)
    try:
        return marker.read_text(encoding="ascii") == _MARKER
    except OSError:
        return False


def enable_remember(data_dir: Path, provider: str) -> Path:
    marker = remember_path(data_dir, provider)
    _atomic_write(marker, _MARKER.encode("ascii"))
    return marker


def save_api_key(data_dir: Path, provider: str, api_key: str) -> Path:
    if not api_key:
        raise ValueError("API 密钥不能为空")
    path = credential_path(data_dir, provider)
    _atomic_write(path, _PREFIX + _protect(api_key.encode("utf-8")))
    return path


def load_api_key(data_dir: Path, provider: str) -> str | None:
    path = credential_path(data_dir, provider)
    try:
        stored = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LocalCredentialError("无法读取保存的加密密钥") from exc
    if not stored.startswith(_PREFIX) or len(stored) == len(_PREFIX):
        raise LocalCredentialError("保存的加密密钥格式无效")
    try:
        return _unprotect(stored[len(_PREFIX):]).decode("utf-8")
    except UnicodeError as exc:
        raise LocalCredentialError("保存的加密密钥内容无效") from exc


def forget_api_key(data_dir: Path, provider: str) -> None:
    for path in (credential_path(data_dir, provider), remember_path(data_dir, provider)):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise LocalCredentialError("无法删除保存的加密密钥") from exc
