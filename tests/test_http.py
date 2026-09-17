import httpx
import pytest

from zhenyun_script_platform_mcp.client import ScriptPlatformClient
from zhenyun_script_platform_mcp.config import Settings
from zhenyun_script_platform_mcp.exceptions import (
    AuthenticationError,
    ScriptPlatformError,
    VersionConflictError,
    WriteNotAllowedError,
)


def test_http_client_sends_only_required_and_configured_headers():
    captured = {}

    def handler(request):
        captured["request"] = request
        return httpx.Response(200, json={"content": []})

    settings = Settings(
        base_url="https://gateway.dev.example.com",
        bearer_token="secret",
        cookie="SESSION=secret",
        menu_id="menu-1",
    )
    client = ScriptPlatformClient(settings, transport=httpx.MockTransport(handler))
    assert client.get("/items", params={"page": 0}) == {"content": []}
    request = captured["request"]
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["cookie"] == "SESSION=secret"
    assert request.headers["h-menu-id"] == "menu-1"
    assert len(request.headers["x-request-id"]) == 32
    assert "sec-fetch-mode" not in request.headers
    assert request.url == "https://gateway.dev.example.com/items?page=0"
    client.close()


@pytest.mark.parametrize(
    ("status", "body", "error_type"),
    [
        (401, {"message": "no"}, AuthenticationError),
        (409, {"message": "version"}, VersionConflictError),
        (500, {"message": "bad"}, ScriptPlatformError),
        (200, {"success": False, "message": "business failed"}, ScriptPlatformError),
    ],
)
def test_http_and_business_errors_are_mapped(status, body, error_type):
    client = ScriptPlatformClient(
        Settings(base_url="https://gateway.dev.example.com"),
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
    )
    with pytest.raises(error_type):
        client.get("/items")
    client.close()


def test_allowed_host_guard_blocks_misconfigured_write_target():
    settings = Settings(
        base_url="https://gateway.prod.example.com",
        allow_write=True,
        allowed_hosts=("gateway.dev.example.com",),
    )
    with pytest.raises(WriteNotAllowedError):
        settings.assert_write_allowed()
