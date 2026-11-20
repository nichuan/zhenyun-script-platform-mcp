"""One signed plan for a batch of existing scripts, executed in a background job."""

from __future__ import annotations

import json
import time
from copy import deepcopy
from dataclasses import asdict, dataclass
from threading import Lock, Thread
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator

from ..codec import assert_plain_script_content, source_hash
from ..confirmation import ConfirmationManager
from ..description_note import append_requirement_code, normalize_requirement_code
from ..exceptions import ConfirmationError, NotFoundError, ScriptPlatformError, VersionConflictError
from ..sanitizer import validate_source_integrity
from .adapter import AdapterService, adapter_revision
from .independent import IndependentScriptService


class _BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("task_code", "running_service", "code", check_fields=False)
    @classmethod
    def nonempty_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Target identifiers cannot be empty")
        return value


class BatchAdapter(_BatchInput):
    task_code: str
    running_service: str
    line_id: str | int | None = None
    source: str | None = None


class BatchIndependent(_BatchInput):
    code: str
    source: str


@dataclass(frozen=True, slots=True)
class Change:
    kind: Literal["adapter", "independent_script"]
    target: dict[str, Any]
    revision: dict[str, Any]
    source: str | None
    description: str | None = None

    @property
    def changed(self) -> bool:
        return self.source is not None or self.description is not None

    def preview(self) -> dict[str, Any]:
        preview = {"kind": self.kind, **self.target, "changed": self.changed}
        if self.kind == "adapter":
            preview.update(
                expected_header_version=self.revision["header_version"],
                original_enabled=self.revision["enabled"],
                planned_final_enabled=self.revision["enabled"],
                disable_before_save=self.changed and self.revision["enabled"],
            )
            if self.target["line_id"] is not None:
                version, old_hash = self.revision["lines"][str(self.target["line_id"])]
                preview.update(expected_line_version=version, old_hash=old_hash)
            if self.description is not None:
                preview.update(
                    old_description=self.revision["description"], new_description=self.description
                )
        else:
            preview.update(
                expected_version=self.revision["version"], old_hash=self.revision["source_hash"]
            )
        if self.source is not None:
            preview["requested_hash"] = source_hash(self.source)
        return preview


@dataclass(frozen=True, slots=True)
class BatchPlan:
    base_url: str
    tenant_num: str
    requirement_code: str | None
    changes: tuple[Change, ...]

    def preview(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "tenant_num": self.tenant_num,
            "requirement_code": self.requirement_code,
            "request_sha256": source_hash(
                json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)
            ),
            "failure_policy": "stop_on_first_failure",
            "items": [change.preview() for change in self.changes],
        }


