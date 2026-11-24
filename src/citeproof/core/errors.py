"""Phân cấp lỗi của CiteProof (docs/12 §4.7).

Mỗi lỗi mang một `code` máy đọc được (`09` §4.1); message là câu cho người dùng.
"""


class CiteProofError(Exception):
    """Lỗi gốc của mọi lỗi nghiệp vụ."""

    code: str = "ERROR"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class InputError(CiteProofError):
    """Người dùng gửi dữ liệu sai (4xx)."""

    code = "INVALID_INPUT"


class NotFoundError(CiteProofError):
    """Tài nguyên không có, hoặc thuộc người khác (luôn trả 404)."""

    code = "NOT_FOUND"


class ConflictError(CiteProofError):
    """Xung đột trạng thái, ví dụ lần chạy đang hoạt động (409)."""

    code = "CONFLICT"


class RateLimitedError(CiteProofError):
    """Vượt giới hạn tần suất hoặc hạn mức (429)."""

    code = "RATE_LIMITED"


class AuthError(CiteProofError):
    """Sai thông tin đăng nhập hoặc chưa đăng nhập (401)."""

    code = "UNAUTHENTICATED"


class ParseError(CiteProofError):
    """Không parse được tài liệu hoặc nguồn."""


class ExternalServiceError(CiteProofError):
    """API ngoài lỗi."""


class RetryableError(ExternalServiceError):
    """Timeout, 429, 5xx: thử lại có backoff."""


class LLMOutputError(CiteProofError):
    """Đầu ra LLM sai schema sau số lần thử tối đa."""


class InternalError(CiteProofError):
    """Lỗi lập trình (500, ghi log ERROR)."""


class LeaseLostError(CiteProofError):
    """Lease của job đã mất (hết hạn rồi bị nhận lại): không được ghi kết quả nữa."""

    code = "LEASE_LOST"
