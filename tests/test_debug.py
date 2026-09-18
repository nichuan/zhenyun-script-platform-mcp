import json

import pytest

from zhenyun_script_platform_mcp.codec import decode_platform_text
from zhenyun_script_platform_mcp.config import Settings
from zhenyun_script_platform_mcp.exceptions import (
    InvalidFixtureError,
    NoValidFixtureError,
)
from zhenyun_script_platform_mcp.services.debug import DebugService, parse_debug_response


class DebugClient:
    def __init__(self, response):
        self.response = response
        self.call = None

    def post(self, path, *, params=None, json=None):
        self.call = (path, params, json)
        return self.response


def test_debug_nested_json_result_and_body_are_parsed():
    response = {
        "result": json.dumps({"body": json.dumps({"ok": True}), "status": 200}),
        "outPutLog": "ran",
        "executionInfo": {"time": 1},
        "queryBlockSql": ["select 1"],
    }
    result = parse_debug_response(response)
    assert result.result == {"body": {"ok": True}, "status": 200}
    assert result.logs == "ran"
    assert result.execution_info == {"time": 1}


def test_debug_non_json_result_is_preserved():
    result = parse_debug_response({"result": "plain value", "outPutLog": None})
    assert result.result == "plain value"


def test_debug_run_encodes_current_source_and_adds_script_version():
    client = DebugClient({"result": "{}", "outPutLog": "ok"})
    service = DebugService(client)
    result = service.run(
        tenant_num="SRM-DEMO",
        source="function process(input) { return input; }",
        raw_input={"中文": 1},
        script_version=3,
    )
    path, params, body = client.call
    assert path == "/sada/v1/script-debug/run"
    assert params == {"debugTenantNum": "SRM-DEMO", "scriptVersion": 3}
    assert decode_platform_text(body["script"]).startswith("function process")
    assert json.loads(body["rawInputJsonStr"]) == {"中文": 1}
    assert result.success is True


def test_debug_rejects_invalid_json_string_input():
    with pytest.raises(InvalidFixtureError):
        DebugService(DebugClient({})).run(tenant_num="T", source="x", raw_input="not-json")


def test_debug_rejects_missing_fixture():
    with pytest.raises(NoValidFixtureError) as error:
        DebugService(DebugClient({})).run(tenant_num="T", source="x", raw_input=None)
    assert error.value.code == "NO_VALID_FIXTURE"


def test_debug_requires_nonempty_tenant():
    client = DebugClient({})
    restricted = Settings(base_url="https://gateway.dev.example.com")

    with pytest.raises(ValueError, match="non-empty tenant"):
        DebugService(client, restricted).run(tenant_num="", source="x", raw_input={})

    assert client.call is None
