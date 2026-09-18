import json
from types import SimpleNamespace

from zhenyun_script_platform_mcp.confirmation import ConfirmationManager
from zhenyun_script_platform_mcp.exceptions import VersionConflictError
from zhenyun_script_platform_mcp.server import _invoke, _invoke_write, _write_preview, mcp


def test_server_exposes_lifecycle_and_verified_platform_tools():
    assert set(mcp._tool_manager._tools) == {
        "independent_script_create",
        "independent_script_get",
        "independent_script_debug",
        "independent_script_save",
        "adapter_get",
        "adapter_debug",
        "adapter_extract_input",
        "adapter_deploy",
        "adapter_create",
        "adapter_update",
        "adapter_toggle",
        "adapter_delete",
        "platform_context_get",
        "platform_capabilities_list",
        "platform_resource_search",
        "platform_resource_get",
        "platform_definition_get",
        "platform_relations_get",
        "platform_api_point_list",
        "platform_resource_create",
        "platform_resource_save",
        "platform_resource_delete",
        "platform_table_action",
    }


def test_tool_annotations_distinguish_reads_remote_execution_and_writes():
    tools = mcp._tool_manager._tools
    assert tools["adapter_get"].annotations.readOnlyHint is True
    assert tools["adapter_extract_input"].annotations.openWorldHint is False
    assert tools["adapter_debug"].annotations.readOnlyHint is False
    assert tools["adapter_debug"].annotations.destructiveHint is False
    assert tools["adapter_deploy"].annotations.destructiveHint is True
    assert tools["independent_script_create"].annotations.destructiveHint is True
    assert tools["independent_script_save"].annotations.destructiveHint is True
    assert tools["platform_resource_get"].annotations.readOnlyHint is True
    assert tools["platform_resource_save"].annotations.destructiveHint is True
    assert tools["platform_table_action"].annotations.destructiveHint is True
    assert {"permission", "module"} <= set(
        tools["independent_script_create"].parameters["required"]
    )


def test_every_write_tool_requires_the_two_phase_confirmation_parameter():
    tools = mcp._tool_manager._tools
    writes = {
        "independent_script_create",
        "independent_script_save",
        "adapter_deploy",
        "adapter_create",
        "adapter_update",
        "adapter_toggle",
        "adapter_delete",
        "platform_resource_create",
        "platform_resource_save",
        "platform_resource_delete",
        "platform_table_action",
    }
    for name in writes:
        assert "confirmation_token" in tools[name].parameters["properties"]
    assert "expected_version" in tools["independent_script_save"].parameters["required"]
    assert "expected_header_version" in tools["adapter_deploy"].parameters["required"]
    assert "expected_line_version" in tools["adapter_deploy"].parameters["required"]


def test_tool_boundary_returns_stable_sanitized_error_without_traceback():
    def fail():
        raise VersionConflictError(
            "Authorization: Bearer top-secret",
            details={"_token": "row-secret", "actual_version": 2},
        )

    result = json.loads(_invoke(fail))
    assert result["ok"] is False
    assert result["error"]["code"] == "VERSION_CONFLICT"
    assert "top-secret" not in result["error"]["message"]
    assert result["error"]["details"]["_token"] == "<REDACTED>"


def test_unexpected_error_keeps_a_sanitized_diagnostic():
    def fail():
        raise RuntimeError("bad config path")

    result = json.loads(_invoke(fail))

    assert result["ok"] is False
    assert result["error"]["code"] == "INTERNAL_ERROR"
    assert result["error"]["message"] == "Unexpected RuntimeError: bad config path"


def test_write_preview_warns_about_script_companion_resources():
    preview = _write_preview(
        {
            "resource_type": "independent_script",
            "record": {"code": "SAMPLE", "quickType": "api"},
        }
    )
    assert preview["warnings"] == [
        (
            "Platform may create or link a related api_publish record for quickType='api'; "
            "check platform_relations_get before deleting the script."
        )
    ]


def test_returned_partial_failure_is_not_marked_ok():
    result = json.loads(_invoke(lambda: {"saved": False, "error": {"code": "SAVE_FAILED"}}))
    assert result["ok"] is False


def test_first_write_call_only_returns_plan_and_does_not_run_action(monkeypatch):
    manager = ConfirmationManager(ttl_seconds=60, secret=b"z" * 32, now=lambda: 100)
    monkeypatch.setattr(
        "zhenyun_script_platform_mcp.server.get_runtime",
        lambda: SimpleNamespace(confirmation=manager),
    )
    calls = []
    arguments = {"tenant": "SRM-DEMO", "code": "A", "source": "return 1;"}
    prepared = json.loads(
        _invoke_write(
            tool="save",
            arguments=arguments,
            confirmation_token=None,
            action=lambda: calls.append("write"),
        )
    )
    assert prepared["requires_confirmation"] is True
    assert calls == []

    completed = json.loads(
        _invoke_write(
            tool="save",
            arguments=arguments,
            confirmation_token=prepared["confirmation_token"],
            action=lambda: {"saved": True},
        )
    )
    assert completed["saved"] is True
    assert completed["human_confirmation"] == "verified"
