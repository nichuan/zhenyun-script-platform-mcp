from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class FixtureStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PLACEHOLDER = "PLACEHOLDER"
    INVALID = "INVALID"
    MISSING = "MISSING"


class IndependentScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | int
    code: str
    tenant_num: str
    tenant_id: str | int | None = None
    quick_type: str | None = None
    description: str | None = None
    object_version_number: str | int | None = None
    source: str
    source_hash: str
    saved_test_input: Any | None = None
    test_input_status: FixtureStatus
