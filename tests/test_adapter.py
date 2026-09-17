import json

import pytest
from conftest import FakeAdapterClient

from zhenyun_script_platform_mcp.codec import decode_platform_text
from zhenyun_script_platform_mcp.exceptions import AmbiguousLineError
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
