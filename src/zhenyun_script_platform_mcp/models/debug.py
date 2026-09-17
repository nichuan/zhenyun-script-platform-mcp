from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class DebugResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    result: Any = None
    logs: str | None = None
    execution_info: Any | None = None
    query_block_sql: Any | None = None
    raw_result: Any | None = None
