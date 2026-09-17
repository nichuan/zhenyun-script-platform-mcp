from __future__ import annotations

from typing import Any

from ..services.adapter import AdapterService


def get_adapter(
    service: AdapterService,
    *,
    tenant_num: str,
    task_code: str,
    running_service: str,
) -> dict[str, Any]:
    return service.get(
        tenant_num=tenant_num,
        task_code=task_code,
        running_service=running_service,
    ).model_dump(mode="json")


def debug_adapter(
    service: AdapterService,
    *,
    tenant_num: str,
    task_code: str,
    running_service: str,
    source: str,
    raw_input: Any,
    line_id: str | int | None = None,
) -> dict[str, Any]:
    return service.debug(
        tenant_num=tenant_num,
        task_code=task_code,
        running_service=running_service,
        source=source,
        raw_input=raw_input,
        line_id=line_id,
    ).model_dump(mode="json")


def deploy_adapter(
    service: AdapterService,
    *,
    tenant_num: str,
    task_code: str,
    running_service: str,
    source: str,
    line_id: str | int | None = None,
    expected_header_version: str | int | None = None,
    expected_line_version: str | int | None = None,
) -> dict[str, Any]:
    return service.deploy(
        tenant_num=tenant_num,
        task_code=task_code,
        running_service=running_service,
        source=source,
        line_id=line_id,
        expected_header_version=expected_header_version,
        expected_line_version=expected_line_version,
    )
