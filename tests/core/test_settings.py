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
