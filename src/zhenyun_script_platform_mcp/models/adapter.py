from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .independent import FixtureStatus


class AdapterLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | int
    header_id: str | int | None = None
    priority: int | None = None
    script_type: str | None = None
    output_entity_code: str | None = None
    object_version_number: str | int | None = None
    source: str
    source_hash: str
    saved_test_input: Any | None = None
    test_input_status: FixtureStatus


class Adapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | int
    task_code: str
    description: str | None = None
    input_entity_code: str | None = None
    tenant_num: str
    running_service: str
    script_version: str | int | None = None
    enabled: bool
    object_version_number: str | int | None = None
    lines: list[AdapterLine]
