"""Structured descriptions for Marmot resources.

The managed update prefix is an MCP convention. The platform only requires a
description beginning with a demand code; these helpers never truncate text.
"""

from __future__ import annotations

import re
from typing import Any

MAX_DESCRIPTION_LENGTH = 255
MAX_SUMMARY_LENGTH = 60
_WORK_ITEM = re.compile(r"^(cdp|srm)-\d+$", re.IGNORECASE)
_MANAGED = re.compile(r"^((?:cdp|srm)-\d+) \[(需求|修复|变更)\] (.+)$", re.IGNORECASE)
_LABELS = {"requirement": "需求", "fix": "修复", "change": "变更"}
_SEPARATOR = " | "


def _normalize(note: dict[str, Any], *, create: bool) -> tuple[str, str, str, str | None]:
    if not isinstance(note, dict) or set(note) - {
        "work_item",
        "change_type",
        "summary",
        "customer_label",
    }:
        raise ValueError("description_note must be an object with known fields")
    raw_work_item = note.get("work_item")
    if not isinstance(raw_work_item, str):
        raise TypeError("description_note.work_item must be a string")
    work_item = raw_work_item.strip().lower()
    if not _WORK_ITEM.fullmatch(work_item):
        raise ValueError("description_note.work_item must be cdp-N or srm-N")
    change_type = note.get("change_type", "change")
    if not isinstance(change_type, str) or change_type not in _LABELS:
        raise ValueError("description_note.change_type must be requirement, fix, or change")
    raw_summary = note.get("summary")
    if not isinstance(raw_summary, str) or any(
        ord(char) < 32 or 127 <= ord(char) <= 159 for char in raw_summary
    ):
        raise ValueError("description_note.summary must be single-line text without controls")
    if "|" in raw_summary:
        raise ValueError("description_note.summary must not contain |")
    summary = re.sub(r"[ \u00a0]{2,}", " ", raw_summary.strip())
    if not summary or len(summary) > MAX_SUMMARY_LENGTH:
        raise ValueError(f"description_note.summary must be 1-{MAX_SUMMARY_LENGTH} characters")
    customer_label = note.get("customer_label")
    if create:
        if not isinstance(customer_label, str):
            raise ValueError("description_note.customer_label is required on create")
        customer_label = customer_label.strip()
        if (
            not customer_label
            or len(customer_label) > 40
            or any(
                char in "【】|" or ord(char) < 32 or 127 <= ord(char) <= 159
                for char in customer_label
            )
        ):
            raise ValueError(
                "description_note.customer_label must be 1-40 characters without delimiters or controls"
            )
        if work_item in summary.lower() or work_item in customer_label.lower():
            raise ValueError(
                "description_note must not repeat the work item in summary or customer_label"
            )
    elif customer_label is not None:
        raise ValueError("description_note.customer_label is only accepted on create")
    return work_item, change_type, summary, customer_label


def _assert_length(description: str) -> str:
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(
            f"description exceeds the MCP limit of {MAX_DESCRIPTION_LENGTH} characters"
        )
    return description


def create_description(note: dict[str, Any]) -> str:
    work_item, _, summary, customer_label = _normalize(note, create=True)
    return _assert_length(f"{work_item}【{customer_label}】{summary}")


def update_description(current: Any, note: dict[str, Any]) -> str:
    work_item, change_type, summary, _ = _normalize(note, create=False)
    if current is None:
        current = ""
    if not isinstance(current, str):
        raise TypeError("current description must be text")
    marker = f"{work_item} [{_LABELS[change_type]}] {summary}"
    segments = current.split(_SEPARATOR) if current else []
    managed = []
    for segment in segments:
        match = _MANAGED.fullmatch(segment)
        if not match:
            break
        managed.append((segment, match.group(1).lower()))
    legacy = segments[len(managed) :]
    matching = [segment for segment, item in managed if item == work_item]
    if matching:
        if matching == [marker] and managed[0][0] == marker:
            return _assert_length(current)
        final = [marker, *(segment for segment, item in managed if item != work_item), *legacy]
    else:
        if not managed and re.match(rf"^{re.escape(work_item)}(?:\s|【|$)", current, re.IGNORECASE):
            raise ValueError(
                "current description starts with the same work item in an unmanaged format; provide a full description"
            )
        final = [marker, *segments]
    return _assert_length(_SEPARATOR.join(final))
