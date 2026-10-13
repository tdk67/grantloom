"""RFC 7807 Problem Details exception hierarchy for GrantLoom.

Provides structured, machine-readable error responses adhering to:
https://datatracker.ietf.org/doc/html/rfc7807
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    """Stable domain error codes for GrantLoom."""
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    DATABASE_MIGRATION_ERROR = "DATABASE_MIGRATION_ERROR"
    
    # Auth & Security
    AUTH_UNAUTHORIZED = "AUTH_UNAUTHORIZED"
    AUTH_PAYMENT_REQUIRED = "AUTH_PAYMENT_REQUIRED"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    SECURITY_INJECTION_DETECTED = "SECURITY_INJECTION_DETECTED"
    SECURITY_TAINT_QUARANTINE = "SECURITY_TAINT_QUARANTINE"
    
    # Input validation & File limits
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DOC_NOT_FOUND = "DOC_NOT_FOUND"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    DOCUMENT_PARSE_FAILED = "DOCUMENT_PARSE_FAILED"
    
    # Retrieval & Verification
    RETRIEVAL_NO_MATCH = "RETRIEVAL_NO_MATCH"
    REWRITE_LIMIT_EXHAUSTED = "REWRITE_LIMIT_EXHAUSTED"
    UNRESOLVED_CITATION = "UNRESOLVED_CITATION"
    SQL_GUARD_VIOLATION = "SQL_GUARD_VIOLATION"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    UPSTREAM_SERVICE_ERROR = "UPSTREAM_SERVICE_ERROR"


class GrantLoomError(Exception):
    """Base application exception producing RFC 7807 problem details."""

    default_status: int = 500
    default_title: str = "Internal Server Error"
    default_code: ErrorCode = ErrorCode.INTERNAL_SERVER_ERROR

    def __init__(
        self,
        detail: str,
        code: Optional[ErrorCode] = None,
        status: Optional[int] = None,
        title: Optional[str] = None,
        instance: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code or self.default_code
        self.status = status or self.default_status
        self.title = title or self.default_title
        self.instance = instance
        self.extra = extra or {}

    @property
    def problem_type(self) -> str:
        kebab_code = self.code.value.lower().replace("_", "-")
        return f"urn:grantloom:error:{kebab_code}"

    def to_problem_details(self) -> dict[str, Any]:
        """Serialize exception to RFC 7807 JSON representation."""
        payload: dict[str, Any] = {
            "type": self.problem_type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "code": self.code.value,
        }
        if self.instance:
            payload["instance"] = self.instance
        if self.extra:
            payload["extra"] = self.extra
        return payload


class NotFoundError(GrantLoomError):
    default_status = 404
    default_title = "Resource Not Found"
    default_code = ErrorCode.DOC_NOT_FOUND


class AuthenticationError(GrantLoomError):
    default_status = 401
    default_title = "Unauthorized"
    default_code = ErrorCode.AUTH_UNAUTHORIZED


class PaymentRequiredError(GrantLoomError):
    default_status = 402
    default_title = "Payment Required"
    default_code = ErrorCode.AUTH_PAYMENT_REQUIRED


class ValidationError(GrantLoomError):
    default_status = 422
    default_title = "Validation Error"
    default_code = ErrorCode.VALIDATION_ERROR


class BadRequestError(GrantLoomError):
    default_status = 400
    default_title = "Bad Request"
    default_code = ErrorCode.VALIDATION_ERROR


class PayloadTooLargeError(GrantLoomError):
    default_status = 413
    default_title = "Payload Too Large"
    default_code = ErrorCode.PAYLOAD_TOO_LARGE


class UnsupportedMediaTypeError(GrantLoomError):
    default_status = 415
    default_title = "Unsupported Media Type"
    default_code = ErrorCode.UNSUPPORTED_MEDIA_TYPE


class RateLimitExceededError(GrantLoomError):
    default_status = 429
    default_title = "Too Many Requests"
    default_code = ErrorCode.RATE_LIMIT_EXCEEDED


class SecurityGuardError(GrantLoomError):
    default_status = 400
    default_title = "Security Guard Rejected"
    default_code = ErrorCode.SECURITY_INJECTION_DETECTED


class UpstreamServiceError(GrantLoomError):
    default_status = 502
    default_title = "Bad Gateway"
    default_code = ErrorCode.UPSTREAM_SERVICE_ERROR


class DatabaseMigrationError(GrantLoomError):
    default_status = 500
    default_title = "Database Migration Failure"
    default_code = ErrorCode.DATABASE_MIGRATION_ERROR
