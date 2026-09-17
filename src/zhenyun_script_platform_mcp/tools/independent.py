from __future__ import annotations

from typing import Any

from ..services.debug import DebugService
from ..services.independent import IndependentScriptService


def get_script(service: IndependentScriptService, *, tenant_num: str, code: str) -> dict[str, Any]:
    return service.get(tenant_num=tenant_num, code=code).model_dump(mode="json")


def debug_script(
    service: DebugService,
    *,
    tenant_num: str,
    source: str,
    raw_input: Any,
) -> dict[str, Any]:
    return service.run(tenant_num=tenant_num, source=source, raw_input=raw_input).model_dump(
        mode="json"
    )


def save_script(
    service: IndependentScriptService,
    *,
    tenant_num: str,
    code: str,
    source: str,
    expected_version: str | int | None = None,
) -> dict[str, Any]:
    return service.save(
        tenant_num=tenant_num,
        code=code,
        source=source,
        expected_version=expected_version,
    )
