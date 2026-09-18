"""Environment configuration for Script Platform, authentication, and confirmation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from .exceptions import ConfigurationError

_ENV_DIR = os.getenv("SCRIPT_PLATFORM_ENV_DIR", "").strip()
_CONFIG_ROOT = Path(_ENV_DIR).expanduser() if _ENV_DIR else Path.cwd()
if _ENV_DIR:
    load_dotenv(_CONFIG_ROOT / ".env")
else:
    load_dotenv()


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid boolean value: {value!r}")


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else _CONFIG_ROOT / path


def _configured_path(name: str, default: str | None = None) -> Path | None:
    raw = os.getenv(name, default or "").strip()
    return _resolve_path(raw) if raw else None


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str
    # Backward-compatible bootstrap only. Managed authentication prefers token_file.
    bearer_token: str = ""
    cookie: str = ""
    menu_id: str = ""
    timeout: float = 30.0
    verify_ssl: bool = True
    token_file: Path | None = None
    token_files: tuple[Path, ...] = ()
    authorization_scheme: str = "bearer"
    sso_username: str = ""
    sso_password: str = ""
    credential_file: Path | None = None
    account_file: Path | None = None
    keychain_service: str = "zhenyun-script-platform-sso"
    token_url: str = (
        "https://sso.going-link.com/auth/realms/going-link/protocol/openid-connect/token"
    )
    client_id: str = "srm-dev"
    authorize_url: str = (
        "https://sso.going-link.com/auth/realms/going-link/protocol/openid-connect/auth"
    )
    redirect_uri: str = "https://zhenyun.dev.isrm.going-link.com/oauth/login/bykey?nonce=platform"
    chrome_path: Path = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    chrome_profile_dir: Path = Path(".auth/chrome-profile")
    auth_headless: bool = True
    token_refresh_skew_seconds: int = 120
    confirmation_ttl_seconds: int = 600
    default_page_size: int = 20
    max_page_size: int = 100
    text_preview_chars: int = 500

    @classmethod
    def from_env(cls) -> Settings:
        base_url = os.getenv("SCRIPT_PLATFORM_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            raise ConfigurationError("SCRIPT_PLATFORM_BASE_URL is required")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigurationError("SCRIPT_PLATFORM_BASE_URL must be an absolute HTTP(S) URL")

        token_file = _configured_path("SCRIPT_PLATFORM_TOKEN_FILE", ".auth/token.json")
        raw_token_files = os.getenv("SCRIPT_PLATFORM_TOKEN_FILES", "")
        token_files = tuple(
            path
            for item in raw_token_files.split(",")
            if item.strip()
            for path in [_resolve_path(item.strip())]
            if path != token_file
        )
        authorization_scheme = (
            os.getenv("SCRIPT_PLATFORM_AUTHORIZATION_SCHEME", "bearer").strip().lower()
        )
        if authorization_scheme not in {"bearer", "raw"}:
            raise ConfigurationError(
                "SCRIPT_PLATFORM_AUTHORIZATION_SCHEME must be 'bearer' or 'raw'"
            )
        try:
            timeout = float(os.getenv("SCRIPT_PLATFORM_TIMEOUT", "30"))
        except ValueError as exc:
            raise ConfigurationError("SCRIPT_PLATFORM_TIMEOUT must be a number") from exc
        if timeout <= 0:
            raise ConfigurationError("SCRIPT_PLATFORM_TIMEOUT must be greater than zero")

        default_page_size = _positive_int("SCRIPT_PLATFORM_DEFAULT_PAGE_SIZE", 20)
        max_page_size = _positive_int("SCRIPT_PLATFORM_MAX_PAGE_SIZE", 100)
        if default_page_size > max_page_size:
            raise ConfigurationError(
                "SCRIPT_PLATFORM_DEFAULT_PAGE_SIZE must not exceed SCRIPT_PLATFORM_MAX_PAGE_SIZE"
            )
        chrome_path = _configured_path(
            "SCRIPT_PLATFORM_CHROME_PATH",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        chrome_profile_dir = _configured_path(
            "SCRIPT_PLATFORM_CHROME_PROFILE_DIR", ".auth/chrome-profile"
        )
        if chrome_path is None or chrome_profile_dir is None:
            raise ConfigurationError("Chrome executable and profile paths must be configured")

        return cls(
            base_url=base_url,
            bearer_token=os.getenv("SCRIPT_PLATFORM_BEARER_TOKEN", "").strip(),
            cookie=os.getenv("SCRIPT_PLATFORM_COOKIE", "").strip(),
            menu_id=os.getenv("SCRIPT_PLATFORM_MENU_ID", "").strip(),
            timeout=timeout,
            verify_ssl=_as_bool(os.getenv("SCRIPT_PLATFORM_VERIFY_SSL"), True),
            token_file=token_file,
            token_files=token_files,
            authorization_scheme=authorization_scheme,
            sso_username=os.getenv("SCRIPT_PLATFORM_SSO_USERNAME", "").strip(),
            sso_password=os.getenv("SCRIPT_PLATFORM_SSO_PASSWORD", ""),
            credential_file=_configured_path(
                "SCRIPT_PLATFORM_SSO_CREDENTIAL_FILE", ".auth/credentials.json"
            ),
            account_file=_configured_path("SCRIPT_PLATFORM_ACCOUNT_FILE", ".auth/account.json"),
            keychain_service=os.getenv(
                "SCRIPT_PLATFORM_KEYCHAIN_SERVICE", "zhenyun-script-platform-sso"
            ).strip(),
            token_url=os.getenv(
                "SCRIPT_PLATFORM_TOKEN_URL",
                "https://sso.going-link.com/auth/realms/going-link/protocol/openid-connect/token",
            ).strip(),
            client_id=os.getenv("SCRIPT_PLATFORM_CLIENT_ID", "srm-dev").strip(),
            authorize_url=os.getenv(
                "SCRIPT_PLATFORM_AUTHORIZE_URL",
                "https://sso.going-link.com/auth/realms/going-link/protocol/openid-connect/auth",
            ).strip(),
            redirect_uri=os.getenv(
                "SCRIPT_PLATFORM_REDIRECT_URI",
                "https://zhenyun.dev.isrm.going-link.com/oauth/login/bykey?nonce=platform",
            ).strip(),
            chrome_path=chrome_path,
            chrome_profile_dir=chrome_profile_dir,
            auth_headless=_as_bool(os.getenv("SCRIPT_PLATFORM_AUTH_HEADLESS"), True),
            token_refresh_skew_seconds=_positive_int(
                "SCRIPT_PLATFORM_TOKEN_REFRESH_SKEW_SECONDS", 120
            ),
            confirmation_ttl_seconds=_positive_int("SCRIPT_PLATFORM_CONFIRM_TTL_SECONDS", 600),
            default_page_size=default_page_size,
            max_page_size=max_page_size,
            text_preview_chars=_positive_int("SCRIPT_PLATFORM_TEXT_PREVIEW_CHARS", 500),
        )

    @property
    def host(self) -> str:
        return (urlparse(self.base_url).hostname or "").lower()

    @staticmethod
    def assert_tenant(tenant: str) -> None:
        if not tenant.strip():
            raise ValueError("A non-empty tenant is required")
