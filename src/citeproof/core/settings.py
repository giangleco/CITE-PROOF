"""Cấu hình ứng dụng, chỉ đọc từ biến môi trường tiền tố CITEPROOF_."""

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    llm_concurrency: int = 4
    # Theo tác vụ (spec §8.2): W1-W4 dùng Haiku, effort thấp; W5, A1, A2 dùng Sonnet, effort cao.
    llm_models: dict[str, str] = {
        "reference.parse": "claude-haiku-5-5",
        "claim.extract": "claude-haiku-5-5",
        "claim.translate": "claude-haiku-5-5",
        "claim.decompose": "claude-haiku-5-5",
        "verdict.judge": "claude-sonnet-5-5",
        "verdict.judge_closed_book": "claude-sonnet-5-5",
        "agent.reference_resolver": "claude-sonnet-5-5",
        "agent.evidence_investigator": "claude-sonnet-5-5",
    }
    llm_effort: dict[str, str] = {
        "reference.parse": "low",
        "claim.extract": "low",
        "claim.translate": "low",
        "claim.decompose": "low",
        "verdict.judge": "high",
        "verdict.judge_closed_book": "high",
        "agent.reference_resolver": "high",
        "agent.evidence_investigator": "high",
    }
    llm_max_tokens: dict[str, int] = {
        "reference.parse": 8000,
        "claim.extract": 4000,
        "claim.translate": 2000,
        "claim.decompose": 2000,
        "verdict.judge": 16000,
        "verdict.judge_closed_book": 16000,
        "agent.reference_resolver": 16000,
        "agent.evidence_investigator": 16000,
    }
    # USD trên 1 triệu token (đầu vào, đầu ra), khóa theo model.
    llm_prices: dict[str, tuple[float, float]] = {
        "claude-haiku-5-5": (0.10, 0.50),
        "claude-sonnet-5-5": (2.0, 10.0),
        "claude-opus-5-5": (4.0, 20.0),
    }

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
    # Người đồng ý trước mốc này phải đồng ý lại (FR-63).
    consent_text_updated_at: datetime = datetime(2026, 11, 23, tzinfo=UTC)
    llm_provider_name: str | None = None
    llm_provider_policy_url: str | None = None
    # IP của reverse proxy được tin X-Forwarded-For (phân tách bằng dấu phẩy); rỗng: không tin ai.
    trusted_proxies: str = "127.0.0.1"

    @model_validator(mode="after")
    def _heartbeat_fits_lease(self) -> Self:
        # Lỡ một nhịp heartbeat (ví dụ DB chậm) vẫn còn hạn lease cho nhịp sau.
        if self.job_heartbeat_seconds * 2 >= self.job_lock_timeout_seconds:
            raise ValueError("job_heartbeat_seconds phải nhỏ hơn nửa job_lock_timeout_seconds")
        return self

    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.env != "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
