"""Recursive redaction used at every MCP/logging boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "<REDACTED>"

# 精确匹配的敏感键（键名先归一化为 snake_case 再比较，覆盖 camelCase / kebab-case）。
_SENSITIVE_KEYS = {
    "authorization",
    "jwt_token",
    "access_token",
    "refresh_token",
    "id_token",
    "cookie",
    "set_cookie",
    "password",
    "passwd",
    "pwd",
    "secret",
    "client_secret",
    "api_key",
    "apikey",
    "token",
    "session",
    "session_id",
    "credential",
    "credentials",
    "private_key",
}

# 通用后缀：覆盖 userToken / authToken / bearerToken 等派生命名，以及原 _token。
_SENSITIVE_SUFFIXES = (
    "_token",
    "_secret",
    "_password",
    "_passwd",
    "_pwd",
    "_cookie",
    "_credential",
    "_credentials",
    "_apikey",
)

# 协议级短时票据，不是凭据：必须原样回传给 Agent 才能完成两阶段确认，禁止脱敏。
_NON_SECRET_KEYS = {
    "confirmation_token",
}

# 文本内嵌凭据：关键字段名 + 分隔符 + 值（值支持 Bearer 形式、裸值、带引号值）。
_TEXT_SECRET = re.compile(
    r"(?i)\b(authorization|jwt[_-]?token|access[_-]?token|refresh[_-]?token|id[_-]?token"
    r"|client[_-]?secret|api[_-]?key|apikey|cookie|password|passwd|secret)"
    r"(\s*[:=]\s*)(Bearer\s+[A-Za-z0-9._~+/=-]+|[^\s,;]+|\"[^\"]*\")"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_MASKED_SOURCE = re.compile(
    r"(?i)(?:<<?(?:redacted|masked)(?::\d+)?>>?|__(?:redacted|masked)(?:_[a-z0-9]+)*__)"
)


def _normalize_key(key: object) -> str:
    """把键名归一化为 snake_case：accessToken / Access-Token -> access_token。"""
    text = str(key).strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return text.lower().replace("-", "_")


def _key_is_sensitive(key: object) -> bool:
    normalized = _normalize_key(key)
    if normalized in _NON_SECRET_KEYS:
        return False
    if normalized in _SENSITIVE_KEYS:
        return True
    return normalized.endswith(_SENSITIVE_SUFFIXES)


def sanitize_text(value: str) -> str:
    value = _TEXT_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", value)
    return _BEARER.sub("Bearer <REDACTED>", value)


def sanitize(value: Any, *, preserve_fields: frozenset[str] = frozenset()) -> Any:
    """Redact secrets recursively while preserving explicitly trusted text fields.

    Source code is an MCP deliverable whose bytes must survive a get/edit/write round trip.
    Callers must opt specific source field names into ``preserve_fields``; all other strings,
    including saved fixtures and error text, continue through the normal redaction rules.
    """
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            public_key = str(key)
            if _key_is_sensitive(key):
                cleaned[public_key] = REDACTED
            elif public_key in preserve_fields and isinstance(item, str):
                cleaned[public_key] = item
            else:
                cleaned[public_key] = sanitize(item, preserve_fields=preserve_fields)
        return cleaned
    if isinstance(value, list):
        return [sanitize(item, preserve_fields=preserve_fields) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize(item, preserve_fields=preserve_fields) for item in value)
    if isinstance(value, str):
        # 字符串值同样过文本脱敏：覆盖响应正文/错误详情里内嵌的凭据。
        return sanitize_text(value)
    return value


def validate_source_integrity(source: str) -> None:
    """Reject known redaction placeholders before source reaches debug or persistence APIs."""
    if not isinstance(source, str):
        raise TypeError("source must be a string")
    match = _MASKED_SOURCE.search(source)
    if match:
        raise ValueError(
            "Source contains a redaction placeholder "
            f"{match.group(0)!r}; reload the original source before debug or save"
        )
