import json

import pytest

from zhenyun_script_platform_mcp.codec import encode_platform_text
from zhenyun_script_platform_mcp.exceptions import InvalidFixtureError
from zhenyun_script_platform_mcp.models import FixtureStatus
from zhenyun_script_platform_mcp.services.fixture import (
    extract_balanced_json,
    parse_encoded_fixture,
)


def test_placeholder_fixture_is_not_returned_as_usable_input():
    parsed = parse_encoded_fixture(encode_platform_text('{"ANYTHING":"string"}'))
    assert parsed.status == FixtureStatus.PLACEHOLDER
    assert parsed.value is None


def test_available_invalid_and_missing_fixture_statuses():
    available = parse_encoded_fixture(encode_platform_text('{"items":[1,2]}'))
    invalid = parse_encoded_fixture(encode_platform_text("not json"))
    missing = parse_encoded_fixture(None)
    assert available.value == {"items": [1, 2]}
    assert available.status == FixtureStatus.AVAILABLE
    assert invalid.status == FixtureStatus.INVALID
    assert missing.status == FixtureStatus.MISSING


def test_balanced_json_handles_nesting_arrays_and_braces_in_strings():
    value = {
        "a": [{"text": 'contains } and [ and escaped " quote'}],
        "tail": True,
    }
    log = f"prefix TASK:input: {json.dumps(value)} suffix {{not relevant"
    result = extract_balanced_json(log, task_code="TASK")
    assert result["found"] is True
    assert result["input"] == value


def test_balanced_json_supports_top_level_array_and_custom_marker():
    result = extract_balanced_json('before marker=[1,{"x":2}] after', marker="marker=")
    assert result["input"] == [1, {"x": 2}]


def test_balanced_json_reports_missing_marker_and_invalid_candidate():
    assert extract_balanced_json("nothing", task_code="TASK")["found"] is False
    with pytest.raises(InvalidFixtureError):
        extract_balanced_json("TASK:input: {broken", task_code="TASK")
