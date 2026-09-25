"""Module errors — controlled, user-safe messages (no internals leak)."""
from __future__ import annotations

from typing import Optional


class AssessError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    message = "An unexpected error occurred."

    def __init__(self, message: Optional[str] = None, *, detail_log: Optional[str] = None) -> None:
        self.message = message or self.message
        self.detail_log = detail_log or self.message
        super().__init__(self.detail_log)

    def payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


class AuthError(AssessError):
    status_code = 401
    code = "UNAUTHORIZED"
    message = "Authentication required."


class ForbiddenError(AssessError):
    status_code = 403
    code = "FORBIDDEN"
    message = "You do not have access to this resource."


class NotFoundError(AssessError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Resource not found."


class ValidationError(AssessError):
    status_code = 422
    code = "INVALID_REQUEST"
    message = "The request was invalid."


class LLMUnavailableError(AssessError):
    status_code = 503
    code = "LLM_UNAVAILABLE"
    message = ("AI evaluation is unavailable — no LLM provider is configured. "
               "Set OPENROUTER_API_KEY or GROQ_API_KEY.")


class UploadError(AssessError):
    status_code = 422
    code = "UPLOAD_FAILED"
    message = "The upload could not be processed."
