from copy import deepcopy

import pytest

from zhenyun_script_platform_mcp.config import Settings
from zhenyun_script_platform_mcp.exceptions import VersionConflictError
from zhenyun_script_platform_mcp.services.platform import PlatformResourceService


class PlatformClient:
    def __init__(self, record=None):
        self.record = deepcopy(record)
        self.events = []

    def get(self, path, *, params=None):
        self.events.append(("GET", path, deepcopy(params), None))
        if "rel-table-definitions" in path:
            return {
                "tableCode": "sada_adaptor_constants",
                "mappingInfo": {
                    "slot2DefinitionMap": {
                        "value1": {
                            "name": "constantCode",
                            "_tls": {"label": "常量编码"},
                            "type": "string",
                            "required": True,
                        }
                    }
                },
                "actionInfos": [{"id": 231, "name": "常量说明"}],
            }
        if "api/point/list" in path:
            return {
                "content": [
                    {
                        "apiCode": "PO_HEADER_SAVE",
                        "serverName": "srm-po",
                        "classBeanName": "poController",
                        "methodName": "save",
                    }
                ],
                "totalElements": 1,
            }
        return {"content": [deepcopy(self.record)] if self.record else [], "totalElements": 1}

    def post(self, path, *, params=None, json=None):
        self.events.append(("POST", path, deepcopy(params), deepcopy(json)))
        if path.endswith("/page") or "script-log-records/query" in path:
            content = [deepcopy(self.record)] if self.record else []
            code = (json or {}).get("constantCode")
            if code and content and content[0].get("constantCode") != code:
                content = []
            return {
                "content": content,
                "totalElements": len(content),
                "totalPages": 1,
                "number": 0,
                "size": (json or {}).get("size", 20),
            }
        if "rel-table-actions/execute" in path:
            return {"action": "done"}
        self.record = deepcopy(json)
        self.record["id"] = 99
        self.record["objectVersionNumber"] = 1
        return {"success": True}

    def put(self, path, *, params=None, json=None):
        self.events.append(("PUT", path, deepcopy(params), deepcopy(json)))
        self.record = deepcopy(json)
        self.record["objectVersionNumber"] += 1
        return {"success": True}

    def delete(self, path, *, params=None, json=None):
        self.events.append(("DELETE", path, deepcopy(params), deepcopy(json)))
        self.record = None


def settings():
    return Settings(
        base_url="https://gateway.dev.example.com",
        text_preview_chars=8,
    )


def constant_record():
    return {
        "id": 7,
        "constantCode": "SECRET_KEY",
        "description": "demo",
        "value": "must-not-leak",
        "tenantNum": "SRM-DEMO",
        "objectVersionNumber": 3,
        "_token": "row-token",
    }


def test_search_masks_secret_fields_and_reports_page():
    service = PlatformResourceService(PlatformClient(constant_record()), settings())
    result = service.search(resource_type="constant", tenant="SRM-DEMO", code="SECRET_KEY")
    assert result["page"]["returned"] == 1
    assert result["records"][0]["value"] == "<REDACTED>"
    assert result["records"][0]["_token"] == "<REDACTED>"


def test_definition_is_locally_parsed_without_mapping_blob():
    service = PlatformResourceService(PlatformClient(), settings())
    result = service.definition(resource_type="constant")
    assert result["fields"][0]["name"] == "constantCode"
    assert result["fields"][0]["label"] == "常量编码"
    assert result["mapping_json_returned"] is False


def test_definition_rejects_resource_without_definition_contract():
    client = PlatformClient()
    service = PlatformResourceService(client, settings())

    with pytest.raises(ValueError, match="not available"):
        service.definition(resource_type="script_log")

    assert client.events == []


def test_api_point_list_preserves_verified_field_mapping():
    service = PlatformResourceService(PlatformClient(), settings())
    result = service.api_points(api_code="PO_HEADER_SAVE")
    assert result["returned"] == 1
    assert result["field_mapping"] == {"classBeanName": "api_rewrite.beanName"}


def test_generic_save_requires_matching_version_and_verifies_fields():
    client = PlatformClient(constant_record())
    service = PlatformResourceService(client, settings())
    with pytest.raises(VersionConflictError):
        service.save(
            resource_type="constant",
            tenant="SRM-DEMO",
            code="SECRET_KEY",
            changes={"description": "changed"},
            expected_version=2,
        )
    result = service.save(
        resource_type="constant",
        tenant="SRM-DEMO",
        code="SECRET_KEY",
        changes={"description": "changed"},
        expected_version=3,
    )
    assert result["verified"] is True
    assert result["new_version"] == 4
    put = next(event for event in client.events if event[0] == "PUT")
    assert put[3]["value"] == "must-not-leak"
    assert put[3]["updateScenario"] == "update"


def test_generic_create_delete_and_table_action_use_verified_wire_shapes():
    client = PlatformClient()
    service = PlatformResourceService(client, settings())
    created = service.create(
        resource_type="constant",
        tenant="SRM-DEMO",
        record={"constantCode": "NEW_CODE", "description": "demo"},
    )
    assert created["verified"] is True
    post = next(
        event for event in client.events if event[0] == "POST" and not event[1].endswith("/page")
    )
    assert post[3]["updateScenario"] == "new"

    action = service.execute_action(
        resource_type="constant",
        tenant="SRM-DEMO",
        code="NEW_CODE",
        action_id=231,
        expected_version=1,
    )
    assert action["action"]["name"] == "常量说明"
    action_event = next(event for event in client.events if "rel-table-actions" in event[1])
    assert isinstance(action_event[3], list)

    deleted = service.delete(
        resource_type="constant",
        tenant="SRM-DEMO",
        code="NEW_CODE",
        expected_version=1,
    )
    assert deleted["verified"] is True
    delete_event = next(event for event in client.events if event[0] == "DELETE")
    assert delete_event[3]["updateScenario"] == "delete"


def test_closed_resource_and_action_contracts_reject_unknown_values():
    service = PlatformResourceService(PlatformClient(), settings())
    with pytest.raises(ValueError, match="Unknown resource_type"):
        service.search(resource_type="arbitrary_table")
    client = PlatformClient(constant_record())
    with pytest.raises(ValueError, match="not registered"):
        PlatformResourceService(client, settings()).execute_action(
            resource_type="constant",
            tenant="SRM-DEMO",
            code="SECRET_KEY",
            action_id=999,
            expected_version=3,
        )
