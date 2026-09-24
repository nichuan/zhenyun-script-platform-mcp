import pytest

from zhenyun_script_platform_mcp.sanitizer import (
    REDACTED,
    sanitize,
    sanitize_text,
    validate_source_integrity,
)


def test_recursive_sensitive_key_redaction_is_case_insensitive():
    value = {
        "Authorization": "Bearer secret",
        "nested": [{"jwt-token": "abc", "_token": "row-token", "safe": 1}],
        "Cookie": "JSESSIONID=secret",
        "password": "pw",
    }
    cleaned = sanitize(value)
    assert cleaned["Authorization"] == REDACTED
    assert cleaned["nested"][0]["jwt-token"] == REDACTED
    assert cleaned["nested"][0]["_token"] == REDACTED
    assert cleaned["nested"][0]["safe"] == 1
    assert cleaned["Cookie"] == REDACTED


def test_free_text_redaction_for_logs_and_errors():
    cleaned = sanitize_text("Authorization: Bearer abc.def password=hello")
    assert "abc.def" not in cleaned
    assert "hello" not in cleaned
    assert cleaned.count(REDACTED) == 2


def test_camel_case_and_generic_secret_keys_are_redacted():
    """回归：camelCase / 通用秘密键此前漏脱敏（H3）。"""
    cleaned = sanitize(
        {
            "accessToken": "a",
            "refreshToken": "b",
            "clientSecret": "c",
            "apiKey": "d",
            "userToken": "e",
            "sessionId": "f",
            "token": "g",
            "secret": "h",
            "keep": "visible",
        }
    )
    for key in (
        "accessToken",
        "refreshToken",
        "clientSecret",
        "apiKey",
        "userToken",
        "sessionId",
        "token",
        "secret",
    ):
        assert cleaned[key] == REDACTED, key
    assert cleaned["keep"] == "visible"


def test_embedded_credentials_in_string_values_are_redacted():
    """回归：sanitize() 此前不递归扫描字符串值（H3）。"""
    cleaned = sanitize(
        {
            "msg": "call failed: Authorization: Bearer abc.def.ghi",
            "detail": "upstream returned access_token=xyz789 for tenant",
        }
    )
    assert "abc.def.ghi" not in cleaned["msg"]
    assert "xyz789" not in cleaned["detail"]
    assert REDACTED in cleaned["msg"]
    assert REDACTED in cleaned["detail"]


def test_benign_strings_are_left_intact():
    cleaned = sanitize({"note": "tenant SRM-DEMO processed 12 orders"})
    assert cleaned["note"] == "tenant SRM-DEMO processed 12 orders"


def test_protocol_confirmation_token_is_not_redacted():
    """confirmation_token 是两阶段确认票据，必须原样回传，不能被 _token 后缀误伤。"""
    cleaned = sanitize({"confirmation_token": "abc.def", "accessToken": "x"})
    assert cleaned["confirmation_token"] == "abc.def"
    assert cleaned["accessToken"] == REDACTED


@pytest.mark.parametrize(
    "source",
    [
        "headers:{Authorization:token}",
        'headers:{"Authorization":"Bearer " + token}',
        'const result = {"token":resp.id, "accessToken":appStore.token};',
        "// Authorization is assigned by the runtime\nreturn input;",
        'headers:{Authorization:"Bearer eyJhbGciOiJIUzI1NiJ9.literal"}',
        "const 中文 = true;\r\n\treturn 中文;",
    ],
)
def test_explicit_source_field_preserves_exact_text(source):
    cleaned = sanitize(
        {
            "lines": [
                {
                    "source": source,
                    "saved_test_input": {"_token": "fixture-secret", "openPassword": "pw"},
                }
            ]
        },
        preserve_fields=frozenset({"source"}),
    )

    assert cleaned["lines"][0]["source"] == source
    assert cleaned["lines"][0]["saved_test_input"]["_token"] == REDACTED
    assert cleaned["lines"][0]["saved_test_input"]["openPassword"] == REDACTED


@pytest.mark.parametrize(
    "placeholder",
    ["<REDACTED>", "<<REDACTED:7>>", "__MASKED_ON_READ_VERIFY_BEFORE_DEPLOY__", "<MASKED>"],
)
def test_source_integrity_rejects_redaction_placeholders(placeholder):
    with pytest.raises(ValueError, match="redaction placeholder"):
        validate_source_integrity(f"return {placeholder};")


def test_source_integrity_allows_normal_mask_identifiers():
    validate_source_integrity("const mask = input.mask; return mask;")
