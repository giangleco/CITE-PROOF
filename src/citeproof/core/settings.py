"""Cấu hình ứng dụng, chỉ đọc từ biến môi trường tiền tố CITEPROOF_."""

import logging
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from citeproof.core import defaults

logger = logging.getLogger(__name__)


def base_url_authority(base_url: str) -> str:
    """`host[:port]` chữ thường của base URL; ValueError khi URL không có host."""
    try:
        parts = urlsplit(base_url.strip())
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"base_url không hợp lệ: {base_url!r}") from exc
    if not host:
        raise ValueError(f"base_url thiếu host (cần dạng https://host/...): {base_url!r}")
    if ":" in host:  # IPv6 literal: hostname bỏ dấu ngoặc, thêm lại để không nhập nhằng với port
        host = f"[{host}]"
    default_port = {"https": 443, "http": 80}.get(parts.scheme.lower())
    return f"{host}:{port}" if port and port != default_port else host


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CITEPROOF_", env_file=".env", extra="ignore")

    env: Literal["local", "test", "pilot"] = "local"
    database_url: str = "postgresql+psycopg://citeproof:citeproof@localhost:5432/citeproof"
    eval_database_url: str = (
        "postgresql+psycopg://citeproof:citeproof@localhost:5432/citeproof_eval"
    )
    log_level: str = "INFO"
    job_max_attempts: int = 3
    job_backoff_base_seconds: float = 5.0
    # Độ dài lease của một job; worker gia hạn mỗi `job_heartbeat_seconds` khi stage còn chạy.
    job_lock_timeout_seconds: int = 120
    job_heartbeat_seconds: float = 30.0
    worker_poll_interval_seconds: float = 1.0

    # Truy cập LLM (ADR-006, spec §8). Không có khóa: mọi bước LLM giảm cấp tất định.
    anthropic_api_key: SecretStr | None = None
    anthropic_timeout_seconds: float = 120.0
    anthropic_max_retries: int = 2
    # Worker gọi Sonnet/Opus bị từ chối thì server chạy lại trên model dự phòng.
    anthropic_refusal_fallback: bool = True
    # Provider LLM: `anthropic` (mặc định) hoặc máy chủ tương thích OpenAI (OpenAI, Gemini,
    # OpenRouter, Ollama...); chỉ một provider mỗi lần triển khai.
    llm_provider: Literal["anthropic", "openai_compatible"] = "anthropic"
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    openai_timeout_seconds: float = 600.0
    openai_max_retries: int = 2
    openai_structured_output: Literal["json_schema", "json_object", "none"] = "json_schema"
    openai_send_reasoning_effort: bool = False
    llm_concurrency: int = 4
    # Theo tác vụ (spec §8.2); mặc định ở `core.defaults`, dùng chung với `RunConfig`.
    llm_models: dict[str, str] = Field(default_factory=lambda: dict(defaults.LLM_MODELS))
    llm_effort: dict[str, str] = Field(default_factory=lambda: dict(defaults.LLM_EFFORT))
    llm_max_tokens: dict[str, int] = Field(default_factory=lambda: dict(defaults.LLM_MAX_TOKENS))
    # USD trên 1 triệu token (đầu vào, đầu ra), khóa theo model.
    llm_prices: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: dict(defaults.LLM_PRICES)
    )
    # Giới hạn một lần chạy và số chunk truy xuất; được đóng băng vào `RunConfig` khi tạo run.
    run_max_claims: int = defaults.RUN_MAX_CLAIMS
    run_max_cost_usd: float = defaults.RUN_MAX_COST_USD
    retrieval_k: int = defaults.RETRIEVAL_K

    # Parse PDF (ADR-004): GROBID là chính; URL rỗng thì chỉ dùng pypdfium2 dự phòng.
    grobid_url: str = "http://localhost:8070"
    grobid_timeout_seconds: float = 300.0
    # Bài dài hơn bị từ chối ở S1 (`PAGE_LIMIT_EXCEEDED`).
    max_pages: int = 300
    # PDF nguồn dài hơn không được parse ở S6 (không có chunk, S8 → `SOURCE_PARSE_FAILED`).
    max_source_pages: int = 200
    # Bản mở tải ở S5 (30 MB): lớn hơn thì bỏ qua bản đó. Chỉ client tải nguồn dùng giới hạn
    # này; client kiểm tra URL của tài liệu web giữ giới hạn nhỏ mặc định.
    max_source_bytes: int = 31_457_280

    # Tra cứu metadata ở S3 (spec §7.3, QĐ-06). `contact_email` là email của người vận hành cho
    # "polite pool" (User-Agent và tham số `mailto` của OpenAlex), không bao giờ là email người
    # dùng; None: không gửi email nào. Unpaywall (S5) chỉ được gọi khi có email này.
    contact_email: str | None = None
    semantic_scholar_api_key: SecretStr | None = None
    # Không có khóa, OpenAlex chia một hạn mức nhỏ mỗi ngày cho mọi client cùng IP.
    openalex_api_key: SecretStr | None = None
    # Số yêu cầu mỗi giây theo nguồn; nguồn không có trong bảng dùng mặc định ở `core.defaults`.
    metadata_rate_limits: dict[str, float] = Field(
        default_factory=lambda: dict(defaults.METADATA_RATE_LIMITS)
    )
    # Số lỗi liên tiếp của một nguồn trong một lần chạy trước khi ngắt mạch nguồn đó.
    metadata_breaker_threshold: int = 3
    metadata_cache_ttl_days: int = 30
    metadata_not_found_ttl_days: int = 7
    metadata_timeout_seconds: float = 15.0
    # Số tài liệu tham khảo S3 kiểm tra song song trong một lần chạy.
    reference_concurrency: int = 4
    # Tra bản lưu trữ archive.org cho tài liệu web (FR-15); tắt vì màn hình đồng ý chưa nêu
    # archive.org (QĐ-42).
    web_archive_lookup: bool = False

    # Giao diện web (spec web UI §7.10). Các giới hạn [ĐX] chốt sau SPK-05.
    storage_dir: Path = Path("var/storage")
    max_upload_bytes: int = 26_214_400
    max_text_chars: int = 50_000
    max_reference_chars: int = 20_000
    session_ttl_days: int = 14
    runs_per_day: int = 20
    # None: bật Secure cho cookie ở mọi môi trường trừ local.
    cookie_secure: bool | None = None
    login_max_failures_per_email: int = 5
    login_max_failures_per_ip: int = 20
    login_window_minutes: int = 15
    # Người đồng ý trước mốc này phải đồng ý lại (FR-63); 24/11/2026: thêm nguồn tải bản mở
    # (QĐ-43).
    consent_text_updated_at: datetime = datetime(2026, 11, 24, tzinfo=UTC)
    llm_provider_name: str | None = None
    llm_provider_policy_url: str | None = None
    # IP của reverse proxy được tin X-Forwarded-For (phân tách bằng dấu phẩy); rỗng: không tin ai.
    trusted_proxies: str = "127.0.0.1"
    # Giới hạn mỗi IP mỗi phút (trong tiến trình, một process app); upload có hạn riêng chặt hơn.
    rate_limit_per_minute: int = 120
    rate_limit_uploads_per_minute: int = 10

    @model_validator(mode="after")
    def _heartbeat_fits_lease(self) -> Self:
        # Lỡ một nhịp heartbeat (ví dụ DB chậm) vẫn còn hạn lease cho nhịp sau.
        if self.job_heartbeat_seconds * 2 >= self.job_lock_timeout_seconds:
            raise ValueError("job_heartbeat_seconds phải nhỏ hơn nửa job_lock_timeout_seconds")
        return self

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "anthropic":
            key = self.anthropic_api_key
            return key is not None and bool(key.get_secret_value().strip())
        return bool(self.openai_base_url and self.openai_base_url.strip())

    @model_validator(mode="after")
    def _llm_provider_consistent(self) -> Self:
        if self.llm_provider != "openai_compatible":
            return self
        if self.openai_base_url:
            try:
                base_url_authority(self.openai_base_url)
            except ValueError as exc:
                raise ValueError(f"openai_base_url: {exc}") from exc
        if not self.llm_configured:
            return self
        if dict(self.llm_models) == dict(defaults.LLM_MODELS):
            raise ValueError(
                "llm_provider=openai_compatible cần đặt CITEPROOF_LLM_MODELS: "
                "tên model mặc định chỉ dành cho Anthropic"
            )
        self._require_tasks()
        self._require_prices()
        if not self.llm_provider_name:
            logger.warning(
                "Chưa đặt CITEPROOF_LLM_PROVIDER_NAME: màn hình đồng ý sẽ không nêu "
                "tên nhà cung cấp LLM"
            )
        return self

    @model_validator(mode="after")
    def _anthropic_prices(self) -> Self:
        if self.llm_provider == "anthropic" and self.llm_configured:
            self._require_tasks()
            self._require_prices()
        return self

    def _require_tasks(self) -> None:
        missing = sorted(set(defaults.LLM_MODELS) - set(self.llm_models))
        if missing:
            raise ValueError("CITEPROOF_LLM_MODELS thiếu model cho tác vụ: " + ", ".join(missing))

    def _require_prices(self) -> None:
        missing = sorted({m for m in self.llm_models.values() if m not in self.llm_prices})
        if missing:
            raise ValueError(
                "thiếu giá trong CITEPROOF_LLM_PRICES cho model: " + ", ".join(missing)
            )

    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.env != "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
