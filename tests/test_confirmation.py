import pytest

from zhenyun_script_platform_mcp.confirmation import ConfirmationManager
from zhenyun_script_platform_mcp.exceptions import ConfirmationError


def test_confirmation_is_bound_to_exact_arguments_and_single_use():
    manager = ConfirmationManager(ttl_seconds=60, secret=b"x" * 32, now=lambda: 100)
    arguments = {"tenant": "SRM-DEMO", "code": "A", "changes": {"enabled": True}}
    plan = manager.prepare(tool="save", arguments=arguments, preview={"target": "A"})

    assert plan["requires_confirmation"] is True
    assert plan["preview"] == {"target": "A"}
    token = plan["confirmation_token"]
    manager.consume(tool="save", arguments=arguments, token=token)
    with pytest.raises(ConfirmationError, match="already been used"):
        manager.consume(tool="save", arguments=arguments, token=token)


def test_confirmation_rejects_changed_arguments_and_expiry():
    now = [100]
    manager = ConfirmationManager(ttl_seconds=10, secret=b"y" * 32, now=lambda: now[0])
    arguments = {"code": "A", "source": "return 1;"}
    token = manager.prepare(tool="save", arguments=arguments, preview={})["confirmation_token"]

    with pytest.raises(ConfirmationError, match="does not match"):
        manager.consume(
            tool="save",
            arguments={"code": "A", "source": "return 2;"},
            token=token,
        )
    now[0] = 111
    with pytest.raises(ConfirmationError, match="expired"):
        manager.consume(tool="save", arguments=arguments, token=token)
