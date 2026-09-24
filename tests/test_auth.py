import json
import os

import httpx
import pytest

from zhenyun_script_platform_mcp.auth_cli import _save_credentials
from zhenyun_script_platform_mcp.client.auth import AuthProvider
from zhenyun_script_platform_mcp.config import Settings
from zhenyun_script_platform_mcp.exceptions import AuthenticationError


def test_token_file_is_hot_loaded_and_metadata_never_contains_token(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps({"token": "first", "obtainedAt": "2026-09-18T00:00:00Z"}),
        encoding="utf-8",
    )
    os.chmod(token_file, 0o600)
    provider = AuthProvider(
        Settings(
            base_url="https://gateway.dev.example.com",
            token_file=token_file,
        )
    )
    assert provider.get_authorization_header() == "Bearer first"

    token_file.write_text(json.dumps({"access_token": "second"}), encoding="utf-8")
    os.chmod(token_file, 0o600)
    assert provider.get_authorization_header() == "Bearer second"
    assert "second" not in json.dumps(provider.metadata())
    assert provider.metadata()["source"] == "token.json"
    provider.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")
def test_token_file_with_unsafe_permissions_is_rejected(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"token": "secret"}), encoding="utf-8")
    os.chmod(token_file, 0o644)
    provider = AuthProvider(
        Settings(base_url="https://gateway.dev.example.com", token_files=(token_file,))
    )
    with pytest.raises(AuthenticationError, match="must not be readable"):
        provider.get_authorization_header()
    provider.close()


def test_valid_opaque_token_is_reused_without_browser_login(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(json.dumps({"access_token": "opaque-current-token"}), encoding="utf-8")
    os.chmod(token_file, 0o600)

    def browser_login(**_):
        raise AssertionError("browser login must not run for a reusable opaque token")

    provider = AuthProvider(
        Settings(base_url="https://gateway.dev.example.com", token_file=token_file),
        browser_login=browser_login,
    )
    assert provider.get_bearer_token() == "opaque-current-token"
    assert provider.get_bearer_token() == "opaque-current-token"
    provider.close()


def test_expired_token_uses_refresh_token_and_persists_result(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps(
            {
                "access_token": "expired",
                "expires_at": 10,
                "refresh_token": "refresh-secret",
                "refresh_expires_at": 500,
            }
        ),
        encoding="utf-8",
    )
    os.chmod(token_file, 0o600)
    requests = []

    def refresh(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": "refreshed", "expires_in": 120, "refresh_token": "next"},
        )

    provider = AuthProvider(
        Settings(base_url="https://gateway.dev.example.com", token_file=token_file),
        refresh_transport=httpx.MockTransport(refresh),
        browser_login=lambda **_: pytest.fail("browser login must not run"),
        now=lambda: 100,
    )
    assert provider.get_bearer_token() == "refreshed"
    assert len(requests) == 1
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["access_token"] == "refreshed"
    assert saved["expires_at"] == 220
    provider.close()


def test_expired_token_without_refresh_logs_in_once_then_reuses(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps({"access_token": "expired", "expires_at": 10}), encoding="utf-8"
    )
    os.chmod(token_file, 0o600)
    logins = []

    def browser_login(**credentials):
        logins.append(credentials)
        return {"access_token": "browser-token", "expires_in": 3600}

    provider = AuthProvider(
        Settings(
            base_url="https://gateway.dev.example.com",
            token_file=token_file,
            sso_username="user",
            sso_password="password",
        ),
        browser_login=browser_login,
        now=lambda: 100,
    )
    assert provider.get_bearer_token() == "browser-token"
    assert provider.get_bearer_token() == "browser-token"
    assert logins == [{"username": "user", "password": "password"}]
    provider.close()


def test_string_expiry_and_refresh_metadata_are_normalized(tmp_path):
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps(
            {
                "access_token": "opaque-token",
                "obtainedAt": "100",
                "expires_in": "120",
                "refresh_token": "refresh-token",
                "refresh_expires_in": "500",
            }
        ),
        encoding="utf-8",
    )
    os.chmod(token_file, 0o600)
    provider = AuthProvider(
        Settings(base_url="https://gateway.dev.example.com", token_file=token_file),
        browser_login=lambda **_: pytest.fail("browser login must not run"),
        now=lambda: 150,
    )

    metadata = provider.metadata()
    assert metadata["expiry_known"] is True
    assert metadata["has_refresh_token"] is True
    assert metadata["expires_at"] is not None
    provider.close()


