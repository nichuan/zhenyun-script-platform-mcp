"""Generic, fail-closed access to verified Script Platform resources."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from ..config import Settings
from ..exceptions import (
    NotFoundError,
    SaveVerificationError,
    ScriptPlatformError,
    VersionConflictError,
)
from ..resources import (
    RESOURCES,
    ResourceDefinition,
    resource_definition,
    table_action,
)
from ..sanitizer import REDACTED, sanitize
from .common import extract_items, versions_equal


class PlatformClient(Protocol):
    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any: ...

    def post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any: ...

    def put(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any: ...

    def delete(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any: ...


@dataclass(slots=True)
class ResourcePage:
    records: list[dict[str, Any]]
    page: dict[str, Any]
    requested: dict[str, Any]
    warnings: list[str]


class PlatformResourceService:
    API_POINT_PATH = "/marmot/v1/0/marmot-api-rewrite/api/point/list"
    DEFINITION_PREFIX = "/sada/v1/rel-table-definitions/find"
    ACTION_PATH = "/sada/v1/rel-table-actions/execute"
    _PROTECTED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "_token",
            "objectVersionNumber",
            "creationDate",
            "createdBy",
            "lastUpdateDate",
            "lastUpdatedBy",
            "updateScenario",
        }
    )

    def __init__(self, client: PlatformClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def capabilities(self) -> dict[str, Any]:
        return {
            "resources": [
                {
                    "resource_type": name,
                    "label": definition.label,
                    "platform_id": definition.platform_id,
                    "code_field": definition.code_field,
                    "read": True,
                    "definition": definition.definition_readable,
                    "write": definition.writable,
                }
                for name, definition in RESOURCES.items()
            ],
            "workflows": {
                "independent_script": ["get", "debug", "save"],
                "adapter": ["get", "debug", "deploy", "create", "update", "toggle", "delete"],
                "generic_resource": [
                    "search",
                    "get",
                    "definition",
                    "relations",
                    "create",
                    "save",
                    "delete",
                ],
                "table_action": ["registered-action execution"],
                "api_mount": ["list points", "create/save api_rewrite"],
            },
            "blocked": {
                "import_start": "HZERO import UI; reference account had no permission",
                "api_test": "HZERO API test UI; reference account had no permission",
            },
            "safety": {
                "arbitrary_url": False,
                "arbitrary_table": False,
                "writes_require_two_phase_human_confirmation": True,
                "write_host_or_tenant_allowlist_required": False,
                "updates_and_deletes_require_expected_version": True,
            },
        }

    def _validate_page(self, page: int, size: int) -> None:
        if page < 0:
            raise ValueError("page must be zero or greater")
        if size < 1 or size > self._settings.max_page_size:
            raise ValueError(f"size must be between 1 and {self._settings.max_page_size}")

    def _search_raw(
        self,
        *,
        resource_type: str,
        tenant: str | None = None,
        code: str | None = None,
        text: str | None = None,
        trace_id: str | None = None,
        last_minutes: int = 60,
        page: int = 0,
        size: int | None = None,
    ) -> tuple[ResourceDefinition, ResourcePage]:
        definition = resource_definition(resource_type)
        actual_size = size or self._settings.default_page_size
        self._validate_page(page, actual_size)
        if tenant:
            self._settings.assert_tenant(tenant)

        page_params = {"page": page, "size": actual_size, "asyncCountFlag": "DEFAULT"}
        filters: dict[str, Any] = {}
        if definition.kind == "query":
            params: dict[str, Any] = dict(page_params)
            if tenant:
                params[definition.tenant_param] = tenant
            if code:
                params[definition.code_field] = code
            if text:
                params["text"] = text
            payload = self._client.get(definition.list_path, params=params)
        elif definition.kind == "rel-table":
            body: dict[str, Any] = dict(page_params)
            # adaptor_static_code was verified as a global table whose tenantId is ignored.
            if tenant and resource_type != "adapter_event":
                body[definition.tenant_param] = tenant
            if code:
                body[definition.code_field] = code
            if text:
                body["description"] = text
            payload = self._client.post(definition.list_path, params=page_params, json=body)
        else:
            body = {"page": page, "lastMinutes": str(last_minutes)}
            if tenant:
                body[definition.tenant_param] = tenant
            if code:
                body[definition.code_field] = code
            if text:
                body["content"] = text
            if trace_id:
                body["traceId"] = trace_id
            payload = self._client.post(definition.list_path, params=page_params, json=body)

        records = [item for item in extract_items(payload) if isinstance(item, dict)]
        warnings: list[str] = []
        observed = sorted(
            {
                str(item.get(definition.tenant_field))
                for item in records
                if item.get(definition.tenant_field) not in {None, ""}
            }
        )
        if tenant and observed and observed != [tenant]:
            warnings.append(
                f"Requested tenant {tenant!r}, but response contained tenant values {observed!r}"
            )
        if not tenant and len(observed) > 1:
            warnings.append("No tenant filter was supplied; the result spans multiple tenants")
        if resource_type == "adapter_event" and tenant:
            warnings.append(
                "adapter_event is a verified global table; its tenant parameter is ignored"
            )

        envelope = payload if isinstance(payload, dict) else {}
        result_page = {
            "number": envelope.get("number", page),
            "size": envelope.get("size", actual_size),
            "total_pages": envelope.get("totalPages"),
            "total_elements": envelope.get("totalElements"),
            "returned": len(records),
        }
        filters.update(
            {
                "tenant": tenant,
                "code": code,
                "text": text,
                "trace_id": trace_id,
                "last_minutes": last_minutes if definition.kind == "script-log" else None,
            }
        )
        return definition, ResourcePage(
            records=records,
            page=result_page,
            requested={"page": page, "size": actual_size, "filters": filters},
            warnings=warnings,
        )

    @staticmethod
    def _truncate(value: str, limit: int) -> dict[str, Any] | str:
        if len(value) <= limit:
            return value
        return {"length": len(value), "preview": value[:limit], "truncated": True}

    def _public_record(
        self,
        definition: ResourceDefinition,
        record: dict[str, Any],
        *,
        truncate: bool,
    ) -> dict[str, Any]:
        public = deepcopy(record)
        for field in definition.mask_fields:
            if field in public:
                public[field] = REDACTED
        if truncate:
            for field in definition.truncate_fields:
                value = public.get(field)
                if isinstance(value, str):
                    public[field] = self._truncate(value, self._settings.text_preview_chars)
        return sanitize(public)

    def search(self, **kwargs: Any) -> dict[str, Any]:
        definition, outcome = self._search_raw(**kwargs)
        return {
            "resource_type": kwargs["resource_type"],
            "platform_id": definition.platform_id,
            "label": definition.label,
            "requested": outcome.requested,
            "page": outcome.page,
            "warnings": outcome.warnings,
            "redaction": {
                "masked_fields": list(definition.mask_fields),
                "truncated_fields": list(definition.truncate_fields),
            },
            "records": [
                self._public_record(definition, record, truncate=True) for record in outcome.records
            ],
        }

    def _get_raw(
        self,
        *,
        resource_type: str,
        tenant: str | None,
        code: str,
    ) -> tuple[ResourceDefinition, dict[str, Any], ResourcePage]:
        definition, outcome = self._search_raw(
            resource_type=resource_type,
            tenant=tenant,
            code=code,
            page=0,
            size=self._settings.max_page_size,
        )
        matches = [
            record
            for record in outcome.records
            if str(record.get(definition.code_field, "")) == code
            and (
                not tenant
                or resource_type == "adapter_event"
                or str(record.get(definition.tenant_field, "")) == tenant
            )
        ]
        if not matches:
            raise NotFoundError(
                f"{resource_type} resource {code!r} was not found"
                + (f" for tenant {tenant!r}" if tenant else "")
            )
        if len(matches) > 1:
            raise VersionConflictError(
                f"Lookup returned {len(matches)} exact records; refine the tenant or code"
            )
        return definition, deepcopy(matches[0]), outcome

    def get(self, *, resource_type: str, code: str, tenant: str | None = None) -> dict[str, Any]:
        definition, record, outcome = self._get_raw(
            resource_type=resource_type, tenant=tenant, code=code
        )
        return {
            "resource_type": resource_type,
            "platform_id": definition.platform_id,
            "label": definition.label,
            "code": code,
            "found": True,
            "scan": outcome.page,
            "warnings": outcome.warnings,
            "record": self._public_record(definition, record, truncate=False),
        }

    @staticmethod
    def _mapping_fields(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        info = payload.get("mappingInfo")
        if isinstance(info, dict):
            slots = info.get("slot2DefinitionMap")
            if isinstance(slots, dict) and slots:
                return [
                    {
                        "slot": slot,
                        "name": value.get("name"),
                        "label": (
                            value.get("_tls", {}).get("label")
                            if isinstance(value.get("_tls"), dict)
                            else value.get("label")
                        ),
                        "type": value.get("type"),
                        "required": value.get("required"),
                        "lov_code": value.get("lovCode"),
                        "lookup_code": value.get("lookupCode"),
                        "component": value.get("_component"),
                    }
                    for slot, value in slots.items()
                    if isinstance(value, dict)
                ], "mappingInfo.slot2DefinitionMap"
            definitions = info.get("definitionList")
            if isinstance(definitions, list):
                return [
                    item for item in definitions if isinstance(item, dict)
                ], "mappingInfo.definitionList"
        raw = payload.get("mappingJson")
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return [], "mappingJson.invalid"
            if isinstance(parsed, dict):
                return PlatformResourceService._mapping_fields({"mappingInfo": parsed})
        return [], "absent"

    def definition(self, *, resource_type: str) -> dict[str, Any]:
        definition = resource_definition(resource_type)
        if not definition.definition_readable:
            raise ValueError(
                f"Definition metadata is not available for resource {resource_type!r} "
                "on the configured DEV platform"
            )
        payload = self._client.get(f"{self.DEFINITION_PREFIX}/{definition.platform_id}")
        if not isinstance(payload, dict):
            raise TypeError("Definition endpoint did not return an object")
        fields, source = self._mapping_fields(payload)
        return {
            "resource_type": resource_type,
            "platform_id": definition.platform_id,
            "label": definition.label,
            "table": {
                key: payload.get(key)
                for key in (
                    "tableCode",
                    "tableName",
                    "description",
                    "module",
                    "dataSource",
                    "supplierIsolation",
                    "noCreation",
                    "saveHistory",
                    "permission",
                )
            },
            "fields": sanitize(fields),
            "field_source": source,
            "actions": {
                key: sanitize(payload.get(key, []))
                for key in ("actionInfos", "lineButtonInfos", "headButtonInfos")
            },
            "mapping_json_returned": False,
        }

    def relations(
        self,
        *,
        code: str,
        tenant: str | None = None,
        scan_size: int = 50,
    ) -> dict[str, Any]:
        relations = {
            "api_publish": ("scriptCode",),
            "api_rewrite": ("scriptCode", "beforeScriptCode"),
            "scheduler": ("scriptCode",),
            "data_import": ("validScriptCode", "doImportScriptCode"),
            "queue_consumer": ("codeBlockCode",),
        }
        hits: list[dict[str, Any]] = []
        scans: list[dict[str, Any]] = []
        for resource_type, fields in relations.items():
            try:
                definition, outcome = self._search_raw(
                    resource_type=resource_type,
                    tenant=tenant,
                    size=scan_size,
                )
                scans.append({"resource_type": resource_type, "scanned": len(outcome.records)})
                for record in outcome.records:
                    matched = [field for field in fields if str(record.get(field, "")) == code]
                    if matched:
                        hits.append(
                            {
                                "resource_type": resource_type,
                                "via_fields": matched,
                                "record": self._public_record(definition, record, truncate=True),
                            }
                        )
            except (ScriptPlatformError, TypeError, ValueError, KeyError) as exc:
                scans.append(
                    {"resource_type": resource_type, "scanned": 0, "error": type(exc).__name__}
                )
        return {
            "code": code,
            "hits": hits,
            "hit_count": len(hits),
            "scan_scope": {
                "per_resource_size": scan_size,
                "resources": scans,
                "complete": False,
                "note": "A bounded scan miss does not prove that no reference exists",
            },
        }

    def api_points(
        self,
        *,
        api_code: str | None = None,
        server_name: str | None = None,
        page: int = 0,
        size: int | None = None,
    ) -> dict[str, Any]:
        actual_size = size or self._settings.default_page_size
        self._validate_page(page, actual_size)
        params: dict[str, Any] = {
            "page": page,
            "size": actual_size,
            "asyncCountFlag": "DEFAULT",
        }
        if api_code:
            params["apiCode"] = api_code
        if server_name:
            params["serverName"] = server_name
        payload = self._client.get(self.API_POINT_PATH, params=params)
        points = [sanitize(item) for item in extract_items(payload) if isinstance(item, dict)]
        envelope = payload if isinstance(payload, dict) else {}
        return {
            "total_elements": envelope.get("totalElements"),
            "returned": len(points),
            "points": points,
            "field_mapping": {"classBeanName": "api_rewrite.beanName"},
        }

    def _assert_write_resource(self, resource_type: str, tenant: str) -> ResourceDefinition:
        self._settings.assert_tenant(tenant)
        definition = resource_definition(resource_type)
        if not definition.writable or definition.kind != "rel-table":
            raise ValueError(
                f"Resource {resource_type!r} does not support generic Rel-Table writes"
            )
        return definition

    @staticmethod
    def _with_tenant(
        definition: ResourceDefinition, tenant: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        result = deepcopy(record)
        if definition.tenant_field == "tenantId" and not tenant.isdigit():
            raise ValueError(f"{definition.platform_id} requires a numeric tenantId")
        result[definition.tenant_field] = (
            int(tenant) if tenant.isdigit() and definition.tenant_field == "tenantId" else tenant
        )
        return result

    def create(
        self,
        *,
        resource_type: str,
        tenant: str,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        definition = self._assert_write_resource(resource_type, tenant)
        if "id" in record or "_token" in record:
            raise ValueError("Create record must not contain id or _token")
        secret_fields = sorted(set(record) & set(definition.mask_fields))
        if secret_fields:
            raise ValueError(
                "Secret fields cannot pass through model context: " + ", ".join(secret_fields)
            )
        code = record.get(definition.code_field)
        if code in {None, ""}:
            raise ValueError(f"Create record must contain {definition.code_field!r}")
        payload = self._with_tenant(definition, tenant, record)
        payload["updateScenario"] = "new"
        self._client.post(definition.record_path, json=payload)
        try:
            _, created, _ = self._get_raw(
                resource_type=resource_type, tenant=tenant, code=str(code)
            )
        except Exception as exc:
            raise SaveVerificationError(
                "Create completed, but the resource could not be reloaded for verification",
                details={"created": True, "verified": False, "cause": type(exc).__name__},
            ) from exc
        return {
            "created": True,
            "verified": True,
            "resource_type": resource_type,
            "code": str(code),
            "record": self._public_record(definition, created, truncate=False),
        }

    def save(
        self,
        *,
        resource_type: str,
        tenant: str,
        code: str,
        changes: dict[str, Any],
        expected_version: str | int,
    ) -> dict[str, Any]:
        definition = self._assert_write_resource(resource_type, tenant)
        if not changes:
            raise ValueError("changes must not be empty")
        blocked = sorted(
            (set(changes) & self._PROTECTED_FIELDS) | (set(changes) & set(definition.mask_fields))
        )
        if blocked:
            raise ValueError(f"Changes contain protected or secret fields: {', '.join(blocked)}")
        _, current, _ = self._get_raw(resource_type=resource_type, tenant=tenant, code=code)
        actual_version = current.get("objectVersionNumber")
        if not versions_equal(expected_version, actual_version):
            raise VersionConflictError(
                "Resource changed after it was loaded. Reload before saving.",
                details={"expected_version": expected_version, "actual_version": actual_version},
            )
        payload = self._with_tenant(definition, tenant, {**current, **deepcopy(changes)})
        payload["updateScenario"] = "update"
        self._client.put(definition.record_path, json=payload)
        _, after, _ = self._get_raw(resource_type=resource_type, tenant=tenant, code=code)
        mismatches = {
            key: {"requested": value, "actual": after.get(key)}
            for key, value in changes.items()
            if after.get(key) != value
        }
        if mismatches:
            raise SaveVerificationError(
                "Save returned successfully, but reloaded fields did not match",
                details={"saved": True, "verified": False, "mismatches": sanitize(mismatches)},
            )
        return {
            "saved": True,
            "verified": True,
            "resource_type": resource_type,
            "code": code,
            "old_version": actual_version,
            "new_version": after.get("objectVersionNumber"),
            "changed_fields": sorted(changes),
        }

    def delete(
        self,
        *,
        resource_type: str,
        tenant: str,
        code: str,
        expected_version: str | int,
    ) -> dict[str, Any]:
        definition = self._assert_write_resource(resource_type, tenant)
        _, current, _ = self._get_raw(resource_type=resource_type, tenant=tenant, code=code)
        actual_version = current.get("objectVersionNumber")
        if not versions_equal(expected_version, actual_version):
            raise VersionConflictError(
                "Resource changed after it was loaded. Reload before deleting.",
                details={"expected_version": expected_version, "actual_version": actual_version},
            )
        payload = deepcopy(current)
        payload["updateScenario"] = "delete"
        self._client.delete(definition.record_path, json=payload)
        try:
            self._get_raw(resource_type=resource_type, tenant=tenant, code=code)
        except NotFoundError:
            return {
                "deleted": True,
                "verified": True,
                "resource_type": resource_type,
                "code": code,
                "old_version": actual_version,
                "recoverable": False,
            }
        raise SaveVerificationError(
            "Delete returned successfully, but the resource still exists",
            details={"deleted": False, "verified": False},
        )

    def execute_action(
        self,
        *,
        resource_type: str,
        tenant: str,
        code: str,
        action_id: int,
        expected_version: str | int,
    ) -> dict[str, Any]:
        definition = self._assert_write_resource(resource_type, tenant)
        action = table_action(definition.platform_id, action_id)
        _, current, _ = self._get_raw(resource_type=resource_type, tenant=tenant, code=code)
        actual_version = current.get("objectVersionNumber")
        if not versions_equal(expected_version, actual_version):
            raise VersionConflictError(
                "Resource changed after it was loaded. Reload before executing the action.",
                details={"expected_version": expected_version, "actual_version": actual_version},
            )
        response = self._client.post(
            self.ACTION_PATH,
            params={"actionId": action_id},
            json=[deepcopy(current)],
        )
        return {
            "executed": True,
            "resource_type": resource_type,
            "code": code,
            "action": {
                "id": action.id,
                "name": action.name,
                "effect": action.effect,
                "backend": action.backend,
                "note": action.note,
            },
            "response": sanitize(response),
            "rollback_supported": False,
        }


__all__ = ["PlatformResourceService"]
