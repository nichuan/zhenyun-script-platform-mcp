"""FastMCP stdio entrypoint for Script Platform lifecycle and verified resources."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from .client import ScriptPlatformClient
from .config import Settings
from .confirmation import ConfirmationManager
from .exceptions import ScriptPlatformError
from .resources import RequirementResourceType, ResourceType
from .sanitizer import sanitize, sanitize_text
from .services import (
    AdapterService,
    DebugService,
    IndependentScriptService,
    PlatformResourceService,
)
from .tools import adapter as adapter_tools
from .tools import fixture as fixture_tools
from .tools import independent as independent_tools
from .tools import platform as platform_tools

FastMCPSettings.model_rebuild()
mcp = FastMCP(
    "zhenyun-script-platform-mcp",
    instructions=(
        "This server is the authoritative source for current Script Platform source, version, "
        "fixtures, adapter state, verified Marmot resources, definitions, relationships, and "
        "registered table actions. Pangu script tools may discover an identity but must not "
        "replace authoritative get calls from this server. Develop scripts against the remote "
        "DEV GraalJS runtime. Debug calls never save. Only call any save/create/update/delete/"
        "toggle/deploy/action tool uses mandatory two-phase confirmation. The first call only "
        "returns a signed plan. Show it to the user and stop; only a later explicit confirmation "
        "allows a second identical call with confirmation_token. Never confirm on the user's behalf."
    ),
)


@dataclass(slots=True)
class Runtime:
    settings: Settings
    client: ScriptPlatformClient
    independent: IndependentScriptService
    debug: DebugService
    adapter: AdapterService
    platform: PlatformResourceService
    confirmation: ConfirmationManager


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        settings = Settings.from_env()
        client = ScriptPlatformClient(settings)
        debug = DebugService(client, settings)
        _runtime = Runtime(
            settings=settings,
            client=client,
            independent=IndependentScriptService(client, settings),
            debug=debug,
            adapter=AdapterService(client, settings, debug),
            platform=PlatformResourceService(client, settings),
            confirmation=ConfirmationManager(settings.confirmation_ttl_seconds),
        )
    return _runtime


def _serialize(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, default=str)


def _invoke(action: Callable[[], Any]) -> str:
    try:
        result = sanitize(action())
        if isinstance(result, dict):
            return _serialize({"ok": "error" not in result, **result})
        return _serialize({"ok": True, "result": result})
    except ScriptPlatformError as exc:
        error = exc.as_dict()
        error["message"] = sanitize_text(error["message"])
        error["details"] = sanitize(error.get("details", {}))
        return _serialize({"ok": False, "error": error})
    except (TypeError, ValueError) as exc:
        return _serialize(
            {
                "ok": False,
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": sanitize_text(str(exc)),
                    "retryable": False,
                },
            }
        )
    except Exception as exc:  # transport boundary: never leak a traceback to the agent
        logging.getLogger(__name__).exception("Unhandled Script Platform tool failure")
        detail = sanitize_text(str(exc).strip())
        message = f"Unexpected {type(exc).__name__}"
        if detail:
            message = f"{message}: {detail}"
        return _serialize(
            {
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": message,
                    "retryable": False,
                },
            }
        )


_TARGET_FIELDS = (
    "resource_type",
    "tenant",
    "tenant_num",
    "code",
    "task_code",
    "running_service",
    "line_id",
    "action_id",
    "enabled",
    "enable",
    "expected_version",
    "expected_header_version",
    "expected_line_version",
)


def _value_preview(field: str, value: Any) -> Any:
    normalized = field.lower()
    if isinstance(value, str) and any(
        marker in normalized
        for marker in ("source", "content", "script", "password", "secret", "token", "value")
    ):
        return {
            "type": "text",
            "chars": len(value),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
    if isinstance(value, dict):
        return {key: _value_preview(str(key), item) for key, item in value.items()}
    if isinstance(value, list):
        return [_value_preview(field, item) for item in value]
    return value


def _creation_warnings(arguments: dict[str, Any]) -> list[str]:
    record = arguments.get("record")
    if not isinstance(record, dict):
        return []
    quick_type = str(record.get("quickType", "")).strip().lower()
    related = {
        "api": "api_publish",
        "api_pre": "api_publish",
        "api_post": "api_publish",
        "api_publish": "api_publish",
        "consumer": "queue_consumer",
        "schedule": "scheduler",
    }.get(quick_type)
    if not related:
        return []
    return [
        (
            f"Platform may create or link a related {related} record for "
            f"quickType={record.get('quickType')!r}; check platform_relations_get "
            "before deleting the script."
        )
    ]


def _write_preview(arguments: dict[str, Any]) -> dict[str, Any]:
    preview: dict[str, Any] = {
        "target": {key: arguments[key] for key in _TARGET_FIELDS if key in arguments},
        "request_sha256": hashlib.sha256(
            json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
    }
    for field in ("record", "changes", "source", "description", "input_entity_code"):
        if field in arguments:
            preview[field] = _value_preview(field, arguments[field])
    warnings = _creation_warnings(arguments)
    if warnings:
        preview["warnings"] = warnings
    return preview


def _invoke_write(
    *,
    tool: str,
    arguments: dict[str, Any],
    confirmation_token: str | None,
    action: Callable[[], Any],
    preflight: Callable[[], Any] | None = None,
) -> str:
    def guarded() -> Any:
        runtime = get_runtime()
        if not confirmation_token:
            if preflight is not None:
                preflight()
            return runtime.confirmation.prepare(
                tool=tool,
                arguments=arguments,
                preview=_write_preview(arguments),
            )
        runtime.confirmation.consume(
            tool=tool,
            arguments=arguments,
            token=confirmation_token,
        )
        result = action()
        if isinstance(result, dict):
            return {**result, "human_confirmation": "verified"}
        return {"result": result, "human_confirmation": "verified"}

    return _invoke(guarded)


READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
REMOTE_EXECUTION = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
PLATFORM_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)


@mcp.tool(annotations=READ_ONLY)
def platform_context_get(validate_remote: bool = False) -> str:
    """读取脱敏的平台环境与认证元数据；可选验证 DEV 连接，返回 ok 或 error.retryable。"""

    def action() -> dict[str, Any]:
        runtime = get_runtime()
        result: dict[str, Any] = {
            "environment": "configured Script Platform target",
            "base_url": runtime.settings.base_url,
            "host": runtime.settings.host,
            "write_policy": {
                "mode": "two_phase_human_confirmation",
                "confirmation_ttl_seconds": runtime.settings.confirmation_ttl_seconds,
            },
            "auth": runtime.client.auth_metadata(),
        }
        if validate_remote:
            result["remote_identity"] = runtime.client.get("/sada/v1/marmot-admin/is-admin")
        return result

    return _invoke(action)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def platform_capabilities_list() -> str:
    """列出已实现的平台资源、写入覆盖和能力边界；只读，返回 ok 或 error.retryable。"""
    return _invoke(lambda: get_runtime().platform.capabilities())


@mcp.tool(annotations=READ_ONLY)
def platform_resource_search(
    resource_type: ResourceType,
    tenant: str | None = None,
    code: str | None = None,
    text: str | None = None,
    trace_id: str | None = None,
    last_minutes: int = 60,
    page: int = 0,
    size: int | None = None,
) -> str:
    """按封闭 resource_type 检索已验证的平台资源；列表文本有界且秘密常量脱敏，返回 ok 或 error.retryable。"""
    return _invoke(
        lambda: platform_tools.search_resources(
            get_runtime().platform,
            resource_type=resource_type,
            tenant=tenant,
            code=code,
            text=text,
            trace_id=trace_id,
            last_minutes=last_minutes,
            page=page,
            size=size,
        )
    )


@mcp.tool(annotations=READ_ONLY)
def platform_requirement_artifacts_search(
    requirement_code: str,
    tenant: str | None = None,
    resource_types: list[RequirementResourceType] | None = None,
    size: int | None = None,
) -> str:
    """按需求号聚合检索 Adapter、Independent、CodeBlock、QueryBlock 与 API 关系资源；只读且不返回完整源码。"""
    return _invoke(
        lambda: platform_tools.search_requirement_artifacts(
            get_runtime().platform,
            requirement_code=requirement_code,
            tenant=tenant,
            resource_types=resource_types,
            size=size,
        )
    )


@mcp.tool(annotations=READ_ONLY)
def platform_resource_get(
    resource_type: ResourceType,
    code: str,
    tenant: str | None = None,
) -> str:
    """按 resource_type、code 和可选租户精确读取当前资源；拒绝缺失或歧义结果，返回 ok 或 error.retryable。"""
    return _invoke(
        lambda: platform_tools.get_resource(
            get_runtime().platform,
            resource_type=resource_type,
            code=code,
            tenant=tenant,
        )
    )


@mcp.tool(annotations=READ_ONLY)
def platform_definition_get(resource_type: ResourceType) -> str:
    """读取 resource_type 的字段与动作定义，不返回大段 mappingJson；只读，返回 ok 或 error.retryable。"""
    return _invoke(
        lambda: platform_tools.get_definition(get_runtime().platform, resource_type=resource_type)
    )


@mcp.tool(annotations=READ_ONLY)
def platform_relations_get(
    code: str,
    tenant: str | None = None,
    scan_size: int = 50,
    scheduler_tenant_id: str | int | None = None,
) -> str:
    """有界扫描脚本或 CodeBlock 关联；scheduler 需数字 tenantId，adapter_event 为全局，返回 ok 或 error.retryable。"""
    return _invoke(
        lambda: platform_tools.get_relations(
            get_runtime().platform,
            code=code,
            tenant=tenant,
            scan_size=scan_size,
            scheduler_tenant_id=scheduler_tenant_id,
        )
    )


@mcp.tool(annotations=READ_ONLY)
def platform_api_point_list(
    api_code: str | None = None,
    server_name: str | None = None,
    page: int = 0,
    size: int | None = None,
) -> str:
    """列出已验证的 API 改写挂载点；挂载使用 api_rewrite CRUD，返回 ok 或 error.retryable。"""
    return _invoke(
        lambda: platform_tools.list_api_points(
            get_runtime().platform,
            api_code=api_code,
            server_name=server_name,
            page=page,
            size=size,
        )
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def platform_resource_create(
    resource_type: ResourceType,
    tenant: str,
    record: dict[str, Any],
    confirmation_token: str | None = None,
) -> str:
    """生成 resource_type 创建计划；仅后续人工确认并携带一次性 confirmation_token 才执行，返回 ok 或 error.retryable。"""
    arguments = {"resource_type": resource_type, "tenant": tenant, "record": record}
    return _invoke_write(
        tool="platform_resource_create",
        arguments=arguments,
        confirmation_token=confirmation_token,
        preflight=lambda: get_runtime().platform.validate_create(
            resource_type=resource_type,
            tenant=tenant,
            record=record,
        ),
        action=lambda: platform_tools.create_resource(
            get_runtime().platform,
            resource_type=resource_type,
            tenant=tenant,
            record=record,
        ),
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def platform_resource_save(
    resource_type: ResourceType,
    tenant: str,
    code: str,
    changes: dict[str, Any],
    expected_version: str | int,
    confirmation_token: str | None = None,
) -> str:
    """生成带 expected_version 的版本保护更新计划；仅后续人工确认并携带一次性 confirmation_token 才执行。"""
    arguments = {
        "resource_type": resource_type,
        "tenant": tenant,
        "code": code,
        "changes": changes,
        "expected_version": expected_version,
    }
    return _invoke_write(
        tool="platform_resource_save",
        arguments=arguments,
        confirmation_token=confirmation_token,
        action=lambda: platform_tools.save_resource(
            get_runtime().platform,
            resource_type=resource_type,
            tenant=tenant,
            code=code,
            changes=changes,
            expected_version=expected_version,
        ),
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def platform_resource_delete(
    resource_type: ResourceType,
    tenant: str,
    code: str,
    expected_version: str | int,
    confirmation_token: str | None = None,
) -> str:
    """生成不可逆删除计划；必须携带 expected_version，并经后续人工确认和一次性 confirmation_token 才执行。"""
    arguments = {
        "resource_type": resource_type,
        "tenant": tenant,
        "code": code,
        "expected_version": expected_version,
    }
    return _invoke_write(
        tool="platform_resource_delete",
        arguments=arguments,
        confirmation_token=confirmation_token,
        action=lambda: platform_tools.delete_resource(
            get_runtime().platform,
            resource_type=resource_type,
            tenant=tenant,
            code=code,
            expected_version=expected_version,
        ),
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def platform_table_action(
    resource_type: ResourceType,
    tenant: str,
    code: str,
    action_id: int,
    expected_version: str | int,
    confirmation_token: str | None = None,
) -> str:
    """生成已登记的平台副作用动作计划；必须携带 expected_version，并经后续人工确认和一次性 confirmation_token 才执行。"""
    arguments = {
        "resource_type": resource_type,
        "tenant": tenant,
        "code": code,
        "action_id": action_id,
        "expected_version": expected_version,
    }
    return _invoke_write(
        tool="platform_table_action",
        arguments=arguments,
        confirmation_token=confirmation_token,
        action=lambda: platform_tools.execute_table_action(
            get_runtime().platform,
            resource_type=resource_type,
            tenant=tenant,
            code=code,
            action_id=action_id,
            expected_version=expected_version,
        ),
    )


@mcp.tool(annotations=READ_ONLY)
def independent_script_get(tenant_num: str, code: str) -> str:
    """权威读取 Independent Script 当前源码、版本、哈希和 Fixture；只读，返回 ok 或 error.retryable。"""
    return _invoke(
        lambda: independent_tools.get_script(
            get_runtime().independent, tenant_num=tenant_num, code=code
        )
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def independent_script_create(
    tenant_num: str,
    code: str,
    quick_type: str,
    description: str,
    permission: str,
    module: str,
    source: str = "",
    raw_input: Any | None = None,
    confirmation_token: str | None = None,
) -> str:
    """生成 Independent Script 创建计划；仅后续人工确认并携带一次性 confirmation_token 才执行，平台负责编码。"""
    record: dict[str, Any] = {
        "code": code,
        "quickType": quick_type,
        "description": description,
        "permission": permission,
        "module": module,
        "content": source,
    }
    if raw_input is not None:
        record["contentInput"] = (
            raw_input
            if isinstance(raw_input, str)
            else json.dumps(raw_input, ensure_ascii=False, separators=(",", ":"))
        )
    arguments = {"tenant_num": tenant_num, "record": record}
    return _invoke_write(
        tool="independent_script_create",
        arguments=arguments,
        confirmation_token=confirmation_token,
        preflight=lambda: get_runtime().platform.validate_create(
            resource_type="independent_script",
            tenant=tenant_num,
            record=record,
        ),
        action=lambda: platform_tools.create_resource(
            get_runtime().platform,
            resource_type="independent_script",
            tenant=tenant_num,
            record=record,
        ),
    )


@mcp.tool(annotations=REMOTE_EXECUTION)
def independent_script_debug(tenant_num: str, source: str, raw_input: Any) -> str:
    """在 DEV 执行未保存的 Independent Script 源码；不持久化任何变更，返回 ok 或 error.retryable。"""
    return _invoke(
        lambda: independent_tools.debug_script(
            get_runtime().debug,
            tenant_num=tenant_num,
            source=source,
            raw_input=raw_input,
        )
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def independent_script_save(
    tenant_num: str,
    code: str,
    source: str,
    expected_version: str | int,
    confirmation_token: str | None = None,
) -> str:
    """生成带 expected_version 的 Independent Script 保存计划；仅后续人工确认并携带一次性 confirmation_token 才执行。"""
    arguments = {
        "tenant_num": tenant_num,
        "code": code,
        "source": source,
        "expected_version": expected_version,
    }
    return _invoke_write(
        tool="independent_script_save",
        arguments=arguments,
        confirmation_token=confirmation_token,
        action=lambda: independent_tools.save_script(
            get_runtime().independent,
            tenant_num=tenant_num,
            code=code,
            source=source,
            expected_version=expected_version,
        ),
    )


@mcp.tool(annotations=READ_ONLY)
def adapter_get(tenant_num: str, task_code: str, running_service: str) -> str:
    """权威读取 Adapter Header、状态、版本和解码后的 Lines；只读，返回 ok 或 error.retryable。"""
    return _invoke(
        lambda: adapter_tools.get_adapter(
            get_runtime().adapter,
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
        )
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def adapter_create(
    tenant_num: str,
    task_code: str,
    running_service: str,
    description: str = "",
    input_entity_code: str = "ANYTHING",
    confirmation_token: str | None = None,
) -> str:
    """生成禁用 Adapter 创建计划；task_code 必须已在事件注册表存在，需后续人工确认和一次性 confirmation_token 执行。"""
    arguments = {
        "tenant_num": tenant_num,
        "task_code": task_code,
        "running_service": running_service,
        "description": description,
        "input_entity_code": input_entity_code,
    }
    return _invoke_write(
        tool="adapter_create",
        arguments=arguments,
        confirmation_token=confirmation_token,
        action=lambda: adapter_tools.create_adapter(
            get_runtime().adapter,
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
            description=description,
            input_entity_code=input_entity_code,
        ),
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def adapter_update(
    tenant_num: str,
    task_code: str,
    running_service: str,
    changes: dict[str, Any],
    expected_header_version: str | int,
    confirmation_token: str | None = None,
) -> str:
    """生成带 expected_header_version 的 Adapter Header 更新计划；仅后续人工确认并携带一次性 confirmation_token 才执行。"""
    arguments = {
        "tenant_num": tenant_num,
        "task_code": task_code,
        "running_service": running_service,
        "changes": changes,
        "expected_header_version": expected_header_version,
    }
    return _invoke_write(
        tool="adapter_update",
        arguments=arguments,
        confirmation_token=confirmation_token,
        action=lambda: adapter_tools.update_adapter(
            get_runtime().adapter,
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
            changes=changes,
            expected_header_version=expected_header_version,
        ),
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def adapter_toggle(
    tenant_num: str,
    task_code: str,
    running_service: str,
    enabled: bool,
    expected_header_version: str | int,
    confirmation_token: str | None = None,
) -> str:
    """生成启用或禁用 Adapter 计划；必须携带 expected_header_version，并经后续人工确认和一次性 confirmation_token 才执行。"""
    arguments = {
        "tenant_num": tenant_num,
        "task_code": task_code,
        "running_service": running_service,
        "enabled": enabled,
        "expected_header_version": expected_header_version,
    }
    return _invoke_write(
        tool="adapter_toggle",
        arguments=arguments,
        confirmation_token=confirmation_token,
        action=lambda: adapter_tools.toggle_adapter(
            get_runtime().adapter,
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
            enabled=enabled,
            expected_header_version=expected_header_version,
        ),
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def adapter_delete(
    tenant_num: str,
    task_code: str,
    running_service: str,
    expected_header_version: str | int,
    confirmation_token: str | None = None,
) -> str:
    """生成 Adapter 删除计划；必须携带 expected_header_version，并经后续人工确认和一次性 confirmation_token 才执行。"""
    arguments = {
        "tenant_num": tenant_num,
        "task_code": task_code,
        "running_service": running_service,
        "expected_header_version": expected_header_version,
    }
    return _invoke_write(
        tool="adapter_delete",
        arguments=arguments,
        confirmation_token=confirmation_token,
        action=lambda: adapter_tools.delete_adapter(
            get_runtime().adapter,
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
            expected_header_version=expected_header_version,
        ),
    )


@mcp.tool(annotations=REMOTE_EXECUTION)
def adapter_debug(
    tenant_num: str,
    task_code: str,
    running_service: str,
    source: str,
    raw_input: Any,
    line_id: str | int | None = None,
) -> str:
    """在 DEV 执行未保存的 Adapter 源码；不会停用、保存或启用 Adapter，返回 ok 或 error.retryable。"""
    return _invoke(
        lambda: adapter_tools.debug_adapter(
            get_runtime().adapter,
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
            source=source,
            raw_input=raw_input,
            line_id=line_id,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
)
def adapter_extract_input(
    log_text: str,
    marker: str | None = None,
    task_code: str | None = None,
) -> str:
    """从调用方提供的日志文本提取平衡 JSON；不查询日志系统，返回 ok 或 error.retryable。"""
    return _invoke(
        lambda: fixture_tools.extract_input(log_text=log_text, marker=marker, task_code=task_code)
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def adapter_deploy(
    tenant_num: str,
    task_code: str,
    running_service: str,
    source: str,
    expected_header_version: str | int,
    expected_line_version: str | int,
    line_id: str | int | None = None,
    enable: bool = False,
    confirmation_token: str | None = None,
) -> str:
    """生成带 Header/Line 版本保护的 Adapter 部署计划；仅后续人工确认并携带一次性 confirmation_token 才执行。"""
    arguments = {
        "tenant_num": tenant_num,
        "task_code": task_code,
        "running_service": running_service,
        "source": source,
        "expected_header_version": expected_header_version,
        "expected_line_version": expected_line_version,
        "line_id": line_id,
        "enable": enable,
    }
    return _invoke_write(
        tool="adapter_deploy",
        arguments=arguments,
        confirmation_token=confirmation_token,
        action=lambda: adapter_tools.deploy_adapter(
            get_runtime().adapter,
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
            source=source,
            line_id=line_id,
            expected_header_version=expected_header_version,
            expected_line_version=expected_line_version,
            enable=enable,
        ),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
