"""Unit tests for RFC 7807 problem details exception hierarchy."""

from app.core.errors import (
    AuthenticationError,
    DatabaseMigrationError,
    ErrorCode,
    GrantLoomError,
    NotFoundError,
    PaymentRequiredError,
    PayloadTooLargeError,
    SecurityGuardError,
    UnsupportedMediaTypeError,
    ValidationError,
)


def test_base_grantloom_error_defaults():
    err = GrantLoomError(detail="Something went wrong")
    payload = err.to_problem_details()

    assert payload["status"] == 500
    assert payload["title"] == "Internal Server Error"
    assert payload["detail"] == "Something went wrong"
    assert payload["code"] == ErrorCode.INTERNAL_SERVER_ERROR.value
    assert payload["type"] == "urn:grantloom:error:internal-server-error"
    assert "instance" not in payload


def test_not_found_error_formatting():
    err = NotFoundError(
        detail="Document doc_987 was not found in registry",
        instance="/api/v1/documents/doc_987",
        extra={"doc_id": "doc_987"},
    )
    payload = err.to_problem_details()

    assert payload["status"] == 404
    assert payload["title"] == "Resource Not Found"
    assert payload["detail"] == "Document doc_987 was not found in registry"
    assert payload["code"] == ErrorCode.DOC_NOT_FOUND.value
    assert payload["type"] == "urn:grantloom:error:doc-not-found"
    assert payload["instance"] == "/api/v1/documents/doc_987"
    assert payload["extra"]["doc_id"] == "doc_987"


def test_auth_and_payment_errors():
    auth_err = AuthenticationError(detail="Missing X-OpenRouter-Key header")
    p1 = auth_err.to_problem_details()
    assert p1["status"] == 401
    assert p1["code"] == ErrorCode.AUTH_UNAUTHORIZED.value

    pay_err = PaymentRequiredError(detail="OpenRouter account balance is zero")
    p2 = pay_err.to_problem_details()
    assert p2["status"] == 402
    assert p2["code"] == ErrorCode.AUTH_PAYMENT_REQUIRED.value


def test_validation_and_media_type_errors():
    val_err = ValidationError(detail="Query field cannot be empty")
    assert val_err.status == 422
    assert val_err.to_problem_details()["status"] == 422

    media_err = UnsupportedMediaTypeError(detail="Only PDF, TXT, CSV, and XLSX are supported")
    assert media_err.status == 415
    assert media_err.to_problem_details()["code"] == ErrorCode.UNSUPPORTED_MEDIA_TYPE.value


def test_database_migration_error():
    mig_err = DatabaseMigrationError(detail="Failed to connect to migration database")
    assert mig_err.status == 500
    assert mig_err.code == ErrorCode.DATABASE_MIGRATION_ERROR
    assert mig_err.to_problem_details()["type"] == "urn:grantloom:error:database-migration-error"


def test_security_and_payload_errors():
    sec_err = SecurityGuardError(detail="Prompt injection pattern detected in input")
    assert sec_err.status == 400
    assert sec_err.code == ErrorCode.SECURITY_INJECTION_DETECTED

    size_err = PayloadTooLargeError(detail="File exceeds maximum allowed size of 25MB")
    assert size_err.status == 413
    assert size_err.code == ErrorCode.PAYLOAD_TOO_LARGE
