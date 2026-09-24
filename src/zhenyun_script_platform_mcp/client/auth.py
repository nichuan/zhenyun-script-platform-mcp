"""Expiry-aware authentication with refresh-token and browser SSO fallback."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..exceptions import AuthenticationError
from .browser_auth import BrowserAuthenticator


@dataclass(frozen=True, slots=True)
class AuthMaterial:
    token: str
    source: str
    obtained_at: str | None = None
    expires_at: float | None = None
    refresh_token: str | None = None
    refresh_expires_at: float | None = None

    def expires_within(self, seconds: int, *, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= (time.time() if now is None else now) + seconds


def _timestamp(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return float(value)
    except ValueError:
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None


def _duration(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed >= 0 else None


def _jwt_exp(token: str) -> float | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padding = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
    except (ValueError, json.JSONDecodeError):
        return None
    value = payload.get("exp") if isinstance(payload, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


class AuthProvider:
    """Reuse valid tokens; refresh or run browser SSO only when the token is absent/expired."""

    def __init__(
        self,
        settings: Settings,
        *,
        refresh_transport: httpx.BaseTransport | None = None,
        browser_login: Callable[..., dict[str, Any]] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._refresh_client = httpx.Client(
            timeout=settings.timeout,
            verify=settings.verify_ssl,
            transport=refresh_transport,
        )
        self._browser_login = browser_login or BrowserAuthenticator(settings).login
        self._now = now
        self._lock = threading.RLock()

    @staticmethod
    def _require_private_file(path: Path, label: str) -> None:
        if os.name == "posix" and path.stat().st_mode & 0o077:
            raise AuthenticationError(
                f"{label} file {path} must not be readable by group or other users"
            )

    @classmethod
    def _read_file(cls, path: Path) -> AuthMaterial | None:
        if not path.is_file():
            return None
        cls._require_private_file(path, "Token")
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthenticationError(f"Token file {path} is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise AuthenticationError(f"Token file {path} must contain a JSON object")
        value = raw.get("access_token") or raw.get("token")
        if not isinstance(value, str) or not value:
            return None
        obtained_at_value = raw.get("obtainedAt") or raw.get("obtained_at")
        obtained_at = obtained_at_value if isinstance(obtained_at_value, str) else None
        obtained_ts = _timestamp(obtained_at)
        expires_at = _timestamp(raw.get("expires_at") or raw.get("expiresAt"))
        expires_in = _duration(raw.get("expires_in") or raw.get("expiresIn"))
        if expires_at is None and obtained_ts is not None and expires_in is not None:
            expires_at = obtained_ts + expires_in
        if expires_at is None:
            expires_at = _jwt_exp(value)
        refresh_expires_at = _timestamp(
            raw.get("refresh_expires_at") or raw.get("refreshExpiresAt")
        )
        refresh_expires_in = _duration(raw.get("refresh_expires_in") or raw.get("refreshExpiresIn"))
        if (
            refresh_expires_at is None
            and obtained_ts is not None
            and refresh_expires_in is not None
        ):
            refresh_expires_at = obtained_ts + refresh_expires_in
        refresh_token = raw.get("refresh_token") or raw.get("refreshToken")
        return AuthMaterial(
            token=value,
            source=str(path),
            obtained_at=obtained_at,
            expires_at=expires_at,
            refresh_token=refresh_token
            if isinstance(refresh_token, str) and refresh_token
            else None,
            refresh_expires_at=refresh_expires_at,
        )

    def _candidates(self) -> tuple[Path, ...]:
        files: list[Path] = []
        if self._settings.token_file is not None:
            files.append(self._settings.token_file)
        files.extend(path for path in self._settings.token_files if path not in files)
        return tuple(files)

    def _current_material(self) -> AuthMaterial | None:
        for path in self._candidates():
            material = self._read_file(path)
            if material:
                return material
        if self._settings.bearer_token:
            return AuthMaterial(
                token=self._settings.bearer_token,
                source="environment-legacy",
                expires_at=_jwt_exp(self._settings.bearer_token),
            )
        return None

    def _read_credentials(self) -> tuple[str, str | None]:
        # Environment values are intentionally resolved first. This makes the
        # service usable on non-macOS systems and avoids touching local
        # credential stores when a complete .env configuration is present.
        username = self._usable_username(self._settings.sso_username)
        environment_password = self._usable_password(self._settings.sso_password)
        if username and environment_password:
            return username, environment_password

        file_password: str | None = None
        for path in (self._settings.account_file, self._settings.credential_file):
            if path is None or not path.is_file():
                continue
            self._require_private_file(path, "Credential")
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise AuthenticationError(f"Credential file {path} is not valid JSON") from exc
            if not isinstance(raw, dict):
                continue
            if not username:
                candidate = raw.get("username") or raw.get("user")
                username = self._usable_username(candidate)
            candidate_password = raw.get("password")
            file_password = self._usable_password(candidate_password) or file_password

        password = environment_password or file_password
        if not password:
            password = self._password_from_keychain(username)
        return username, password

    @staticmethod
    def _usable_username(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        value = value.strip()
        return "" if not value or value.startswith("<") else value

    @staticmethod
    def _usable_password(value: Any) -> str | None:
        if not isinstance(value, str) or not value or value.startswith("<"):
            return None
        # Do not strip passwords: leading/trailing spaces may be intentional.
        return value

    def _password_from_keychain(self, username: str) -> str | None:
        if not username or sys.platform != "darwin":
            return None
        try:
            security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        except OSError:
            return None
        service = self._settings.keychain_service.encode("utf-8")
        account = username.encode("utf-8")
        password_length = ctypes.c_uint32()
        password_data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        security.SecKeychainFindGenericPassword.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        security.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        status = security.SecKeychainFindGenericPassword(
            None,
            len(service),
            service,
            len(account),
            account,
            ctypes.byref(password_length),
            ctypes.byref(password_data),
            ctypes.byref(item),
        )
        try:
            if status != 0 or not password_data or password_length.value == 0:
                return None
            return ctypes.string_at(password_data, password_length.value).decode("utf-8")
        finally:
            if password_data:
                security.SecKeychainItemFreeContent(None, password_data)

    def _write_token(self, payload: dict[str, Any]) -> AuthMaterial:
        path = self._settings.token_file
        if path is None:
            raise AuthenticationError("SCRIPT_PLATFORM_TOKEN_FILE is required for automatic auth")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = self._now()
        output = dict(payload)
        output["obtainedAt"] = datetime.fromtimestamp(now, UTC).isoformat()
        expires_in = _duration(output.get("expires_in") or output.get("expiresIn"))
        if expires_in is not None:
            output["expires_at"] = now + expires_in
        refresh_expires_in = _duration(
            output.get("refresh_expires_in") or output.get("refreshExpiresIn")
        )
        if refresh_expires_in is not None:
            output["refresh_expires_at"] = now + refresh_expires_in
        descriptor, temp_name = tempfile.mkstemp(prefix=".token-", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(output, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            os.chmod(path, 0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        material = self._read_file(path)
        if material is None:  # pragma: no cover - defensive
            raise AuthenticationError("Automatic authentication did not produce a usable token")
        return material

    def _refresh(self, material: AuthMaterial) -> AuthMaterial | None:
        if not material.refresh_token:
            return None
        if material.refresh_expires_at is not None and material.refresh_expires_at <= self._now():
            return None
        try:
            response = self._refresh_client.post(
                self._settings.token_url,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self._settings.client_id,
                    "refresh_token": material.refresh_token,
                },
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
            return None
        if not payload.get("refresh_token"):
            payload["refresh_token"] = material.refresh_token
        if "refresh_expires_at" not in payload and material.refresh_expires_at is not None:
            payload["refresh_expires_at"] = material.refresh_expires_at
        return self._write_token(payload)

    def _login(self) -> AuthMaterial:
        username, password = self._read_credentials()
        payload = self._browser_login(username=username, password=password)
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
            raise AuthenticationError("Automatic browser login did not return an access token")
        return self._write_token(payload)

    def get_material(self) -> AuthMaterial:
        with self._lock:
            material = self._current_material()
            if material and not material.expires_within(
                self._settings.token_refresh_skew_seconds, now=self._now()
            ):
                return material
            if material:
                refreshed = self._refresh(material)
                if refreshed:
                    return refreshed
            return self._login()

    def refresh_after_rejection(self, rejected_token: str | None = None) -> AuthMaterial:
        """Refresh once after a real 401/expired envelope; unknown opaque tokens use this path."""
        with self._lock:
            current = self._current_material()
            if current and rejected_token and current.token != rejected_token:
                return current
            if current:
                refreshed = self._refresh(current)
                if refreshed:
                    return refreshed
            return self._login()

    def get_authorization(self) -> tuple[str, str]:
        material = self.get_material()
        header = (
            material.token
            if self._settings.authorization_scheme == "raw"
            else f"Bearer {material.token}"
        )
        return header, material.token

    def get_bearer_token(self) -> str:
        """Backward-compatible accessor; the value must never be logged."""
        return self.get_material().token

    def get_authorization_header(self) -> str:
        return self.get_authorization()[0]

    def metadata(self) -> dict[str, Any]:
        material = self._current_material()
        automatic_login_configured = bool(
            self._settings.sso_password
            or (
                self._settings.credential_file is not None
                and self._settings.credential_file.is_file()
            )
            or (self._settings.account_file is not None and self._settings.account_file.is_file())
        )
        if not material:
            return {
                "configured": False,
                "automatic_login_configured": automatic_login_configured,
            }
        return {
            "configured": True,
            "source": Path(material.source).name
            if material.source != "environment-legacy"
            else material.source,
            "obtained_at": material.obtained_at,
            "expires_at": (
                datetime.fromtimestamp(material.expires_at, UTC).isoformat()
                if material.expires_at is not None
                else None
            ),
            "expiry_known": material.expires_at is not None,
            "expired": material.expires_within(0, now=self._now()),
            "has_refresh_token": material.refresh_token is not None,
            "authorization_scheme": self._settings.authorization_scheme,
            "automatic_login_configured": automatic_login_configured,
        }

    def close(self) -> None:
        self._refresh_client.close()


__all__ = ["AuthMaterial", "AuthProvider"]
