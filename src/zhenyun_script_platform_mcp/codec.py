"""Script Platform UTF-16BE/Base64 codec."""

from __future__ import annotations

import base64
import binascii
import hashlib

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


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
