"""Windows DPAPI local credential storage; synthetic secrets only."""
from __future__ import annotations

import os

import pytest

from app.local_credentials import (
    LocalCredentialError,
    credential_path,
    enable_remember,
    forget_api_key,
    load_api_key,
    remember_enabled,
    save_api_key,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is the product boundary")
def test_current_user_dpapi_round_trip_never_writes_plaintext(tmp_path):
    synthetic = "synthetic-test-key-123456"
    marker = enable_remember(tmp_path, "deepseek")
    stored = save_api_key(tmp_path, "deepseek", synthetic)
    assert remember_enabled(tmp_path, "deepseek")
    assert marker.is_file() and stored.is_file()
    assert synthetic.encode() not in stored.read_bytes()
    assert load_api_key(tmp_path, "deepseek") == synthetic
    forget_api_key(tmp_path, "deepseek")
    assert not marker.exists() and not stored.exists()


def test_missing_saved_key_is_not_an_empty_credential(tmp_path):
    assert load_api_key(tmp_path, "gemini") is None
    assert not remember_enabled(tmp_path, "gemini")


def test_provider_name_cannot_escape_credential_directory(tmp_path):
    with pytest.raises(ValueError):
        credential_path(tmp_path, "../outside")


def test_invalid_or_truncated_blob_fails_closed(tmp_path):
    path = credential_path(tmp_path, "deepseek")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-a-cirp-dpapi-file")
    with pytest.raises(LocalCredentialError, match="格式无效"):
        load_api_key(tmp_path, "deepseek")
