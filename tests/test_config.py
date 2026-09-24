import pytest

from zhenyun_script_platform_mcp import config
from zhenyun_script_platform_mcp.exceptions import ConfigurationError


def test_configuration_root_follows_discovered_dotenv_not_cwd(monkeypatch, tmp_path):
    project = tmp_path / "script-platform"
    dotenv_file = project / ".env"
    dotenv_file.parent.mkdir()
    dotenv_file.write_text("SCRIPT_PLATFORM_BASE_URL=https://example.test\n", encoding="utf-8")

    monkeypatch.delenv("SCRIPT_PLATFORM_ENV_DIR", raising=False)
    monkeypatch.setattr(config, "find_dotenv", lambda: str(dotenv_file))
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: True)

    root = config._load_environment()
    assert root == project.resolve()

    monkeypatch.setattr(config, "_CONFIG_ROOT", root)
    monkeypatch.setenv("SCRIPT_PLATFORM_TOKEN_FILE", ".auth/token.json")
    assert (
        config._configured_path("SCRIPT_PLATFORM_TOKEN_FILE")
        == (project / ".auth/token.json").resolve()
    )
    assert config._resolve_config_dir(".auth", base=project) == (project / ".auth").resolve()


def test_create_verification_delay_must_not_be_negative(monkeypatch):
    monkeypatch.setenv("SCRIPT_PLATFORM_BASE_URL", "https://example.test")
    monkeypatch.setenv("SCRIPT_PLATFORM_CREATE_VERIFY_DELAY_SECONDS", "-1")
    with pytest.raises(ConfigurationError, match="must not be negative"):
        config.Settings.from_env()
