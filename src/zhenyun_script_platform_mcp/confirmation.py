"""Two-phase, short-lived confirmation tokens for every platform mutation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .exceptions import ConfirmationError
from .sanitizer import sanitize


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _digest(tool: str, arguments: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"tool": tool, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ConfirmationManager:
    def __init__(
        self,
        ttl_seconds: int = 600,
        *,
        secret: bytes | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._secret = secret or secrets.token_bytes(32)
        self._now = now
        self._used: set[str] = set()
        self._lock = threading.Lock()

    def prepare(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        preview: dict[str, Any],
    ) -> dict[str, Any]:
        expires_at = int(self._now()) + self._ttl_seconds
        payload = {
            "tool": tool,
            "digest": _digest(tool, arguments),
            "exp": expires_at,
            "nonce": secrets.token_urlsafe(12),
        }
        encoded = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = _b64encode(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
        return {
            "requires_confirmation": True,
            "confirmation_token": f"{encoded}.{signature}",
            "expires_at": datetime.fromtimestamp(expires_at, UTC).isoformat(),
            "operation": tool,
            "preview": sanitize(preview),
            "instruction": (
                "Show this exact plan to the user and stop. Execute only after a later user "
                "message explicitly confirms it, then call the same tool with confirmation_token."
            ),
        }

    def consume(self, *, tool: str, arguments: dict[str, Any], token: str) -> None:
        try:
            encoded, supplied_signature = token.split(".", 1)
            raw = _b64decode(encoded)
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ConfirmationError("The confirmation token is malformed") from exc
        expected_signature = _b64encode(
            hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ConfirmationError("The confirmation token signature is invalid")
        if not isinstance(payload, dict):
            raise ConfirmationError("The confirmation token payload is invalid")
        if payload.get("tool") != tool or payload.get("digest") != _digest(tool, arguments):
            raise ConfirmationError("The confirmed operation does not match this request")
        expires_at = payload.get("exp")
        if not isinstance(expires_at, int) or expires_at < int(self._now()):
            raise ConfirmationError("The confirmation token has expired; prepare a new plan")
        with self._lock:
            if token in self._used:
                raise ConfirmationError("The confirmation token has already been used")
            self._used.add(token)


__all__ = ["ConfirmationManager"]
