"""Small shared boundaries for child-process environment isolation."""
from __future__ import annotations

import os
from collections.abc import Mapping


SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTHORIZATION",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)


def environment_without_secrets(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy an environment while removing names that commonly carry credentials."""
    env = dict(os.environ if source is None else source)
    for name in list(env):
        if any(marker in name.upper() for marker in SENSITIVE_ENV_MARKERS):
            env.pop(name, None)
    return env
