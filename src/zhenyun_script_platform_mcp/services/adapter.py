"""Adapter lookup, unsaved debug, and guarded deployment state machine."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from ..codec import decode_platform_text, encode_platform_text, source_hash
from ..config import Settings
from ..exceptions import (
    AdapterStateError,
    AmbiguousLineError,
    NotFoundError,
    SaveVerificationError,
    ScriptPlatformError,
    VersionConflictError,
)
from ..models import Adapter, AdapterLine, DebugResult
from .common import bool_value, extract_items, versions_equal
from .debug import DebugService
from .fixture import parse_encoded_fixture


class AdapterClient(Protocol):
    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any: ...

    def post(self, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> Any: ...

    def delete(
        self, path: str, *, json: Any = None, params: dict[str, Any] | None = None
    ) -> Any: ...


@dataclass(slots=True)
class AdapterSnapshot:
    public: Adapter
    raw_header: dict[str, Any]
    line_key: str


class AdapterService:
    ENDPOINT = "/sada/v1/adaptor-task-headers"
    TOGGLE_ENDPOINT = f"{ENDPOINT}/toggle-cache"
    SAVE_ENDPOINT = f"{ENDPOINT}/adaptor-save"
    _MUTABLE_HEADER_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "description",
            "inputEntityCode",
            "trustful",
            "favorite",
        }
    )

    def __init__(
        self,
        client: AdapterClient,
        settings: Settings,
        debug_service: DebugService | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._debug = debug_service or DebugService(client)

    def _get_snapshot(
        self,
        *,
        tenant_num: str,
        task_code: str,
        running_service: str,
    ) -> AdapterSnapshot:
        self._settings.assert_tenant(tenant_num)
        payload = self._client.get(
            self.ENDPOINT,
            params={
                "page": 0,
                "size": 10,
                "asyncCountFlag": "DEFAULT",
                "taskCode": task_code,
                "runningService": running_service,
                "applyTenantNum": tenant_num,
            },
        )
        matches = [
            header
            for header in extract_items(payload)
            if str(header.get("taskCode", "")) == task_code
            and str(header.get("runningService", "")) == running_service
            and str(header.get("applyTenantNum", "")) == tenant_num
        ]
        if not matches:
            raise NotFoundError(
                f"Adapter {task_code!r} was not found for service {running_service!r} "
                f"and tenant {tenant_num!r}"
            )
        if len(matches) > 1:
            raise AdapterStateError(
                f"Adapter lookup returned {len(matches)} exact headers; refusing to choose"
            )
        raw = matches[0]
        line_key = "adaptorTaskLines"
        raw_lines = raw.get(line_key)
        if raw_lines is None and "adapterTaskLines" in raw:
            line_key = "adapterTaskLines"
            raw_lines = raw.get(line_key)
        if not isinstance(raw_lines, list):
            raw_lines = []

        lines: list[AdapterLine] = []
        for raw_line in raw_lines:
            if not isinstance(raw_line, dict):
                continue
            source = decode_platform_text(raw_line.get("scriptContent"))
            fixture = parse_encoded_fixture(raw_line.get("inputContent"))
            lines.append(
                AdapterLine(
                    id=raw_line.get("id"),
                    header_id=raw_line.get("headerId"),
                    priority=raw_line.get("priority"),
                    script_type=raw_line.get("scriptType"),
                    output_entity_code=raw_line.get("outputEntityCode"),
                    object_version_number=raw_line.get("objectVersionNumber"),
                    source=source,
                    source_hash=source_hash(source),
                    saved_test_input=fixture.value,
                    test_input_status=fixture.status,
                )
            )
        public = Adapter(
            id=raw.get("id"),
            task_code=str(raw.get("taskCode")),
            description=raw.get("description"),
            input_entity_code=raw.get("inputEntityCode"),
            tenant_num=str(raw.get("applyTenantNum")),
            running_service=str(raw.get("runningService")),
            script_version=raw.get("scriptVersion"),
            enabled=bool_value(raw.get("enabledFlag")),
            object_version_number=raw.get("objectVersionNumber"),
            lines=lines,
        )
        return AdapterSnapshot(public=public, raw_header=deepcopy(raw), line_key=line_key)

    def get(self, *, tenant_num: str, task_code: str, running_service: str) -> Adapter:
        return self._get_snapshot(
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
        ).public

    @staticmethod
    def _select_line(adapter: Adapter, line_id: str | int | None) -> AdapterLine:
        if line_id is not None:
            for line in adapter.lines:
                if str(line.id) == str(line_id):
                    return line
            raise NotFoundError(f"Adapter line {line_id!r} was not found")
        if len(adapter.lines) == 1:
            return adapter.lines[0]
        if not adapter.lines:
            raise NotFoundError("Adapter has no executable lines")
        raise AmbiguousLineError(
            "Adapter has multiple lines; line_id is required",
            details={"line_ids": [line.id for line in adapter.lines]},
        )

    def debug(
        self,
        *,
        tenant_num: str,
        task_code: str,
        running_service: str,
        source: str,
        raw_input: Any,
        line_id: str | int | None = None,
    ) -> DebugResult:
        snapshot = self._get_snapshot(
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
        )
        self._select_line(snapshot.public, line_id)
        return self._debug.run(
            tenant_num=tenant_num,
            source=source,
            raw_input=raw_input,
            script_version=snapshot.public.script_version,
        )

    def _toggle(
        self,
        *,
        tenant_num: str,
        task_code: str,
        enabled: bool,
    ) -> Any:
        return self._client.get(
            self.TOGGLE_ENDPOINT,
            params={
                "applyTenantNum": tenant_num,
                "enabledFlag": "true" if enabled else "false",
                "taskCode": task_code,
            },
        )

    def _reload(self, adapter: Adapter) -> AdapterSnapshot:
        return self._get_snapshot(
            tenant_num=adapter.tenant_num,
            task_code=adapter.task_code,
            running_service=adapter.running_service,
        )

    @staticmethod
    def _failure(error: Exception) -> dict[str, Any]:
        if isinstance(error, ScriptPlatformError):
            return error.as_dict()
        return {
            "code": "SCRIPT_PLATFORM_ERROR",
            "message": f"{type(error).__name__}: {error}",
            "retryable": False,
        }

    def _restore_enabled(self, original: Adapter) -> bool:
        try:
            self._toggle(
                tenant_num=original.tenant_num,
                task_code=original.task_code,
                enabled=True,
            )
            return self._reload(original).public.enabled
        except (ScriptPlatformError, RuntimeError, TypeError, ValueError):
            return False

    @staticmethod
    def _prepare_header_payload(raw_header: dict[str, Any]) -> dict[str, Any]:
        payload = deepcopy(raw_header)
        payload["_status"] = "update"
        payload["__id"] = payload.get("__id", 1)
        raw_lines = payload.get("adaptorTaskLines")
        if raw_lines is None:
            raw_lines = payload.get("adapterTaskLines")
        if not isinstance(raw_lines, list):
            raise AdapterStateError("Adapter payload did not contain its line collection")
        for index, raw_line in enumerate(raw_lines):
            if not isinstance(raw_line, dict):
                raise AdapterStateError("Adapter payload contains a non-object line")
            raw_line["_status"] = "update" if raw_line.get("id") is not None else "create"
            raw_line["__id"] = raw_line.get("__id", 100 + index)
            raw_line["taskCode"] = payload.get("taskCode")
            raw_line["headerId"] = raw_line.get("headerId", payload.get("id"))
        return payload

    def create(
        self,
        *,
        tenant_num: str,
        task_code: str,
        running_service: str,
        description: str = "",
        input_entity_code: str = "ANYTHING",
    ) -> dict[str, Any]:
        self._settings.assert_tenant(tenant_num)
        try:
            self._get_snapshot(
                tenant_num=tenant_num,
                task_code=task_code,
                running_service=running_service,
            )
        except NotFoundError:
            pass
        else:
            raise VersionConflictError(
                "Adapter already exists; use adapter_update or adapter_deploy"
            )

        payload = {
            "taskCode": task_code,
            "applyTenantNum": tenant_num,
            "runningService": running_service,
            "description": description,
            "scriptVersion": 3,
            "inputEntityCode": input_entity_code,
            "trustful": True,
            "favorite": False,
            "_status": "create",
            "__id": 1,
            "adaptorTaskLines": [
                {
                    "priority": 1,
                    "scriptType": "JS",
                    "outputEntityCode": input_entity_code,
                    "_status": "create",
                    "__id": 100,
                }
            ],
        }
        self._client.post(self.SAVE_ENDPOINT, json=payload)
        try:
            after = self._get_snapshot(
                tenant_num=tenant_num,
                task_code=task_code,
                running_service=running_service,
            )
        except Exception as exc:
            raise SaveVerificationError(
                "Adapter create completed, but the new header could not be reloaded",
                details={"created": True, "verified": False, "cause": type(exc).__name__},
            ) from exc
        return {
            "created": True,
            "verified": True,
            "id": after.public.id,
            "header_version": after.public.object_version_number,
            "line_ids": [line.id for line in after.public.lines],
            "enabled": after.public.enabled,
            "note": "Platform event registration may prefill the initial line; reload before editing source",
        }

    def update(
        self,
        *,
        tenant_num: str,
        task_code: str,
        running_service: str,
        changes: dict[str, Any],
        expected_header_version: str | int,
    ) -> dict[str, Any]:
        self._settings.assert_tenant(tenant_num)
        if not changes:
            raise ValueError("changes must not be empty")
        blocked = sorted(set(changes) - self._MUTABLE_HEADER_FIELDS)
        if blocked:
            raise ValueError(
                "adapter_update only accepts metadata fields: "
                + ", ".join(sorted(self._MUTABLE_HEADER_FIELDS))
                + f"; unsupported: {', '.join(blocked)}"
            )
        before = self._get_snapshot(
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
        )
        if not versions_equal(expected_header_version, before.public.object_version_number):
            raise VersionConflictError(
                "Adapter changed after it was loaded. Reload before updating metadata.",
                details={
                    "expected_header_version": expected_header_version,
                    "actual_header_version": before.public.object_version_number,
                },
            )
        payload = self._prepare_header_payload(before.raw_header)
        payload.update(deepcopy(changes))
        self._client.post(self.SAVE_ENDPOINT, json=payload)
        after = self._reload(before.public)
        mismatches = {
            key: {"requested": value, "actual": after.raw_header.get(key)}
            for key, value in changes.items()
            if after.raw_header.get(key) != value
        }
        if mismatches:
            raise SaveVerificationError(
                "Adapter metadata save returned successfully, but reloaded fields did not match",
                details={"saved": True, "verified": False, "mismatches": mismatches},
            )
        return {
            "saved": True,
            "verified": True,
            "old_header_version": before.public.object_version_number,
            "new_header_version": after.public.object_version_number,
            "changed_fields": sorted(changes),
            "enabled": after.public.enabled,
        }

    def toggle(
        self,
        *,
        tenant_num: str,
        task_code: str,
        running_service: str,
        enabled: bool,
        expected_header_version: str | int,
    ) -> dict[str, Any]:
        self._settings.assert_tenant(tenant_num)
        before = self._get_snapshot(
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
        )
        if not versions_equal(expected_header_version, before.public.object_version_number):
            raise VersionConflictError(
                "Adapter changed after it was loaded. Reload before toggling.",
                details={
                    "expected_header_version": expected_header_version,
                    "actual_header_version": before.public.object_version_number,
                },
            )
        if before.public.enabled == enabled:
            return {
                "changed": False,
                "verified": True,
                "enabled": enabled,
                "header_version": before.public.object_version_number,
            }
        self._toggle(tenant_num=tenant_num, task_code=task_code, enabled=enabled)
        after = self._reload(before.public)
        if after.public.enabled != enabled:
            raise AdapterStateError("Adapter state did not match the requested toggle after reload")
        return {
            "changed": True,
            "verified": True,
            "old_enabled": before.public.enabled,
            "enabled": after.public.enabled,
            "old_header_version": before.public.object_version_number,
            "new_header_version": after.public.object_version_number,
        }

    def delete(
        self,
        *,
        tenant_num: str,
        task_code: str,
        running_service: str,
        expected_header_version: str | int,
    ) -> dict[str, Any]:
        self._settings.assert_tenant(tenant_num)
        before = self._get_snapshot(
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
        )
        if not versions_equal(expected_header_version, before.public.object_version_number):
            raise VersionConflictError(
                "Adapter changed after it was loaded. Reload before deleting.",
                details={
                    "expected_header_version": expected_header_version,
                    "actual_header_version": before.public.object_version_number,
                },
            )
        if before.public.enabled:
            raise AdapterStateError(
                "Refusing to delete an enabled Adapter; call adapter_toggle(enabled=false) first"
            )
        self._client.delete(self.ENDPOINT, json=deepcopy(before.raw_header))
        try:
            self._reload(before.public)
        except NotFoundError:
            return {
                "deleted": True,
                "verified": True,
                "id": before.public.id,
                "old_header_version": before.public.object_version_number,
                "recoverable": False,
            }
        raise SaveVerificationError(
            "Adapter delete returned successfully, but the header still exists",
            details={"deleted": False, "verified": False},
        )

    def deploy(
        self,
        *,
        tenant_num: str,
        task_code: str,
        running_service: str,
        source: str,
        line_id: str | int | None = None,
        expected_header_version: str | int | None = None,
        expected_line_version: str | int | None = None,
        enable: bool = False,
    ) -> dict[str, Any]:
        original = self._get_snapshot(
            tenant_num=tenant_num,
            task_code=task_code,
            running_service=running_service,
        )
        original_line = self._select_line(original.public, line_id)
        if expected_header_version is not None and not versions_equal(
            expected_header_version, original.public.object_version_number
        ):
            raise VersionConflictError(
                "Adapter header was modified after loading. Reload before deploying.",
                details={
                    "expected_header_version": expected_header_version,
                    "actual_header_version": original.public.object_version_number,
                },
            )
        if expected_line_version is not None and not versions_equal(
            expected_line_version, original_line.object_version_number
        ):
            raise VersionConflictError(
                "Adapter line was modified after loading. Reload before deploying.",
                details={
                    "line_id": original_line.id,
                    "expected_line_version": expected_line_version,
                    "actual_line_version": original_line.object_version_number,
                },
            )

        original_enabled = original.public.enabled
        current = original
        disabled_by_us = False
        try:
            if original_enabled:
                self._toggle(tenant_num=tenant_num, task_code=task_code, enabled=False)
                disabled_by_us = True
                current = self._reload(original.public)
                if current.public.enabled:
                    raise AdapterStateError("Adapter remained enabled after the disable operation")

            target_line = self._select_line(current.public, original_line.id)
            payload = self._prepare_header_payload(current.raw_header)
            raw_lines = payload.get(current.line_key)
            if not isinstance(raw_lines, list):
                raise AdapterStateError("Fresh adapter payload did not contain its line collection")
            replaced = False
            for raw_line in raw_lines:
                if not isinstance(raw_line, dict):
                    continue
                if str(raw_line.get("id")) == str(target_line.id):
                    raw_line["scriptContent"] = encode_platform_text(source)
                    replaced = True
            if not replaced:
                raise AdapterStateError("Target line disappeared before save")
        except (ScriptPlatformError, RuntimeError, TypeError, ValueError) as exc:
            if not disabled_by_us:
                raise
            restored = self._restore_enabled(original.public)
            raise AdapterStateError(
                "Adapter deployment preparation failed after disable; restore was attempted",
                details={
                    "enabled_restored": restored,
                    "requires_manual_attention": not restored,
                    "cause": self._failure(exc),
                },
            ) from exc

        try:
            self._client.post(self.SAVE_ENDPOINT, json=payload)
        except (ScriptPlatformError, RuntimeError, TypeError, ValueError) as exc:
            restored = self._restore_enabled(original.public) if original_enabled else True
            return {
                "saved": False,
                "verified": False,
                "original_enabled": original_enabled,
                "enabled_restored": restored,
                "requires_manual_attention": original_enabled and not restored,
                "error": self._failure(exc),
            }

        try:
            after_save = self._reload(original.public)
            saved_line = self._select_line(after_save.public, target_line.id)
        except (ScriptPlatformError, RuntimeError, TypeError, ValueError) as exc:
            raise SaveVerificationError(
                "Adapter save completed, but the saved state could not be reloaded; kept disabled",
                details={
                    "saved": True,
                    "verified": False,
                    "final_enabled": False,
                    "requires_manual_attention": True,
                    "cause": self._failure(exc),
                },
            ) from exc

        if saved_line.source != source:
            raise SaveVerificationError(
                "Adapter save completed, but the reloaded source did not match; kept disabled",
                details={
                    "saved": True,
                    "verified": False,
                    "final_enabled": False,
                    "requires_manual_attention": True,
                    "line_id": target_line.id,
                    "requested_hash": source_hash(source),
                    "actual_hash": saved_line.source_hash,
                },
            )

        result: dict[str, Any] = {
            "saved": True,
            "verified": True,
            "line_id": target_line.id,
            "old_hash": original_line.source_hash,
            "new_hash": saved_line.source_hash,
            "original_enabled": original_enabled,
            "final_enabled": False,
            "old_header_version": original.public.object_version_number,
            "new_header_version": after_save.public.object_version_number,
            "old_line_version": original_line.object_version_number,
            "new_line_version": saved_line.object_version_number,
            "requires_manual_attention": False,
        }
        if not original_enabled and not enable:
            return result

        try:
            self._toggle(tenant_num=tenant_num, task_code=task_code, enabled=True)
            final = self._reload(original.public)
            if not final.public.enabled:
                raise AdapterStateError("Adapter remained disabled after re-enable")
            result["final_enabled"] = True
            result["new_header_version"] = final.public.object_version_number
            return result
        except (ScriptPlatformError, RuntimeError, TypeError, ValueError) as exc:
            result.update(
                {
                    "final_enabled": False,
                    "error": {
                        "code": "RE_ENABLE_FAILED",
                        "message": "Source was saved and verified, but the adapter could not be re-enabled",
                        "retryable": False,
                        "cause": self._failure(exc),
                    },
                    "requires_manual_attention": True,
                }
            )
            return result
