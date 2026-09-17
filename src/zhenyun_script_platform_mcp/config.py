"""Environment-only configuration and write safety guards."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from .exceptions import ConfigurationError, WriteNotAllowedError

_ENV_DIR = os.getenv("SCRIPT_PLATFORM_ENV_DIR", "").strip()
if _ENV_DIR:
    load_dotenv(Path(_ENV_DIR) / ".env")
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


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str
    bearer_token: str = ""
    cookie: str = ""
    menu_id: str = ""
    timeout: float = 30.0
    verify_ssl: bool = True
    allow_write: bool = False
    allowed_hosts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        base_url = os.getenv("SCRIPT_PLATFORM_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            raise ConfigurationError("SCRIPT_PLATFORM_BASE_URL is required")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigurationError("SCRIPT_PLATFORM_BASE_URL must be an absolute HTTP(S) URL")
        raw_hosts = os.getenv("SCRIPT_PLATFORM_ALLOWED_HOSTS", "")
        allowed_hosts = tuple(host.strip().lower() for host in raw_hosts.split(",") if host.strip())
        try:
            timeout = float(os.getenv("SCRIPT_PLATFORM_TIMEOUT", "30"))
        except ValueError as exc:
            raise ConfigurationError("SCRIPT_PLATFORM_TIMEOUT must be a number") from exc
        if timeout <= 0:
            raise ConfigurationError("SCRIPT_PLATFORM_TIMEOUT must be greater than zero")
        return cls(
            base_url=base_url,
            bearer_token=os.getenv("SCRIPT_PLATFORM_BEARER_TOKEN", "").strip(),
            cookie=os.getenv("SCRIPT_PLATFORM_COOKIE", "").strip(),
            menu_id=os.getenv("SCRIPT_PLATFORM_MENU_ID", "").strip(),
            timeout=timeout,
            verify_ssl=_as_bool(os.getenv("SCRIPT_PLATFORM_VERIFY_SSL"), True),
            allow_write=_as_bool(os.getenv("SCRIPT_PLATFORM_ALLOW_WRITE"), False),
            allowed_hosts=allowed_hosts,
        )

    @property
    def host(self) -> str:
        return (urlparse(self.base_url).hostname or "").lower()

    def assert_write_allowed(self) -> None:
        if not self.allow_write:
            raise WriteNotAllowedError(
                "Writes are disabled. Set SCRIPT_PLATFORM_ALLOW_WRITE=true only after "
                "confirming the target is DEV."
            )
        if self.allowed_hosts and self.host not in self.allowed_hosts:
            raise WriteNotAllowedError(
                f"Host {self.host!r} is not listed in SCRIPT_PLATFORM_ALLOWED_HOSTS",
                details={"host": self.host, "allowed_hosts": list(self.allowed_hosts)},
            )
