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
from app.settings import (DEEPSEEK_BASE_URL, DEEPSEEK_FLASH_MODEL, DEEPSEEK_MODEL,
                          DEEPSEEK_PRO_MODEL, GEMINI_BASE_URL, GEMINI_MODEL,
                          OPENAI_BASE_URL, Settings)


PROFILE_VERSION = 6
PROFILE_FILE = "active-model-profile.json"
DEFAULTS = {
    "deepseek": {
        "api_base_url": DEEPSEEK_BASE_URL,
        "model": DEEPSEEK_FLASH_MODEL,
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
    return settings.provider if settings.provider in ("deepseek", "gemini", "openai", "mock") else "custom"


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
    vision_enabled: bool = False,
    structured_output_mode: str = "json_object",
    reasoning_effort: str = "none",
) -> Settings:
    """Build one validated in-memory configuration; no files or HTTP are touched."""
    kind = provider.strip().lower()
    if not isinstance(structured_output_mode,str):
        raise ValueError("Choose JSON object or strict JSON Schema output.")
    output_mode = structured_output_mode.strip().lower()
    if output_mode not in ("json_object", "json_schema"):
        raise ValueError("Choose JSON object or strict JSON Schema output.")
    if kind not in ("custom", "openai") and output_mode != "json_object":
        raise ValueError("Strict JSON Schema output is available only for OpenAI or a custom OpenAI-compatible API.")
    if not isinstance(reasoning_effort,str):
        raise ValueError("Choose no thinking or low, high, or max Reference reasoning.")
    selected_effort=reasoning_effort.strip().lower()
    if selected_effort not in ('none','low','high','max'):
        raise ValueError("Choose no thinking or low, high, or max Reference reasoning.")
    if kind!='deepseek' and selected_effort!='none':
        raise ValueError("Bounded Reference reasoning is currently supported only for DeepSeek.")
    if kind == "mock":
        return replace(
            current, provider="mock", api_key="", live_enabled=False,
            vision_enabled=False, structured_output_mode="json_object",api_protocol="chat_completions",
            reference_reasoning_effort='none',
            min_request_interval_seconds=0,
        )
    if kind not in ("deepseek", "gemini", "openai", "custom"):
        raise ValueError("Choose OpenAI, DeepSeek, Gemini, a custom API, or Mock mode.")

    if kind == "deepseek":
        preset = DEFAULTS[kind]
        base_url = str(preset["api_base_url"])
        selected_model = model.strip() or str(preset["model"])
        if selected_model == DEEPSEEK_MODEL:
            selected_model = DEEPSEEK_FLASH_MODEL
        if selected_model not in (DEEPSEEK_FLASH_MODEL, DEEPSEEK_PRO_MODEL):
            raise ValueError("This release requires DeepSeek V4.1 Flash or V4 Pro.")
        provider_id = kind
        interval = float(preset["min_interval"])
        protocol = "chat_completions"
    elif kind in DEFAULTS:
        preset = DEFAULTS[kind]
        base_url = str(preset["api_base_url"])
        selected_model = str(preset["model"])
        provider_id = kind
        interval = float(preset["min_interval"])
        protocol = "chat_completions"
    elif kind == "openai":
        base_url = OPENAI_BASE_URL
        selected_model = model.strip()
        provider_id = kind
        interval = 0.0
        protocol = "responses"
    else:
        base_url = api_base_url.strip().rstrip("/")
        selected_model = model.strip()
        provider_id = custom_provider_name(base_url, selected_model)
        interval = 0.0
        protocol = "chat_completions"

    local_candidate = replace(
        current, provider=provider_id, api_base_url=base_url,
        api_key=api_key.strip(), cheap_model=selected_model,
        vision_enabled=kind in ("deepseek", "openai", "custom") and vision_enabled,
        vision_model=(selected_model if kind in ("deepseek", "openai", "custom") else selected_model),
        structured_output_mode=output_mode,
        api_protocol=protocol,
        reference_reasoning_effort=selected_effort,
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
        "vision_enabled": bool(settings.provider != "mock" and settings.vision_enabled),
        "structured_output_mode": settings.structured_output_mode,
        "api_protocol": settings.api_protocol,
        "reasoning_effort": settings.reference_reasoning_effort,
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
        "vision_enabled": settings.vision_enabled,
        "structured_output_mode": settings.structured_output_mode,
        "api_protocol": settings.api_protocol,
        "reasoning_effort": settings.reference_reasoning_effort,
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
    old_fields = {"version", "provider", "api_base_url", "model"}
    legacy_fields = old_fields | {"input_rate", "output_rate"}
    vision_fields = old_fields | {"vision_enabled"}
    structured_fields = vision_fields | {"structured_output_mode"}
    current_fields = structured_fields | {"api_protocol"}
    reasoning_fields = current_fields | {"reasoning_effort"}
    if (not isinstance(value, dict) or value.get("version") not in (1,2,3,4,5,PROFILE_VERSION)
            or set(value) not in (
                old_fields, legacy_fields, vision_fields, structured_fields,
                current_fields, reasoning_fields)):
        raise LocalCredentialError("The saved model profile is invalid.")
    if any(not isinstance(value.get(key), str) for key in old_fields - {"version"}):
        raise LocalCredentialError("The saved model profile is invalid.")
    if 'vision_enabled' in value and type(value['vision_enabled']) is not bool:
        raise LocalCredentialError("The saved model profile is invalid.")
    if ('structured_output_mode' in value
            and value['structured_output_mode'] not in ('json_object','json_schema')):
        raise LocalCredentialError("The saved model profile is invalid.")
    if 'api_protocol' in value and value['api_protocol'] not in ('chat_completions','responses'):
        raise LocalCredentialError("The saved model profile is invalid.")
    if 'reasoning_effort' in value and value['reasoning_effort'] not in ('none','low','high','max'):
        raise LocalCredentialError("The saved model profile is invalid.")
    try:
        configured=configured_settings(
            current, provider=value["provider"], api_base_url=value["api_base_url"],
            model=value["model"],vision_enabled=value.get('vision_enabled',False),
            structured_output_mode=value.get('structured_output_mode','json_object'),
            reasoning_effort=value.get('reasoning_effort','none'),
        )
        if configured.api_protocol!=value.get('api_protocol',configured.api_protocol):
            raise ValueError('The saved API protocol does not match its provider.')
        return configured
    except ValueError as exc:
        raise LocalCredentialError("The saved model profile is not usable: " + str(exc)) from exc
