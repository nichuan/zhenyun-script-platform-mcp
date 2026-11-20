import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from conftest import FakeAdapterClient

from zhenyun_script_platform_mcp import server
from zhenyun_script_platform_mcp.codec import encode_platform_text
from zhenyun_script_platform_mcp.confirmation import ConfirmationManager
from zhenyun_script_platform_mcp.services.adapter import AdapterService
from zhenyun_script_platform_mcp.services.batch import (
    BatchAdapter,
    BatchIndependent,
    ScriptBatchService,
)
from zhenyun_script_platform_mcp.services.independent import IndependentScriptService


class BatchClient:
    def __init__(self):
        self.adapters = {}
        self.scripts = {}
        self.writes = []
        for index in range(7):
            client = FakeAdapterClient(enabled=index % 2 == 0, line_count=2)
            code = f"ADAPTER_{index}"
            client.header.update(taskCode=code, id=50 + index)
            for line in client.header["adaptorTaskLines"]:
                line.update(taskCode=code, headerId=50 + index)
            self.adapters[code] = client
        for index in range(3):
            code = f"SCRIPT_{index}"
            self.scripts[code] = {
                "id": index,
                "code": code,
                "tenantNum": "SRM-DEMO",
                "objectVersionNumber": 1,
                "content": encode_platform_text("return 0;"),
            }

    def get(self, path, *, params=None):
        code = params["taskCode"]
        if path == AdapterService.TOGGLE_ENDPOINT:
            self.writes.append((code, params["enabledFlag"]))
        return self.adapters[code].get(path, params=params)

    def post(self, path, *, json=None, params=None):
        if path == IndependentScriptService.PAGE_ENDPOINT:
            return {"content": [deepcopy(self.scripts[json["code"]])]}
        code = json["taskCode"]
        self.writes.append((code, "save"))
        return self.adapters[code].post(path, json=json, params=params)

    def put(self, path, *, json=None):
        code = json["code"]
        self.writes.append((code, "save"))
        self.scripts[code] = {**json, "objectVersionNumber": json["objectVersionNumber"] + 1}


@pytest.fixture
def batch_platform(write_settings, monkeypatch):
    client = BatchClient()
    batch = ScriptBatchService(
        AdapterService(client, write_settings),
        IndependentScriptService(client, write_settings),
        ConfirmationManager(),
        base_url=write_settings.base_url,
        ttl_seconds=600,
    )
    monkeypatch.setattr(server, "_runtime", SimpleNamespace(batch=batch))
    return client


def prepare(adapter_indexes=(0,), script_indexes=(), *, description_only=False):
    return json.loads(
        server.script_batch_deploy(
            tenant_num="SRM-DEMO",
            requirement_code="CRO-5974",
            adapters=[
                BatchAdapter(
                    task_code=f"ADAPTER_{index}",
                    running_service="srm-source",
                    line_id=None if description_only else 102,
                    source=None if description_only else f"return {index + 10};",
                )
                for index in adapter_indexes
            ],
            independent_scripts=[
                BatchIndependent(code=f"SCRIPT_{index}", source=f"return {index + 1};")
                for index in script_indexes
            ],
        )
    )


def execute(plan):
    started = json.loads(server.script_batch_deploy(confirmation_token=plan["confirmation_token"]))
    assert started["ok"], started
    for _ in range(100):
        result = json.loads(server.script_batch_status(started["batch_id"]))
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    pytest.fail("Batch did not reach a terminal state")


def test_one_confirmation_deploys_seven_adapters_and_three_independents(batch_platform):
    client = batch_platform
    plan = prepare(range(7), range(3))
    assert plan["requires_confirmation"]
    assert len(plan["preview"]["items"]) == 10
    assert plan["preview"]["items"][3]["new_description"] == "test adapter;CRO-5974"
    assert client.writes == []
    rejected = json.loads(
        server.script_batch_deploy(
            tenant_num="SRM-OTHER", confirmation_token=plan["confirmation_token"]
        )
    )
    assert rejected["error"]["code"] == "INVALID_ARGUMENT"
    assert client.writes == []

    result = execute(plan)
    assert result["status"] == "completed"
    assert [item["status"] for item in result["items"]] == ["verified"] * 10
    assert client.writes[:3] == [(f"SCRIPT_{index}", "save") for index in range(3)]
    assert client.writes[3:8] == [
        ("ADAPTER_0", "false"),
        ("ADAPTER_0", "save"),
        ("ADAPTER_0", "true"),
        ("ADAPTER_1", "save"),
        ("ADAPTER_2", "false"),
    ]
    assert len(client.writes) == 18
    for index, adapter in enumerate(client.adapters.values()):
        assert adapter.header["enabledFlag"] == (index % 2 == 0)
        assert adapter.header["description"] == "test adapter;CRO-5974"
        assert adapter.header["adaptorTaskLines"][0]["scriptContent"] == encode_platform_text(
            "return line1;"
        )
    replay = json.loads(server.script_batch_deploy(confirmation_token=plan["confirmation_token"]))
    assert replay["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert len(client.writes) == 18


def test_description_only_is_disabled_first_and_requirement_is_not_duplicated(batch_platform):
    client = batch_platform
    result = execute(prepare(description_only=True))
    assert result["status"] == "completed"
    assert client.writes == [
        ("ADAPTER_0", "false"),
        ("ADAPTER_0", "save"),
        ("ADAPTER_0", "true"),
    ]
    client.adapters["ADAPTER_0"].header["description"] = "test adapter;cro-5974"
    no_op = prepare(description_only=True)
    assert not no_op["requires_confirmation"]
    assert no_op["status"] == "unchanged"
    assert len(client.writes) == 3


def test_conflict_stops_batch_without_touching_adapter_or_remainder(batch_platform):
    plan = prepare((0, 1))
    batch_platform.adapters["ADAPTER_0"].header["objectVersionNumber"] += 1

    result = execute(plan)
    assert result["status"] == "failed"
    assert result["items"][0]["error"]["code"] == "VERSION_CONFLICT"
    assert result["items"][1]["status"] == "not_run"
    assert batch_platform.writes == []


def test_unverified_save_leaves_adapter_disabled_and_stops_remainder(batch_platform):
    batch_platform.adapters["ADAPTER_0"].ignore_save = True

    result = execute(prepare((0, 1)))
    failed, not_run = result["items"]
    assert result["status"] == "failed"
    assert failed["error"]["code"] == "SAVE_VERIFICATION_FAILED"
    assert failed["requires_manual_attention"]
    assert failed["last_observed_enabled"] is False
    assert not_run["status"] == "not_run"
    assert batch_platform.writes == [("ADAPTER_0", "false"), ("ADAPTER_0", "save")]
