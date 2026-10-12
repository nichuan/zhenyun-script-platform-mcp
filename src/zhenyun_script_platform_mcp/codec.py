"""Script Platform UTF-16BE/Base64 codec."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re

from .exceptions import ScriptPlatformError


class PlatformCodecError(ScriptPlatformError):
    code = "INVALID_PLATFORM_TEXT"


def encode_platform_text(value: str) -> str:
    if not isinstance(value, str):
        raise PlatformCodecError("Platform text must be a string")
    return base64.b64encode(value.encode("utf-16-be")).decode("ascii")


def decode_platform_text(value: str) -> str:
    if not isinstance(value, str):
        raise PlatformCodecError("Encoded platform text must be a string")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PlatformCodecError("Platform text is not valid Base64") from exc
    if raw.startswith(b"\xfe\xff"):
        raw = raw[2:]
    try:
        return raw.decode("utf-16-be")
    except UnicodeDecodeError as exc:
        raise PlatformCodecError("Platform text is not valid UTF-16BE") from exc


def decode_script_content(value: str) -> str:
    """Read script content returned as plain text or historical UTF-16BE/Base64."""
    if not isinstance(value, str):
        raise PlatformCodecError("Script content must be a string")
    if not value:
        return value
    try:
        decoded = decode_platform_text(value).lstrip("\ufeff")
    except PlatformCodecError:
        return value
    raw = base64.b64decode(value)
    if encode_platform_text(decoded) != value and not raw.startswith(b"\xfe\xff"):
        return value
    readable = all(
        char in "\r\n\t" or (ord(char) >= 32 and not 127 <= ord(char) <= 159) for char in decoded
    )
    has_script_structure = bool(
        re.search(r"\bfunction\b|\breturn\b|\brequire\s*\(|=>|module\.exports|[{};\n]", decoded)
    )
    ascii_utf16be = len(raw) >= 2 and raw[::2].count(0) >= max(1, len(raw) // 4)
    return decoded if readable and (has_script_structure or ascii_utf16be) else value


def assert_plain_script_content(value: str) -> None:
    if decode_script_content(value) != value:
        raise PlatformCodecError(
            "Script content must be plain text; the MCP applies UTF-16BE/Base64 encoding"
        )


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
