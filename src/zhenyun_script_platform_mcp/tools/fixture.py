from __future__ import annotations

from typing import Any

from ..services.fixture import extract_balanced_json


def extract_input(
    *, log_text: str, marker: str | None = None, task_code: str | None = None
) -> dict[str, Any]:
    return extract_balanced_json(log_text, marker=marker, task_code=task_code)
