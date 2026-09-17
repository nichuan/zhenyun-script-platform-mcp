import base64

import pytest

from zhenyun_script_platform_mcp.codec import (
    PlatformCodecError,
    decode_platform_text,
    encode_platform_text,
    source_hash,
)


@pytest.mark.parametrize(
    "text",
    ["", "function run() { return 1; }", "中文脚本：供应商 ✓", "emoji 😀\nnext"],
)
def test_utf16be_base64_round_trip(text):
    encoded = encode_platform_text(text)
    assert base64.b64decode(encoded) == text.encode("utf-16-be")
    assert decode_platform_text(encoded) == text


def test_decode_accepts_legacy_bom():
    encoded = base64.b64encode(b"\xfe\xff" + "中文".encode("utf-16-be")).decode()
    assert decode_platform_text(encoded) == "中文"


@pytest.mark.parametrize("value", ["%%%", "YQ==", None])
def test_invalid_encoded_text_is_rejected(value):
    with pytest.raises(PlatformCodecError):
        decode_platform_text(value)


def test_source_hash_is_stable_sha256():
    assert source_hash("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
