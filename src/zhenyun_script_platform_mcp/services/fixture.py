"""Fixture classification and balanced JSON extraction from log text."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..codec import PlatformCodecError, decode_platform_text
from ..exceptions import InvalidFixtureError
from ..models import FixtureStatus
from ..sanitizer import sanitize


@dataclass(frozen=True, slots=True)
class ParsedFixture:
    value: Any | None
    status: FixtureStatus


def is_placeholder(value: Any) -> bool:
    return isinstance(value, dict) and len(value) == 1 and value.get("ANYTHING") == "string"


def parse_encoded_fixture(encoded: Any) -> ParsedFixture:
    if encoded is None or encoded == "":
        return ParsedFixture(None, FixtureStatus.MISSING)
    if not isinstance(encoded, str):
        return ParsedFixture(None, FixtureStatus.INVALID)
    try:
        decoded = decode_platform_text(encoded)
        value = json.loads(decoded)
    except (PlatformCodecError, json.JSONDecodeError, UnicodeError, TypeError):
        return ParsedFixture(None, FixtureStatus.INVALID)
    if is_placeholder(value):
        return ParsedFixture(None, FixtureStatus.PLACEHOLDER)
    return ParsedFixture(value, FixtureStatus.AVAILABLE)


def _balanced_candidate(text: str, start: int) -> tuple[str | None, int]:
    pairs = {"{": "}", "[": "]"}
    stack: list[str] = []
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in pairs:
            stack.append(pairs[char])
        elif char in {"}", "]"}:
            if not stack or char != stack[-1]:
                return None, index + 1
            stack.pop()
            if not stack:
                return text[start : index + 1], index + 1
    return None, len(text)


def extract_balanced_json(
    log_text: str,
    *,
    marker: str | None = None,
    task_code: str | None = None,
) -> dict[str, Any]:
    if not isinstance(log_text, str):
        raise InvalidFixtureError("log_text must be a string")
    selected_marker = marker or (f"{task_code}:input:" if task_code else None)
    offset = 0
    if selected_marker:
        marker_at = log_text.find(selected_marker)
        if marker_at < 0:
            return {"found": False, "input": None, "sanitized_input": None}
        offset = marker_at + len(selected_marker)

    saw_candidate = False
    cursor = offset
    while cursor < len(log_text):
        starts = [
            index
            for index in (log_text.find("{", cursor), log_text.find("[", cursor))
            if index >= 0
        ]
        if not starts:
            break
        start = min(starts)
        saw_candidate = True
        candidate, next_cursor = _balanced_candidate(log_text, start)
        cursor = max(next_cursor, start + 1)
        if candidate is None:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return {"found": True, "input": value, "sanitized_input": sanitize(value)}

    if saw_candidate:
        raise InvalidFixtureError(
            "A JSON start was found in the log, but it was not valid balanced JSON"
        )
    return {"found": False, "input": None, "sanitized_input": None}
