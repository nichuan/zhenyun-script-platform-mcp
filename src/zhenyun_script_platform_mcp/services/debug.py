"""Unsaved-source execution against the remote GraalJS debug runtime."""

from __future__ import annotations

import json
from typing import Any, Protocol

from ..codec import encode_platform_text
from ..config import Settings
from ..exceptions import DebugExecutionError, InvalidFixtureError, NoValidFixtureError
from ..models import DebugResult
from ..sanitizer import sanitize, sanitize_text, validate_source_integrity


class DebugClient(Protocol):
    def post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any: ...


def _parse_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_debug_response(payload: Any) -> DebugResult:
    if not isinstance(payload, dict):
        raise DebugExecutionError("Debug runtime returned an unexpected response shape")
    body = payload
    if isinstance(payload.get("data"), dict) and "result" in payload["data"]:
        body = payload["data"]
    result = _parse_json_string(body.get("result"))
    if isinstance(result, dict) and "body" in result:
        result = dict(result)
        result["body"] = _parse_json_string(result["body"])
    logs = body.get("outPutLog", body.get("outputLog"))
    return DebugResult(
        success=True,
        result=result,
        logs=sanitize_text(logs) if isinstance(logs, str) else None,
        execution_info=sanitize(body.get("executionInfo")),
        query_block_sql=sanitize(body.get("queryBlockSql")),
        raw_result=sanitize(payload),
    )


class DebugService:
    ENDPOINT = "/sada/v1/script-debug/run"

    def __init__(self, client: DebugClient, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings

    @staticmethod
    def _serialize_input(raw_input: Any) -> str:
        if raw_input is None:
            raise NoValidFixtureError(
                "No valid debug input was provided. Supply an explicit raw_input or a valid fixture."
            )
        if isinstance(raw_input, str):
            try:
                json.loads(raw_input)
            except json.JSONDecodeError as exc:
                raise InvalidFixtureError("raw_input string must contain valid JSON") from exc
            return raw_input
        try:
            return json.dumps(raw_input, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise InvalidFixtureError("raw_input must be JSON serializable") from exc

    def run(
        self,
        *,
        tenant_num: str,
        source: str,
        raw_input: Any,
        script_version: str | int | None = None,
    ) -> DebugResult:
        validate_source_integrity(source)
        if self._settings is not None:
            self._settings.assert_tenant(tenant_num)
        params: dict[str, Any] = {"debugTenantNum": tenant_num}
        if script_version is not None:
            params["scriptVersion"] = script_version
        response = self._client.post(
            self.ENDPOINT,
            params=params,
            json={
                "script": encode_platform_text(source),
                "rawInputJsonStr": self._serialize_input(raw_input),
            },
        )
        return parse_debug_response(response)
