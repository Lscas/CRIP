"""Validate and persist a customer-selected model endpoint without storing plaintext keys."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import secrets

from app.local_credentials import (LocalCredentialError, enable_remember, load_api_key,
                                   remember_enabled, save_api_key)
from app.settings import (DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_VISION_MODEL,
                          GEMINI_BASE_URL, GEMINI_MODEL, Settings)


PROFILE_VERSION = 2
PROFILE_FILE = "active-model-profile.json"
DEFAULTS = {
    "deepseek": {
        "api_base_url": DEEPSEEK_BASE_URL,
        "model": DEEPSEEK_MODEL,
        "min_interval": 1.0,
    },
    "gemini": {
        "api_base_url": GEMINI_BASE_URL,
        "model": GEMINI_MODEL,
        "min_interval": 15.0,
    },
}


def profile_path(data_dir: Path) -> Path:
    return Path(data_dir).resolve() / "credentials" / PROFILE_FILE


def custom_provider_name(api_base_url: str, model: str) -> str:
    value = api_base_url.rstrip("/") + "\0" + model
    return "custom-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def provider_kind(settings: Settings) -> str:
    return settings.provider if settings.provider in ("deepseek", "gemini", "mock") else "custom"


def _valid_key(value: str) -> bool:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return 16 <= len(encoded) <= 256 and not any(char.isspace() for char in value)


def configured_settings(
    current: Settings,
    *,
    provider: str,
    api_base_url: str = "",
    model: str = "",
    api_key: str = "",
) -> Settings:
    """Build one validated in-memory configuration; no files or HTTP are touched."""
    kind = provider.strip().lower()
    if kind == "mock":
        return replace(
            current, provider="mock", api_key="", live_enabled=False,
            vision_enabled=False,
            min_request_interval_seconds=0,
        )
    if kind not in ("deepseek", "gemini", "custom"):
        raise ValueError("Choose DeepSeek, Gemini, a custom API, or Mock mode.")

    if kind in DEFAULTS:
        preset = DEFAULTS[kind]
        base_url = str(preset["api_base_url"])
        selected_model = str(preset["model"])
        provider_id = kind
        interval = float(preset["min_interval"])
    else:
        base_url = api_base_url.strip().rstrip("/")
        selected_model = model.strip()
        provider_id = custom_provider_name(base_url, selected_model)
        interval = 0.0

    local_candidate = replace(
        current, provider=provider_id, api_base_url=base_url,
        api_key=api_key.strip(), cheap_model=selected_model,
        vision_enabled=kind == "deepseek",
        vision_model=DEEPSEEK_VISION_MODEL,
        live_enabled=True,
        min_request_interval_seconds=interval,
    )
    key = local_candidate.api_key
    if not key and not local_candidate.is_local_model() and remember_enabled(current.data_dir, provider_id):
        try:
            key = load_api_key(current.data_dir, provider_id) or ""
        except LocalCredentialError as exc:
            raise ValueError("The saved API key cannot be decrypted by this Windows user.") from exc
        local_candidate = replace(local_candidate, api_key=key)
    if key and not _valid_key(key):
        raise ValueError("The API key must be 16-256 ASCII characters without spaces.")
    errors = local_candidate.live_errors()
    if errors:
        raise ValueError(errors[0])
    return local_candidate


def public_configuration(settings: Settings) -> dict:
    kind = provider_kind(settings)
    saved = (kind != "mock" and remember_enabled(settings.data_dir, settings.provider))
    return {
        "provider": kind,
        "provider_identity": settings.provider,
        "api_base_url": "" if kind == "mock" else settings.api_base_url,
        "model": "" if kind == "mock" else settings.cheap_model,
        "local_model": settings.is_local_model(),
        "saved_key": saved,
        "saved_profile": profile_path(settings.data_dir).exists(),
        "vision_enabled": bool(settings.provider == "deepseek" and settings.vision_enabled),
    }


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(8))
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
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


def save_active_configuration(settings: Settings) -> None:
    """Persist only non-secret profile data; remote keys remain DPAPI encrypted."""
    if settings.provider == "mock":
        clear_active_configuration(settings.data_dir)
        return
    if not settings.is_local_model():
        enable_remember(settings.data_dir, settings.provider)
        save_api_key(settings.data_dir, settings.provider, settings.api_key)
    value = {
        "version": PROFILE_VERSION,
        "provider": provider_kind(settings),
        "api_base_url": settings.api_base_url,
        "model": settings.cheap_model,
    }
    _atomic_json(profile_path(settings.data_dir), value)


def clear_active_configuration(data_dir: Path) -> None:
    try:
        profile_path(data_dir).unlink(missing_ok=True)
    except OSError as exc:
        raise LocalCredentialError("The saved model profile could not be removed.") from exc


def load_active_configuration(current: Settings) -> Settings | None:
    path = profile_path(current.data_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LocalCredentialError("The saved model profile could not be read.") from exc
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise LocalCredentialError("The saved model profile is invalid.") from exc
    current_fields = {"version", "provider", "api_base_url", "model"}
    legacy_fields = current_fields | {"input_rate", "output_rate"}
    if (not isinstance(value, dict) or value.get("version") not in (1, PROFILE_VERSION)
            or set(value) not in (current_fields, legacy_fields)):
        raise LocalCredentialError("The saved model profile is invalid.")
    if any(not isinstance(value.get(key), str) for key in current_fields - {"version"}):
        raise LocalCredentialError("The saved model profile is invalid.")
    try:
        return configured_settings(
            current, provider=value["provider"], api_base_url=value["api_base_url"],
            model=value["model"],
        )
    except ValueError as exc:
        raise LocalCredentialError("The saved model profile is not usable: " + str(exc)) from exc
