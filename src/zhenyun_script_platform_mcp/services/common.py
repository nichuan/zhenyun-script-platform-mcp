"""Response-shape helpers shared by domain services."""

from __future__ import annotations

from typing import Any


def extract_items(payload: Any) -> list[dict[str, Any]]:
    """Find a page/list in common HZero response envelopes without losing records."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("content", "list", "rows", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    for key in ("data", "result", "body"):
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            found = extract_items(value)
            if found:
                return found
    # Some GET APIs return the header object directly.
    if any(key in payload for key in ("taskCode", "code", "adaptorTaskLines")):
        return [payload]
    return []


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def versions_equal(expected: str | int | None, actual: str | int | None) -> bool:
    return str(expected) == str(actual)
