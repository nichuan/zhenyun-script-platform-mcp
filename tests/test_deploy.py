import pytest
from conftest import FakeAdapterClient

from zhenyun_script_platform_mcp.exceptions import (
    AdapterStateError,
    SaveVerificationError,
    VersionConflictError,
)
from zhenyun_script_platform_mcp.services.adapter import AdapterService


def deploy(service, **overrides):
    values = {
        "tenant_num": "SRM-DEMO",
        "task_code": "TASK",
        "running_service": "srm-source",
        "source": "return changed;",
    }
    values.update(overrides)
    return service.deploy(**values)


def test_deploy_originally_disabled_stays_disabled(write_settings):
    client = FakeAdapterClient(enabled=False)
    result = deploy(AdapterService(client, write_settings))
    assert client.events == ["GET", "SAVE", "GET"]
    assert result["saved"] is True
    assert result["verified"] is True
    assert result["original_enabled"] is False
    assert result["final_enabled"] is False


def test_deploy_originally_enabled_has_exact_safe_call_order(write_settings):
    client = FakeAdapterClient(enabled=True)
    result = deploy(AdapterService(client, write_settings))
    assert client.events == [
        "GET",
        "TOGGLE false",
        "GET",
        "SAVE",
        "GET",
        "TOGGLE true",
        "GET",
    ]
    assert result["saved"] is True
    assert result["verified"] is True
    assert result["final_enabled"] is True
    assert result["old_hash"] != result["new_hash"]


def test_disable_succeeds_save_fails_and_enabled_state_is_restored(write_settings):
    client = FakeAdapterClient(enabled=True)
    client.save_fails = True
    result = deploy(AdapterService(client, write_settings))
    assert client.events == [
        "GET",
        "TOGGLE false",
        "GET",
        "SAVE",
        "TOGGLE true",
        "GET",
    ]
    assert result["saved"] is False
    assert result["enabled_restored"] is True
    assert result["requires_manual_attention"] is False


def test_save_succeeds_enable_fails_requires_manual_attention(write_settings):
    client = FakeAdapterClient(enabled=True)
    client.enable_fails = True
    result = deploy(AdapterService(client, write_settings))
    assert result["saved"] is True
    assert result["verified"] is True
    assert result["final_enabled"] is False
    assert result["error"]["code"] == "RE_ENABLE_FAILED"
    assert result["requires_manual_attention"] is True


def test_save_verification_failure_keeps_adapter_disabled(write_settings):
    client = FakeAdapterClient(enabled=True)
    client.ignore_save = True
    with pytest.raises(SaveVerificationError) as error:
        deploy(AdapterService(client, write_settings))
    assert client.events == ["GET", "TOGGLE false", "GET", "SAVE", "GET"]
    assert client.header["enabledFlag"] is False
    assert error.value.details["requires_manual_attention"] is True


def test_preparation_failure_after_disable_restores_original_state(write_settings):
    class FailFreshGetOnce(FakeAdapterClient):
        failed = False

        def get(self, path, *, params=None):
            if (
                not path.endswith("/toggle-cache")
                and self.header["enabledFlag"] is False
                and not self.failed
            ):
                self.events.append("GET")
                self.failed = True
                raise RuntimeError("fresh get failed")
            return super().get(path, params=params)

    client = FailFreshGetOnce(enabled=True)
    with pytest.raises(AdapterStateError) as error:
        deploy(AdapterService(client, write_settings))
    assert client.events == ["GET", "TOGGLE false", "GET", "TOGGLE true", "GET"]
    assert error.value.details["enabled_restored"] is True
    assert client.header["enabledFlag"] is True


@pytest.mark.parametrize(
    ("kwargs", "detail_key"),
    [
        ({"expected_header_version": 14}, "actual_header_version"),
        ({"expected_line_version": 19}, "actual_line_version"),
    ],
)
def test_deploy_version_conflicts_happen_before_state_change(write_settings, kwargs, detail_key):
    client = FakeAdapterClient(enabled=True)
    with pytest.raises(VersionConflictError) as error:
        deploy(AdapterService(client, write_settings), **kwargs)
    assert detail_key in error.value.details
    assert client.events == ["GET"]
