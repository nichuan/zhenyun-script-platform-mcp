from copy import deepcopy

import pytest

from zhenyun_script_platform_mcp.codec import decode_platform_text, encode_platform_text
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


class EventuallyVisibleClient(PlatformClient):
    def __init__(self, record=None, hidden_page_reads=1):
        super().__init__(record)
        self.hidden_page_reads = hidden_page_reads

    def post(self, path, *, params=None, json=None):
        if path.endswith("/page") and self.hidden_page_reads:
            self.events.append(("POST", path, deepcopy(params), deepcopy(json)))
            self.hidden_page_reads -= 1
            return {"content": [], "totalElements": 0, "totalPages": 0, "number": 0}
        return super().post(path, params=params, json=json)


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


def test_independent_definition_exposes_known_platform_validation_notes():
    service = PlatformResourceService(PlatformClient(), settings())
    result = service.definition(resource_type="independent_script")
    assert any("uppercase" in note for note in result["validation_notes"])
    assert any("description" in note for note in result["validation_notes"])


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


def test_independent_resource_create_encodes_plain_text_and_defaults_empty_source():
    client = PlatformClient()
    service = PlatformResourceService(client, settings())

    service.create(
        resource_type="independent_script",
        tenant="SRM-DEMO",
        record={
            "code": "NEW_SCRIPT",
            "description": "cdp-00000 create test",
            "permission": "PUBLIC",
            "module": "srm",
            "content": "return input;",
            "contentInput": {"body": {}},
        },
    )
    assert decode_platform_text(client.record["content"]) == "return input;"
    assert decode_platform_text(client.record["contentInput"]) == '{"body":{}}'
    create_event = next(
        event for event in client.events if event[0] == "POST" and not event[1].endswith("/page")
    )
    assert create_event[3]["tenantId"] == 0

    empty_client = PlatformClient()
    PlatformResourceService(empty_client, settings()).create(
        resource_type="independent_script",
        tenant="SRM-DEMO",
        record={
            "code": "EMPTY_SCRIPT",
            "description": "cdp-00000 empty test",
            "permission": "PUBLIC",
            "module": "srm",
        },
    )
    assert decode_platform_text(empty_client.record["content"]) == ""


def test_generic_independent_save_encodes_plain_text_changes():
    client = PlatformClient(
        {
            "id": 7,
            "code": "EDIT_SCRIPT",
            "tenantNum": "SRM-DEMO",
            "objectVersionNumber": 1,
            "content": encode_platform_text("return old;")
        }
    )
    result = PlatformResourceService(client, settings()).save(
        resource_type="independent_script",
        tenant="SRM-DEMO",
        code="EDIT_SCRIPT",
        changes={"content": "return new;"},
        expected_version=1,
    )

    assert result["verified"] is True
    assert decode_platform_text(client.record["content"]) == "return new;"


def test_independent_create_rejects_hidden_platform_constraints_locally():
    client = PlatformClient()
    service = PlatformResourceService(client, settings())
    with pytest.raises(ValueError, match="code must contain"):
        service.create(
            resource_type="independent_script",
            tenant="SRM-DEMO",
            record={
                "code": "bad-code",
                "description": "cdp-00000 invalid",
                "permission": "PUBLIC",
                "module": "srm",
            },
        )
    with pytest.raises(ValueError, match="description must start"):
        service.create(
            resource_type="independent_script",
            tenant="SRM-DEMO",
            record={
                "code": "VALID_CODE",
                "description": "not-a-demand-code",
                "permission": "PUBLIC",
                "module": "srm",
            },
        )
    assert client.events == []


def test_independent_create_rejects_missing_required_permission_or_module():
    client = PlatformClient()
    service = PlatformResourceService(client, settings())
    with pytest.raises(ValueError, match="permission"):
        service.create(
            resource_type="independent_script",
            tenant="SRM-DEMO",
            record={
                "code": "VALID_CODE",
                "description": "cdp-00000 missing permission",
                "module": "srm",
            },
        )
    assert client.events == []


def test_create_verification_retries_short_platform_visibility_delay():
    client = EventuallyVisibleClient(hidden_page_reads=2)
    service = PlatformResourceService(
        client,
        Settings(
            base_url="https://gateway.dev.example.com",
            create_verify_attempts=3,
            create_verify_delay_seconds=0,
        ),
    )
    result = service.create(
        resource_type="constant",
        tenant="SRM-DEMO",
        record={"constantCode": "EVENTUALLY_VISIBLE", "description": "demo"},
    )
    assert result["verified"] is True
    assert client.hidden_page_reads == 0


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
    assert post[3]["tenantId"] == 0

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


def test_scheduler_requires_numeric_tenant_id_before_http_call():
    client = PlatformClient()
    service = PlatformResourceService(client, settings())
    with pytest.raises(ValueError, match="numeric tenantId"):
        service.search(resource_type="scheduler", tenant="SRM-ZHENYUN")
    assert client.events == []


def test_relations_do_not_silently_skip_scheduler_for_tenant_code():
    client = PlatformClient(constant_record())
    result = PlatformResourceService(client, settings()).relations(
        code="SCRIPT_CODE", tenant="SRM-ZHENYUN"
    )
    scheduler_scan = next(
        scan for scan in result["scan_scope"]["resources"] if scan["resource_type"] == "scheduler"
    )
    assert scheduler_scan["effective_tenant"] is None
    assert scheduler_scan["scanned"] == 1
    assert result["warnings"]


def test_relations_accept_explicit_numeric_scheduler_tenant_id():
    client = PlatformClient(constant_record())
    result = PlatformResourceService(client, settings()).relations(
        code="SCRIPT_CODE", tenant="SRM-ZHENYUN", scheduler_tenant_id=30
    )
    scheduler_scan = next(
        scan for scan in result["scan_scope"]["resources"] if scan["resource_type"] == "scheduler"
    )
    assert scheduler_scan["effective_tenant"] == "30"
    scheduler_request = next(
        event
        for event in client.events
        if event[0] == "POST" and "marmot_scheduler/page" in event[1]
    )
    assert scheduler_request[3]["tenantId"] == 30


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
