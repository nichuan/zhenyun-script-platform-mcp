"""Recursive redaction used at every MCP/logging boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "<REDACTED>"
_SENSITIVE_KEYS = {
    "authorization",
    "jwt_token",
    "access_token",
    "refresh_token",
    "cookie",
    "password",
    "_token",
}
_TEXT_SECRET = re.compile(
    r"(?i)(authorization|jwt_token|access_token|refresh_token|cookie|password)"
    r"(\s*[:=]\s*)(Bearer\s+[A-Za-z0-9._~+/=-]+|[^\s,;]+|\"[^\"]*\")"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def _key_is_sensitive(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS


def sanitize_text(value: str) -> str:
    value = _TEXT_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", value)
    return _BEARER.sub("Bearer <REDACTED>", value)


def sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _key_is_sensitive(key) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize(item) for item in value)
    return value
