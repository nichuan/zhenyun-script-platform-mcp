"""Domain exceptions exposed as stable, agent-readable error codes."""

from __future__ import annotations

from typing import Any


class ScriptPlatformError(Exception):
    code = "SCRIPT_PLATFORM_ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if retryable is not None:
            self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            result["details"] = self.details
        return result


class ConfigurationError(ScriptPlatformError):
    code = "CONFIGURATION_ERROR"


class AuthenticationError(ScriptPlatformError):
    code = "AUTHENTICATION_ERROR"


class ConfirmationError(ScriptPlatformError):
    code = "CONFIRMATION_REQUIRED"


class NotFoundError(ScriptPlatformError):
    code = "NOT_FOUND"


class VersionConflictError(ScriptPlatformError):
    code = "VERSION_CONFLICT"


class InvalidFixtureError(ScriptPlatformError):
    code = "INVALID_FIXTURE"


class NoValidFixtureError(InvalidFixtureError):
    code = "NO_VALID_FIXTURE"


class AmbiguousLineError(ScriptPlatformError):
    code = "AMBIGUOUS_LINE"


class DebugExecutionError(ScriptPlatformError):
    code = "DEBUG_EXECUTION_ERROR"


class SaveVerificationError(ScriptPlatformError):
    code = "SAVE_VERIFICATION_FAILED"


class AdapterStateError(ScriptPlatformError):
    code = "ADAPTER_STATE_ERROR"
