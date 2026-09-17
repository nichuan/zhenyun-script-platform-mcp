"""FastMCP stdio entrypoint exposing exactly the seven supported workflows."""

from __future__ import annotations

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
from .exceptions import ScriptPlatformError
from .sanitizer import sanitize, sanitize_text
from .services import AdapterService, DebugService, IndependentScriptService
from .tools import adapter as adapter_tools
from .tools import fixture as fixture_tools
from .tools import independent as independent_tools

FastMCPSettings.model_rebuild()
mcp = FastMCP(
    "zhenyun-script-platform-mcp",
    instructions=(
        "This server is the authoritative source for current Script Platform source, version, "
        "fixtures, and adapter state. Pangu script tools may discover an identity but must not "
        "replace get calls from this server before debug, save, or deploy. Develop scripts "
        "against the remote DEV GraalJS runtime. Debug calls never save. "
        "Only call independent_script_save or adapter_deploy after the user explicitly asks "
        "to persist, deploy, publish, or update DEV."
    ),
)


@dataclass(slots=True)
class Runtime:
    settings: Settings
    client: ScriptPlatformClient
    independent: IndependentScriptService
    debug: DebugService
    adapter: AdapterService


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        settings = Settings.from_env()
        client = ScriptPlatformClient(settings)
        debug = DebugService(client)
        _runtime = Runtime(
            settings=settings,
            client=client,
            independent=IndependentScriptService(client, settings),
            debug=debug,
            adapter=AdapterService(client, settings, debug),
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
    except Exception as exc:  # transport boundary: never leak a traceback to the agent
        logging.getLogger(__name__).exception("Unhandled Script Platform tool failure")
        return _serialize(
            {
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": f"Unexpected {type(exc).__name__}",
                    "retryable": False,
                },
            }
        )


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
def independent_script_get(tenant_num: str, code: str) -> str:
    """Authoritatively read the current Independent Script, version, hash, and fixture.

    Use this after any discovery search and before editing, debugging, or saving. This is
    read-only and supersedes legacy Pangu database source readers for the current platform state.
    """
    return _invoke(
        lambda: independent_tools.get_script(
            get_runtime().independent, tenant_num=tenant_num, code=code
        )
    )


@mcp.tool(annotations=REMOTE_EXECUTION)
def independent_script_debug(tenant_num: str, source: str, raw_input: Any) -> str:
    """Execute unsaved Independent Script source in DEV. Does not persist any change."""
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
    expected_version: str | int | None = None,
) -> str:
    """Persist an Independent Script to DEV after explicit user approval. This changes state."""
    return _invoke(
        lambda: independent_tools.save_script(
            get_runtime().independent,
            tenant_num=tenant_num,
            code=code,
            source=source,
            expected_version=expected_version,
        )
    )


@mcp.tool(annotations=READ_ONLY)
def adapter_get(tenant_num: str, task_code: str, running_service: str) -> str:
    """Authoritatively read the current Adapter header, state, versions, and every decoded line.

    Use this after any discovery search and before editing, debugging, or deploying. This is
    read-only and supersedes legacy Pangu database source readers for the current platform state.
    """
    return _invoke(
        lambda: adapter_tools.get_adapter(
            get_runtime().adapter,
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
        )
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
    """Execute unsaved Adapter source in DEV; never disables, saves, or enables an Adapter."""
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
    """Extract balanced JSON from supplied log text. This tool never queries a log system."""
    return _invoke(
        lambda: fixture_tools.extract_input(log_text=log_text, marker=marker, task_code=task_code)
    )


@mcp.tool(annotations=PLATFORM_WRITE)
def adapter_deploy(
    tenant_num: str,
    task_code: str,
    running_service: str,
    source: str,
    line_id: str | int | None = None,
    expected_header_version: str | int | None = None,
    expected_line_version: str | int | None = None,
) -> str:
    """Persist tested Adapter source to DEV after explicit user approval.

    This is state-changing. It may temporarily disable an enabled Adapter, reload its latest
    full payload, save and verify one line, and then restore the original enabled state.
    """
    return _invoke(
        lambda: adapter_tools.deploy_adapter(
            get_runtime().adapter,
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
            source=source,
            line_id=line_id,
            expected_header_version=expected_header_version,
            expected_line_version=expected_line_version,
        )
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
