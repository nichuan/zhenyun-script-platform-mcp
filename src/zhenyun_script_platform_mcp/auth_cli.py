"""Local credential bootstrap commands; secrets are never printed or written to .env."""

from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from .client.auth import AuthProvider
from .config import Settings
from .exceptions import AuthenticationError, ScriptPlatformError


def _private_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AuthenticationError(f"Credential file does not exist: {path}")
    AuthProvider._require_private_file(path, "Credential")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthenticationError(f"Credential file {path} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise AuthenticationError(f"Credential file {path} must contain a JSON object")
    return value


def _write_account(path: Path, username: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temp_name = tempfile.mkstemp(prefix=".account-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"username": username}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _write_credentials(path: Path, username: str, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temp_name = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"username": username, "password": password},
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _store_keychain(*, service: str, username: str, password: str) -> None:
    if sys.platform != "darwin":
        raise AuthenticationError("macOS Keychain is only available on macOS")
    try:
        security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        core_foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
    except OSError as exc:  # pragma: no cover - macOS runtime failure
        raise AuthenticationError("macOS Security Framework is unavailable") from exc

    service_bytes = service.encode("utf-8")
    username_bytes = username.encode("utf-8")
    password_bytes = password.encode("utf-8")
    item = ctypes.c_void_p()
    password_length = ctypes.c_uint32()
    password_data = ctypes.c_void_p()

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
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    core_foundation.CFRelease.restype = None
    status = security.SecKeychainFindGenericPassword(
        None,
        len(service_bytes),
        service_bytes,
        len(username_bytes),
        username_bytes,
        ctypes.byref(password_length),
        ctypes.byref(password_data),
        ctypes.byref(item),
    )

    try:
        if status == 0:
            security.SecKeychainItemModifyAttributesAndData.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
            buffer = ctypes.create_string_buffer(password_bytes)
            status = security.SecKeychainItemModifyAttributesAndData(
                item,
                None,
                len(password_bytes),
                ctypes.cast(buffer, ctypes.c_void_p),
            )
        elif status == -25300:  # errSecItemNotFound
            security.SecKeychainAddGenericPassword.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_char_p,
                ctypes.c_uint32,
                ctypes.c_char_p,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
            buffer = ctypes.create_string_buffer(password_bytes)
            status = security.SecKeychainAddGenericPassword(
                None,
                len(service_bytes),
                service_bytes,
                len(username_bytes),
                username_bytes,
                len(password_bytes),
                ctypes.cast(buffer, ctypes.c_void_p),
                ctypes.byref(item),
            )
    finally:
        if password_data:
            security.SecKeychainItemFreeContent(None, password_data)
        if item:
            core_foundation.CFRelease(item)

    if status != 0:
        raise AuthenticationError(f"Unable to store the SSO password in Keychain ({status})")


def _username_from_settings(settings: Settings) -> str:
    if settings.sso_username:
        return settings.sso_username
    if settings.account_file and settings.account_file.is_file():
        raw = _private_json(settings.account_file)
        candidate = raw.get("username") or raw.get("user")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _save_credentials(settings: Settings, username: str, password: str, *, storage: str) -> None:
    username = username.strip()
    if not username or not password or password.startswith("<"):
        raise AuthenticationError("Both username and a non-placeholder password are required")
    if storage == "keychain":
        _store_keychain(
            service=settings.keychain_service,
            username=username,
            password=password,
        )
    else:
        if settings.credential_file is None:
            raise AuthenticationError("SCRIPT_PLATFORM_SSO_CREDENTIAL_FILE is required")
        _write_credentials(settings.credential_file, username, password)
    if settings.account_file is None:
        raise AuthenticationError("SCRIPT_PLATFORM_ACCOUNT_FILE is required")
    _write_account(settings.account_file, username)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Script Platform automatic SSO")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show safe token/authentication metadata")
    login = commands.add_parser("login", help="Acquire or reuse a managed access token")
    login.add_argument(
        "--force",
        action="store_true",
        help="Refresh or log in even if the current token has no known expiry",
    )
    store = commands.add_parser("store-password", help="Store an SSO password securely")
    store.add_argument("--username", help="SSO account; defaults to configured account")
    store.add_argument(
        "--storage",
        choices=("file", "keychain"),
        default="file",
        help="A private 0600 file works unattended; Keychain may require UI unlock",
    )
    imported = commands.add_parser(
        "import-credentials", help="Import a private user/password JSON file"
    )
    imported.add_argument("--from-file", type=Path, required=True)
    imported.add_argument("--storage", choices=("file", "keychain"), default="file")
    token_import = commands.add_parser(
        "import-token", help="Import an existing private token JSON into the managed cache"
    )
    token_import.add_argument("--from-file", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        settings = Settings.from_env()
        if args.command == "status":
            provider = AuthProvider(settings)
            try:
                print(json.dumps(provider.metadata(), ensure_ascii=False, indent=2))
            finally:
                provider.close()
            return

        if args.command == "store-password":
            username = args.username or _username_from_settings(settings)
            password = getpass.getpass("SSO password: ")
            _save_credentials(settings, username, password, storage=args.storage)
            print(f"SSO account saved in protected {args.storage} storage.")
            return

        if args.command == "import-credentials":
            raw = _private_json(args.from_file.expanduser())
            username = raw.get("username") or raw.get("user")
            password = raw.get("password")
            if not isinstance(username, str) or not isinstance(password, str):
                raise AuthenticationError("Credential JSON must contain user/username and password")
            _save_credentials(settings, username, password, storage=args.storage)
            print(f"SSO account imported into protected {args.storage} storage.")
            return

        if args.command == "import-token":
            material = AuthProvider._read_file(args.from_file.expanduser())
            if material is None:
                raise AuthenticationError("Token JSON does not contain token or access_token")
            provider = AuthProvider(settings)
            try:
                provider._write_token({"access_token": material.token})
                print("Existing access token imported into the protected managed cache.")
            finally:
                provider.close()
            return

        provider = AuthProvider(settings)
        try:
            material = (
                provider.refresh_after_rejection(None) if args.force else provider.get_material()
            )
            metadata = provider.metadata()
            metadata["source"] = Path(material.source).name
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
        finally:
            provider.close()
    except ScriptPlatformError as exc:
        raise SystemExit(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
