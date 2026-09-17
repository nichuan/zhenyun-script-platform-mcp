"""Minimal, centralized Script Platform HTTP client."""

from __future__ import annotations

import json
import uuid
from typing import Any, Self

import httpx

from ..config import Settings
from ..exceptions import (
    AuthenticationError,
    NotFoundError,
    ScriptPlatformError,
    VersionConflictError,
)
from .auth import AuthProvider


class ScriptPlatformClient:
    def __init__(
        self,
        settings: Settings,
        auth_provider: AuthProvider | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._auth = auth_provider or AuthProvider(settings)
        self._client = httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout,
            verify=settings.verify_ssl,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        return self.request("POST", path, params=params, json=json)

    def put(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        return self.request("PUT", path, params=params, json=json)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        request_id = uuid.uuid4().hex
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers["X-Request-ID"] = request_id
        token = self._auth.get_bearer_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.settings.cookie:
            headers["Cookie"] = self.settings.cookie
        if self.settings.menu_id:
            headers["H-Menu-Id"] = self.settings.menu_id
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=json,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise ScriptPlatformError(
                f"Script Platform request timed out after {self.settings.timeout:g}s",
                details={"request_id": request_id},
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ScriptPlatformError(
                f"Script Platform request failed: {type(exc).__name__}",
                details={"request_id": request_id},
                retryable=True,
            ) from exc

        payload = self._decode_response(response)
        request_id = response.headers.get("X-Request-ID", request_id)
        if response.status_code in {401, 403}:
            raise AuthenticationError(
                "Script Platform rejected the configured credentials",
                details={"request_id": request_id},
            )
        if response.status_code == 404:
            raise NotFoundError(
                "Script Platform resource was not found", details={"request_id": request_id}
            )
        if response.status_code == 409:
            raise VersionConflictError(
                "Script Platform reported an optimistic-lock conflict",
                details={"request_id": request_id},
            )
        if response.status_code >= 500:
            raise ScriptPlatformError(
                f"Script Platform returned HTTP {response.status_code}",
                details={"request_id": request_id},
                retryable=True,
            )
        if response.status_code >= 400:
            raise ScriptPlatformError(
                f"Script Platform returned HTTP {response.status_code}",
                details={"request_id": request_id},
            )
        self._raise_business_error(payload, request_id)
        return payload

    @staticmethod
    def _decode_response(response: httpx.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise ScriptPlatformError(
                f"Script Platform returned non-JSON content (HTTP {response.status_code})"
            ) from exc

    @staticmethod
    def _raise_business_error(payload: Any, request_id: str) -> None:
        if not isinstance(payload, dict):
            return
        failed = payload.get("success") is False or payload.get("failed") is True
        status = str(payload.get("status", "")).strip().lower()
        if status in {"failed", "failure", "error"}:
            failed = True
        if not failed:
            return
        message = str(
            payload.get("message")
            or payload.get("msg")
            or payload.get("error")
            or "Script Platform business operation failed"
        )
        lowered = message.lower()
        if "version" in lowered or "optimistic" in lowered or "lock" in lowered:
            raise VersionConflictError(message, details={"request_id": request_id})
        raise ScriptPlatformError(message, details={"request_id": request_id})
