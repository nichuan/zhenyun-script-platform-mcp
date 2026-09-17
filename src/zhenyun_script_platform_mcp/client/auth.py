"""Authentication provider extension point."""

from __future__ import annotations

from ..config import Settings


class AuthProvider:
    """Read the bearer token from immutable process configuration."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_bearer_token(self) -> str:
        return self._settings.bearer_token
