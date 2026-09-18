"""Independent Script lookup and version-safe full-record save."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from ..codec import decode_platform_text, encode_platform_text, source_hash
from ..config import Settings
from ..exceptions import NotFoundError, SaveVerificationError, VersionConflictError
from ..models import IndependentScript
from .common import extract_items, versions_equal
from .fixture import parse_encoded_fixture


class IndependentClient(Protocol):
    def post(self, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> Any: ...

    def put(self, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> Any: ...


@dataclass(slots=True)
class IndependentSnapshot:
    public: IndependentScript
    raw_record: dict[str, Any]


class IndependentScriptService:
    ENDPOINT = "/sada/v1/rel-table-records/marmot_script_library"
    PAGE_ENDPOINT = f"{ENDPOINT}/page"

    def __init__(self, client: IndependentClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def _get_snapshot(self, *, tenant_num: str, code: str) -> IndependentSnapshot:
        self._settings.assert_tenant(tenant_num)
        payload = self._client.post(
            self.PAGE_ENDPOINT,
            json={
                "code": code,
                "tenantNum": tenant_num,
                "page": 0,
                "size": 10,
                "asyncCountFlag": "DEFAULT",
            },
        )
        matches = [
            record
            for record in extract_items(payload)
            if str(record.get("code", "")) == code
            and str(record.get("tenantNum", "")) == tenant_num
        ]
        if not matches:
            raise NotFoundError(
                f"Independent script {code!r} was not found for tenant {tenant_num!r}"
            )
        if len(matches) > 1:
            raise VersionConflictError(
                f"Independent script lookup returned {len(matches)} exact records; refusing to choose"
            )
        record = matches[0]
        source = decode_platform_text(record.get("content"))
        fixture = parse_encoded_fixture(record.get("contentInput"))
        public = IndependentScript(
            id=record.get("id"),
            code=str(record.get("code")),
            tenant_num=str(record.get("tenantNum")),
            tenant_id=record.get("tenantId"),
            quick_type=record.get("quickType"),
            description=record.get("description"),
            object_version_number=record.get("objectVersionNumber"),
            source=source,
            source_hash=source_hash(source),
            saved_test_input=fixture.value,
            test_input_status=fixture.status,
        )
        return IndependentSnapshot(public=public, raw_record=deepcopy(record))

    def get(self, *, tenant_num: str, code: str) -> IndependentScript:
        return self._get_snapshot(tenant_num=tenant_num, code=code).public

    def save(
        self,
        *,
        tenant_num: str,
        code: str,
        source: str,
        expected_version: str | int | None = None,
    ) -> dict[str, Any]:
        before = self._get_snapshot(tenant_num=tenant_num, code=code)
        actual_version = before.public.object_version_number
        if expected_version is not None and not versions_equal(expected_version, actual_version):
            raise VersionConflictError(
                "Independent script was modified after loading. Reload before saving.",
                details={"expected_version": expected_version, "actual_version": actual_version},
            )
        payload = deepcopy(before.raw_record)
        payload["content"] = encode_platform_text(source)
        self._client.put(self.ENDPOINT, json=payload)

        try:
            after = self._get_snapshot(tenant_num=tenant_num, code=code)
        except Exception as exc:
            raise SaveVerificationError(
                "Save completed, but the independent script could not be reloaded for verification",
                details={
                    "saved": True,
                    "verified": False,
                    "old_hash": before.public.source_hash,
                    "requested_hash": source_hash(source),
                    "cause": type(exc).__name__,
                },
            ) from exc
        if after.public.source != source:
            raise SaveVerificationError(
                "Save returned successfully, but the reloaded source did not match",
                details={
                    "saved": True,
                    "verified": False,
                    "old_hash": before.public.source_hash,
                    "requested_hash": source_hash(source),
                    "actual_hash": after.public.source_hash,
                },
            )
        return {
            "saved": True,
            "verified": True,
            "old_version": actual_version,
            "new_version": after.public.object_version_number,
            "old_hash": before.public.source_hash,
            "new_hash": after.public.source_hash,
        }
