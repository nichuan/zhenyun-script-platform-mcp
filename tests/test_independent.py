from copy import deepcopy

import pytest

from zhenyun_script_platform_mcp.codec import decode_platform_text, encode_platform_text
from zhenyun_script_platform_mcp.config import Settings
from zhenyun_script_platform_mcp.exceptions import VersionConflictError, WriteNotAllowedError
from zhenyun_script_platform_mcp.models import FixtureStatus
from zhenyun_script_platform_mcp.services.independent import IndependentScriptService


class IndependentClient:
    def __init__(self):
        self.record = {
            "id": 7,
            "code": "AFTER_API",
            "tenantNum": "SRM-DEMO",
            "tenantId": 1,
            "description": "after hook",
            "quickType": "API_POST",
            "objectVersionNumber": 10,
            "content": encode_platform_text("return input;"),
            "contentInput": encode_platform_text('{"body":"{}"}'),
            "_token": "row-token-must-be-preserved",
        }
        self.put_payload = None

    def post(self, path, *, json=None, params=None):
        assert path.endswith("/page")
        return {"content": [deepcopy(self.record)], "totalElements": 1}

    def put(self, path, *, json=None, params=None):
        self.put_payload = deepcopy(json)
        self.record = deepcopy(json)
        self.record["objectVersionNumber"] += 1
        return {"success": True}


def settings(*, write=True):
    return Settings(
        base_url="https://gateway.dev.example.com",
        allow_write=write,
        allowed_hosts=("gateway.dev.example.com",),
    )


def test_independent_get_decodes_source_and_fixture():
    result = IndependentScriptService(IndependentClient(), settings()).get(
        tenant_num="SRM-DEMO", code="AFTER_API"
    )
    assert result.source == "return input;"
    assert result.saved_test_input == {"body": "{}"}
    assert result.test_input_status == FixtureStatus.AVAILABLE
    assert len(result.source_hash) == 64


def test_independent_save_uses_full_latest_record_and_verifies():
    client = IndependentClient()
    result = IndependentScriptService(client, settings()).save(
        tenant_num="SRM-DEMO",
        code="AFTER_API",
        source="return {...input, changed: true};",
        expected_version=10,
    )
    assert result["saved"] is True
    assert result["verified"] is True
    assert result["old_version"] == 10
    assert result["new_version"] == 11
    assert client.put_payload["_token"] == "row-token-must-be-preserved"
    assert decode_platform_text(client.put_payload["content"]).endswith("true};")


def test_independent_version_conflict_prevents_put():
    client = IndependentClient()
    with pytest.raises(VersionConflictError):
        IndependentScriptService(client, settings()).save(
            tenant_num="SRM-DEMO",
            code="AFTER_API",
            source="new",
            expected_version=9,
        )
    assert client.put_payload is None


def test_independent_write_guard_defaults_to_denied():
    client = IndependentClient()
    with pytest.raises(WriteNotAllowedError):
        IndependentScriptService(client, settings(write=False)).save(
            tenant_num="SRM-DEMO", code="AFTER_API", source="new"
        )
    assert client.put_payload is None