class ScriptBatchService:
    TOOL = "script_batch_deploy"
    MAX_ITEMS = 30
    MAX_SOURCE_BYTES = 4 * 1024 * 1024
    MAX_HISTORY = 16

    def __init__(
        self,
        adapter: AdapterService,
        independent: IndependentScriptService,
        confirmation: ConfirmationManager,
        *,
        base_url: str,
        ttl_seconds: int,
    ) -> None:
        self._adapter = adapter
        self._independent = independent
        self._confirmation = confirmation
        self._base_url = base_url
        self._ttl_seconds = ttl_seconds
        self._pending: dict[str, tuple[BatchPlan, float]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active: str | None = None
        self._lock = Lock()

    def prepare(
        self,
        *,
        tenant_num: str,
        adapters: list[BatchAdapter],
        independent_scripts: list[BatchIndependent],
        requirement_code: str | None,
    ) -> dict[str, Any]:
        tenant_num = tenant_num.strip()
        if not tenant_num:
            raise ValueError("tenant_num must not be empty")
        if not 1 <= len(adapters) + len(independent_scripts) <= self.MAX_ITEMS:
            raise ValueError(f"A batch must contain 1-{self.MAX_ITEMS} existing scripts")
        if len({item.task_code for item in adapters}) != len(adapters):
            raise ValueError("Each adapter task code may appear only once in a tenant batch")
        if len({item.code for item in independent_scripts}) != len(independent_scripts):
            raise ValueError("Each independent script code may appear only once in a batch")
        if requirement_code is not None:
            requirement_code = normalize_requirement_code(requirement_code)
        for item in adapters:
            if item.source is None and (requirement_code is None or item.line_id is not None):
                raise ValueError(
                    "An adapter needs source or requirement_code. line_id requires source."
                )
        sources = [
            item.source for item in (*adapters, *independent_scripts) if item.source is not None
        ]
        if sum(len(source.encode("utf-8")) for source in sources) > self.MAX_SOURCE_BYTES:
            raise ValueError("Combined script sources exceed 4 MiB")
        for source in sources:
            validate_source_integrity(source)
        for item in independent_scripts:
            assert_plain_script_content(item.source)

        changes = [self._plan_independent(tenant_num, item) for item in independent_scripts] + [
            self._plan_adapter(tenant_num, item, requirement_code) for item in adapters
        ]
        plan = BatchPlan(self._base_url, tenant_num, requirement_code, tuple(changes))
        if not any(change.changed for change in changes):
            return {
                "requires_confirmation": False,
                "status": "unchanged",
                "preview": plan.preview(),
            }
        with self._lock:
            self._discard_expired_plans()
            if len(self._pending) >= self.MAX_HISTORY:
                raise ConfirmationError(
                    "Too many pending batch plans. Wait for older plans to expire."
                )
            prepared = self._confirmation.prepare(
                tool=self.TOOL, arguments=asdict(plan), preview=plan.preview()
            )
            self._pending[prepared["confirmation_token"]] = (
                plan,
                time.monotonic() + self._ttl_seconds,
            )
        prepared["instruction"] = (
            "Show the exact batch plan and wait for one later explicit human confirmation. "
            "Then call script_batch_deploy with only confirmation_token, and poll "
            "script_batch_status until the batch reaches a terminal status."
        )
        return prepared

    def _plan_independent(self, tenant_num: str, item: BatchIndependent) -> Change:
        before = self._independent.get(tenant_num=tenant_num, code=item.code)
        if before.object_version_number is None:
            raise VersionConflictError(f"Independent script {item.code!r} has no version")
        revision = {
            "id": before.id,
            "version": before.object_version_number,
            "source_hash": before.source_hash,
        }
        return Change(
            "independent_script",
            {"code": item.code},
            revision,
            None if before.source == item.source else item.source,
        )

    def _plan_adapter(
        self, tenant_num: str, item: BatchAdapter, requirement_code: str | None
    ) -> Change:
        target = {"task_code": item.task_code, "running_service": item.running_service}
        before = self._adapter.get(tenant_num=tenant_num, **target)
        if before.object_version_number is None:
            raise VersionConflictError(f"Adapter {item.task_code!r} has no header version")
        line = self._adapter._select_line(before, item.line_id) if item.source is not None else None
        if line is not None and line.object_version_number is None:
            raise VersionConflictError(f"Adapter {item.task_code!r} line {line.id} has no version")
        description = (
            append_requirement_code(before.description, requirement_code)
            if requirement_code is not None
            else before.description
        )
        change = Change(
            "adapter",
            {**target, "line_id": line.id if line is not None else None},
            adapter_revision(before),
            item.source if line is not None and line.source != item.source else None,
            description if description != before.description else None,
        )
        if change.changed and before.enabled:
            self._adapter.assert_unique_toggle_target(before)
        return change

    def _discard_expired_plans(self) -> None:
        now = time.monotonic()
        for token, (_, expiry) in list(self._pending.items()):
            if expiry < now:
                del self._pending[token]

    def start(self, confirmation_token: str) -> dict[str, Any]:
        with self._lock:
            self._discard_expired_plans()
            pending = self._pending.get(confirmation_token)
            if pending is None:
                raise ConfirmationError("Batch plan is unknown, expired or already confirmed")
            if self._active is not None:
                raise ConfirmationError(
                    f"Batch {self._active} is still running. Check its status before starting another."
                )
            plan, _ = pending
            self._confirmation.consume(
                tool=self.TOOL, arguments=asdict(plan), token=confirmation_token
            )
            del self._pending[confirmation_token]
            preview = plan.preview()
            batch_id = uuid4().hex
            self._jobs[batch_id] = {
                "batch_id": batch_id,
                "status": "running",
                "human_confirmation": "verified",
                **preview,
            }
            for item in self._jobs[batch_id]["items"]:
                item["status"] = "pending"
            while len(self._jobs) > self.MAX_HISTORY:
                del self._jobs[next(iter(self._jobs))]
            self._active = batch_id
            try:
                Thread(target=self._run, args=(batch_id, plan), daemon=True).start()
            except RuntimeError:
                self._jobs[batch_id]["status"] = "failed"
                for item in self._jobs[batch_id]["items"]:
                    item["status"] = "not_run"
                self._active = None
                raise
            return {"batch_id": batch_id, "status": "running", "human_confirmation": "verified"}

    def status(self, batch_id: str) -> dict[str, Any]:
        with self._lock:
            if batch_id not in self._jobs:
                raise NotFoundError("Batch status is unavailable in this MCP process")
            return deepcopy(self._jobs[batch_id])

    def _apply(self, tenant_num: str, change: Change) -> dict[str, Any]:
        if not change.changed:
            if change.kind == "adapter":
                current = self._adapter.get(
                    tenant_num=tenant_num,
                    task_code=change.target["task_code"],
                    running_service=change.target["running_service"],
                )
                if adapter_revision(current) != change.revision:
                    raise VersionConflictError("Unchanged adapter drifted since the batch plan")
            else:
                current_script = self._independent.get(tenant_num=tenant_num, **change.target)
                if (
                    current_script.id != change.revision["id"]
                    or current_script.object_version_number != change.revision["version"]
                    or current_script.source_hash != change.revision["source_hash"]
                ):
                    raise VersionConflictError(
                        "Unchanged independent script drifted since the plan"
                    )
            return {"status": "unchanged"}
        if change.kind == "adapter":
            result = self._adapter.apply_guarded_change(
                tenant_num=tenant_num,
                **change.target,
                revision=change.revision,
                source=change.source,
                changes={"description": change.description}
                if change.description is not None
                else {},
            )
        else:
            assert change.source is not None
            result = self._independent.save(
                tenant_num=tenant_num,
                **change.target,
                source=change.source,
                expected_version=change.revision["version"],
                expected_id=change.revision["id"],
                expected_source_hash=change.revision["source_hash"],
            )
        return {"status": "failed" if "error" in result else "verified", **result}

    def _run(self, batch_id: str, plan: BatchPlan) -> None:
        with self._lock:
            job = self._jobs[batch_id]
        failed = False
        for index, change in enumerate(plan.changes):
            with self._lock:
                job["items"][index]["status"] = "running"
            try:
                result = self._apply(plan.tenant_num, change)
            except Exception as exc:  # noqa: BLE001 - every worker failure must reach batch status
                error = (
                    exc.as_dict()
                    if isinstance(exc, ScriptPlatformError)
                    else {"code": "SCRIPT_PLATFORM_ERROR", "message": str(exc)}
                )
                result = {"status": "failed", "error": error}
                if (
                    error.get("details", {}).get("outcome") == "unknown"
                    or error["code"] == "SAVE_VERIFICATION_FAILED"
                ):
                    result["requires_manual_attention"] = True
            with self._lock:
                job["items"][index].update(result)
                if result["status"] == "failed":
                    failed = True
                    for item in job["items"][index + 1 :]:
                        item["status"] = "not_run"
                    break
        with self._lock:
            job["status"] = "failed" if failed else "completed"
            self._active = None
