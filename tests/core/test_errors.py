from citeproof.core.errors import (
    AuthError,
    CiteProofError,
    ConflictError,
    InputError,
    NotFoundError,
    RateLimitedError,
)


def test_error_codes() -> None:
    assert CiteProofError("x").code == "ERROR"
    assert InputError("x").code == "INVALID_INPUT"
    assert InputError("x", code="FILE_TOO_LARGE").code == "FILE_TOO_LARGE"
    assert NotFoundError().code == "NOT_FOUND"
    assert ConflictError().code == "CONFLICT"
    assert RateLimitedError().code == "RATE_LIMITED"
    assert AuthError().code == "UNAUTHENTICATED"
    assert str(InputError("thông báo", code="X")) == "thông báo"


def test_new_errors_are_citeproof_errors() -> None:
    for cls in (NotFoundError, ConflictError, RateLimitedError, AuthError):
        assert issubclass(cls, CiteProofError)
