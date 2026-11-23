"""Phân cấp lỗi của CiteProof (docs/12 §4.7)."""


class CiteProofError(Exception):
    """Lỗi gốc của mọi lỗi nghiệp vụ."""


class InputError(CiteProofError):
    """Người dùng gửi dữ liệu sai (4xx)."""


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
