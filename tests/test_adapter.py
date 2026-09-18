import json

import pytest
from conftest import FakeAdapterClient

from zhenyun_script_platform_mcp.codec import decode_platform_text
from zhenyun_script_platform_mcp.config import Settings
from zhenyun_script_platform_mcp.exceptions import (
    AdapterStateError,
    AmbiguousLineError,
)
from zhenyun_script_platform_mcp.models import FixtureStatus
from zhenyun_script_platform_mcp.services.adapter import AdapterService


def test_adapter_get_single_line_decodes_every_field(adapter_client, write_settings):
    adapter = AdapterService(adapter_client, write_settings).get(
        tenant_num="SRM-DEMO", task_code="TASK", running_service="srm-source"
    )
    assert adapter.script_version == 3
    assert adapter.enabled is True
    assert len(adapter.lines) == 1
    assert adapter.lines[0].source == "return line1;"
    assert adapter.lines[0].test_input_status == FixtureStatus.PLACEHOLDER


def test_adapter_get_requires_nonempty_tenant(adapter_client):
    restricted = Settings(base_url="https://gateway.dev.example.com")

    with pytest.raises(ValueError, match="non-empty tenant"):
        AdapterService(adapter_client, restricted).get(
            tenant_num="", task_code="TASK", running_service="srm-source"
        )

    assert adapter_client.events == []


def test_adapter_get_preserves_multiple_lines(write_settings):
    adapter = AdapterService(FakeAdapterClient(line_count=2), write_settings).get(
        tenant_num="SRM-DEMO", task_code="TASK", running_service="srm-source"
    )
    assert [line.id for line in adapter.lines] == [101, 102]
    assert [line.priority for line in adapter.lines] == [1, 2]


def test_adapter_debug_uses_script_version_without_state_change(adapter_client, write_settings):
    service = AdapterService(adapter_client, write_settings)
    result = service.debug(
        tenant_num="SRM-DEMO",
        task_code="TASK",
        running_service="srm-source",
        source="return changed;",
        raw_input={"x": 1},
    )
    params, body = adapter_client.last_debug
    assert adapter_client.events == ["GET", "DEBUG"]
    assert params == {"debugTenantNum": "SRM-DEMO", "scriptVersion": 3}
    assert decode_platform_text(body["script"]) == "return changed;"
    assert json.loads(body["rawInputJsonStr"]) == {"x": 1}
    assert result.result == {"ok": True}


def test_multi_line_debug_requires_line_id(write_settings):
    client = FakeAdapterClient(line_count=2)
    with pytest.raises(AmbiguousLineError) as error:
        AdapterService(client, write_settings).debug(
            tenant_num="SRM-DEMO",
            task_code="TASK",
            running_service="srm-source",
            source="return changed;",
            raw_input={},
        )
    assert error.value.details["line_ids"] == [101, 102]
    assert client.events == ["GET"]


def test_multi_line_debug_accepts_explicit_line(write_settings):
    client = FakeAdapterClient(line_count=2)
    AdapterService(client, write_settings).debug(
        tenant_num="SRM-DEMO",
        task_code="TASK",
        running_service="srm-source",
        source="return changed;",
        raw_input={},
        line_id=102,
    )
    assert client.events == ["GET", "DEBUG"]


def test_adapter_toggle_requires_version_and_verifies_state(adapter_client, write_settings):
    service = AdapterService(adapter_client, write_settings)
    result = service.toggle(
        tenant_num="SRM-DEMO",
        task_code="TASK",
        running_service="srm-source",
        enabled=False,
        expected_header_version=15,
    )
    assert result["changed"] is True
    assert result["enabled"] is False
    assert adapter_client.events == ["GET", "TOGGLE false", "GET"]


def test_adapter_metadata_update_preserves_lines(adapter_client, write_settings):
    service = AdapterService(adapter_client, write_settings)
    result = service.update(
        tenant_num="SRM-DEMO",
        task_code="TASK",
        running_service="srm-source",
        changes={"description": "changed"},
        expected_header_version=15,
    )
    assert result["verified"] is True
    assert adapter_client.header["description"] == "changed"
    assert adapter_client.header["adaptorTaskLines"][0]["_status"] == "update"


def test_adapter_create_builds_required_initial_line(write_settings):
    client = FakeAdapterClient()
    client.present = False
    result = AdapterService(client, write_settings).create(
        tenant_num="SRM-DEMO",
        task_code="NEW_TASK",
        running_service="srm-source",
        description="new",
    )
    assert result["created"] is True
    assert client.header["_status"] == "create"
    assert client.header["adaptorTaskLines"][0]["_status"] == "create"
    assert result["enabled"] is False


def test_adapter_delete_refuses_enabled_then_verifies_disabled_delete(
    adapter_client, write_settings
):
    service = AdapterService(adapter_client, write_settings)
    with pytest.raises(AdapterStateError, match="enabled Adapter"):
        service.delete(
            tenant_num="SRM-DEMO",
            task_code="TASK",
            running_service="srm-source",
            expected_header_version=15,
        )
    adapter_client.header["enabledFlag"] = False
    result = service.delete(
        tenant_num="SRM-DEMO",
        task_code="TASK",
        running_service="srm-source",
        expected_header_version=15,
    )
    assert result["deleted"] is True
    assert result["verified"] is True
