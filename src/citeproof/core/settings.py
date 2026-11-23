"""Cấu hình ứng dụng, chỉ đọc từ biến môi trường tiền tố CITEPROOF_."""

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    job_lock_timeout_seconds: int = 900
    worker_poll_interval_seconds: float = 1.0
    # USD trên 1 triệu token (đầu vào, đầu ra), khóa theo model.
    llm_prices: dict[str, tuple[float, float]] = {}

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

    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.env != "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
