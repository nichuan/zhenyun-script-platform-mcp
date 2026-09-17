from zhenyun_script_platform_mcp.sanitizer import REDACTED, sanitize, sanitize_text


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
