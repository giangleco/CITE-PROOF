"""Cấu hình ứng dụng, chỉ đọc từ biến môi trường tiền tố CITEPROOF_."""

from functools import lru_cache
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
