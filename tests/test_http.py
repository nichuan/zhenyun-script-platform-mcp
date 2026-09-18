import httpx
import pytest

from zhenyun_script_platform_mcp.client import ScriptPlatformClient
from zhenyun_script_platform_mcp.config import Settings
from zhenyun_script_platform_mcp.exceptions import (
    AuthenticationError,
    ScriptPlatformError,
    VersionConflictError,
)


class FakeAuth:
    def __init__(self):
        self.token = "old"
        self.refreshes = 0

    def get_authorization(self):
        return f"Bearer {self.token}", self.token

    def refresh_after_rejection(self, rejected_token=None):
        assert rejected_token == "old"
        self.token = "new"
        self.refreshes += 1

    def metadata(self):
        return {"configured": True}

    def close(self):
        pass


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
        auth_provider=FakeAuth(),
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
    )
    with pytest.raises(error_type):
        client.get("/items")
    client.close()


def test_401_refreshes_once_and_retries_with_new_token():
    seen = []
    auth = FakeAuth()

    def handler(request):
        seen.append(request.headers["authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json={"ok": True})

    client = ScriptPlatformClient(
        Settings(base_url="https://gateway.dev.example.com"),
        auth_provider=auth,
        transport=httpx.MockTransport(handler),
    )
    assert client.get("/status") == {"ok": True}
    assert seen == ["Bearer old", "Bearer new"]
    assert auth.refreshes == 1
    client.close()


def test_raw_authorization_scheme_is_supported_for_gateway_tokens():
    captured = {}

    def handler(request):
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"ok": True})

    client = ScriptPlatformClient(
        Settings(
            base_url="https://gateway.dev.example.com",
            bearer_token="raw-token",
            authorization_scheme="raw",
        ),
        transport=httpx.MockTransport(handler),
    )
    client.get("/status")
    assert captured["authorization"] == "raw-token"
    client.close()


def test_delete_accepts_204_empty_body():
    client = ScriptPlatformClient(
        Settings(base_url="https://gateway.dev.example.com", bearer_token="test-token"),
        transport=httpx.MockTransport(lambda _: httpx.Response(204)),
    )
    assert client.delete("/record", json={"id": 1}) is None
    client.close()


def test_http_200_json_and_xml_business_failures_are_rejected():
    json_client = ScriptPlatformClient(
        Settings(base_url="https://gateway.dev.example.com", bearer_token="test-token"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "code": "BAD",
                    "message": "failed",
                    "traceId": "trace",
                    "type": "error",
                },
            )
        ),
    )
    with pytest.raises(ScriptPlatformError):
        json_client.get("/action")
    json_client.close()

    xml_client = ScriptPlatformClient(
        Settings(base_url="https://gateway.dev.example.com"),
        auth_provider=FakeAuth(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                text="<oauth><status>ACCESS_TOKEN_EXPIRED</status><code>401</code></oauth>",
            )
        ),
    )
    with pytest.raises(AuthenticationError):
        xml_client.get("/action")
    xml_client.close()