def test_browser_login_payload_persists_refresh_token_and_string_expiry(tmp_path):
    token_file = tmp_path / "token.json"
    os.chmod(tmp_path, 0o700)
    provider = AuthProvider(
        Settings(
            base_url="https://gateway.dev.example.com",
            token_file=token_file,
            sso_username="user",
            sso_password="password",
        ),
        browser_login=lambda **_: {
            "access_token": "browser-token-with-enough-length",
            "refresh_token": "browser-refresh-token",
            "expires_in": "120",
            "refresh_expires_in": "500",
        },
        now=lambda: 100,
    )

    assert provider.get_bearer_token() == "browser-token-with-enough-length"
    saved = json.loads(token_file.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "browser-refresh-token"
    assert saved["expires_at"] == 220
    assert saved["refresh_expires_at"] == 600
    assert provider.metadata()["has_refresh_token"] is True
    provider.close()


def test_environment_credentials_take_priority_over_files_and_keychain(tmp_path, monkeypatch):
    account_file = tmp_path / "account.json"
    credential_file = tmp_path / "credentials.json"
    account_file.write_text(json.dumps({"username": "file-user"}), encoding="utf-8")
    credential_file.write_text(
        json.dumps({"username": "file-user", "password": "file-password"}),
        encoding="utf-8",
    )
    os.chmod(account_file, 0o600)
    os.chmod(credential_file, 0o600)
    provider = AuthProvider(
        Settings(
            base_url="https://gateway.dev.example.com",
            sso_username="env-user",
            sso_password="env-password",
            account_file=account_file,
            credential_file=credential_file,
        )
    )
    monkeypatch.setattr(
        provider,
        "_password_from_keychain",
        lambda _username: pytest.fail(
            "Keychain must not be consulted for complete env credentials"
        ),
    )

    assert provider._read_credentials() == ("env-user", "env-password")
    provider.close()


def test_missing_environment_password_falls_back_to_keychain(monkeypatch):
    provider = AuthProvider(
        Settings(
            base_url="https://gateway.dev.example.com",
            sso_username="env-user",
        )
    )
    monkeypatch.setattr(
        provider, "_password_from_keychain", lambda username: f"keychain-{username}"
    )

    assert provider._read_credentials() == ("env-user", "keychain-env-user")
    provider.close()


def test_store_password_creates_private_unattended_credential_file(tmp_path):
    credential_file = tmp_path / "auth" / "credentials.json"
    account_file = tmp_path / "auth" / "account.json"
    settings = Settings(
        base_url="https://gateway.dev.example.com",
        credential_file=credential_file,
        account_file=account_file,
    )
    _save_credentials(settings, "user", "password", storage="file")

    assert json.loads(credential_file.read_text(encoding="utf-8")) == {
        "username": "user",
        "password": "password",
    }
    assert json.loads(account_file.read_text(encoding="utf-8")) == {"username": "user"}
    if os.name == "posix":
        assert credential_file.stat().st_mode & 0o077 == 0
        assert account_file.stat().st_mode & 0o077 == 0


def test_store_password_rejects_placeholder(tmp_path):
    settings = Settings(
        base_url="https://gateway.dev.example.com",
        credential_file=tmp_path / "credentials.json",
        account_file=tmp_path / "account.json",
    )
    with pytest.raises(AuthenticationError, match="non-placeholder"):
        _save_credentials(settings, "user", "<password>", storage="file")
