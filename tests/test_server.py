import json

from zhenyun_script_platform_mcp.exceptions import VersionConflictError
from zhenyun_script_platform_mcp.server import _invoke, mcp


def test_server_exposes_exactly_the_seven_workflow_tools():
    assert set(mcp._tool_manager._tools) == {
        "independent_script_get",
        "independent_script_debug",
        "independent_script_save",
        "adapter_get",
        "adapter_debug",
        "adapter_extract_input",
        "adapter_deploy",
    }


def test_tool_annotations_distinguish_reads_remote_execution_and_writes():
    tools = mcp._tool_manager._tools
    assert tools["adapter_get"].annotations.readOnlyHint is True
    assert tools["adapter_extract_input"].annotations.openWorldHint is False
    assert tools["adapter_debug"].annotations.readOnlyHint is False
    assert tools["adapter_debug"].annotations.destructiveHint is False
    assert tools["adapter_deploy"].annotations.destructiveHint is True
    assert tools["independent_script_save"].annotations.destructiveHint is True


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


def test_returned_partial_failure_is_not_marked_ok():
    result = json.loads(_invoke(lambda: {"saved": False, "error": {"code": "SAVE_FAILED"}}))
    assert result["ok"] is False
