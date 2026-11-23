import pytest

from citeproof.core.settings import Settings, get_settings


def test_settings_read_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITEPROOF_JOB_MAX_ATTEMPTS", "5")
    assert Settings().job_max_attempts == 5


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CITEPROOF_JOB_MAX_ATTEMPTS", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings init kwarg
    assert s.env == "local"
    assert s.log_level == "INFO"
    assert s.job_backoff_base_seconds == 5.0
    assert s.job_lock_timeout_seconds == 900
    assert s.worker_poll_interval_seconds == 1.0
    assert s.llm_prices == {}


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_secure_cookies_default_by_env() -> None:
    assert Settings(env="local").secure_cookies() is False
    assert Settings(env="pilot").secure_cookies() is True
    assert Settings(env="local", cookie_secure=True).secure_cookies() is True
    assert Settings(env="pilot", cookie_secure=False).secure_cookies() is False


def test_web_settings_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.max_upload_bytes == 26_214_400
    assert s.runs_per_day == 20
    assert s.session_ttl_days == 14
    assert s.login_max_failures_per_email == 5
    assert s.llm_provider_name is None
