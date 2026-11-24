from datetime import UTC, datetime

import pytest

from citeproof.core.enums import LLMTask
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
    assert s.job_lock_timeout_seconds == 120
    assert s.job_heartbeat_seconds == 30.0
    assert s.worker_poll_interval_seconds == 1.0
    assert s.llm_prices == {
        "claude-haiku-5-5": (0.10, 0.50),
        "claude-sonnet-5-5": (2.0, 10.0),
        "claude-opus-5-5": (4.0, 20.0),
    }


_LOCAL_TASKS = {"reference.parse", "claim.extract", "claim.translate", "claim.decompose"}


def test_llm_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CITEPROOF_ANTHROPIC_API_KEY", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_api_key is None
    assert s.anthropic_timeout_seconds == 120.0
    assert s.anthropic_max_retries == 2
    assert s.anthropic_refusal_fallback is True
    assert s.llm_concurrency == 4
    tasks = {t.value for t in LLMTask}
    assert tasks == _LOCAL_TASKS | {
        "verdict.judge",
        "verdict.judge_closed_book",
        "agent.reference_resolver",
        "agent.evidence_investigator",
    }
    assert set(s.llm_models) == set(s.llm_effort) == set(s.llm_max_tokens) == tasks
    for task in tasks:
        local = task in _LOCAL_TASKS
        assert s.llm_models[task] == ("claude-haiku-5-5" if local else "claude-sonnet-5-5")
        assert s.llm_effort[task] == ("low" if local else "high")
    assert s.llm_max_tokens == {
        "reference.parse": 8000,
        "claim.extract": 4000,
        "claim.translate": 2000,
        "claim.decompose": 2000,
        "verdict.judge": 16000,
        "verdict.judge_closed_book": 16000,
        "agent.reference_resolver": 16000,
        "agent.evidence_investigator": 16000,
    }


def test_anthropic_key_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITEPROOF_ANTHROPIC_API_KEY", "sk-ant-secret")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-secret"
    assert "sk-ant-secret" not in repr(s)


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


def test_run_settings_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.run_max_claims == 150
    assert s.run_max_cost_usd == 5.0
    assert s.retrieval_k == 6


def test_default_tables_are_copied_per_instance() -> None:
    first = Settings(_env_file=None)  # type: ignore[call-arg]
    first.llm_models["verdict.judge"] = "x"
    first.llm_prices["claude-haiku-5-5"] = (9.0, 9.0)
    second = Settings(_env_file=None)  # type: ignore[call-arg]
    assert second.llm_models["verdict.judge"] == "claude-sonnet-5-5"
    assert second.llm_prices["claude-haiku-5-5"] == (0.10, 0.50)


def test_pdf_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GROBID_URL", "GROBID_TIMEOUT_SECONDS", "MAX_PAGES"):
        monkeypatch.delenv(f"CITEPROOF_{name}", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.grobid_url == "http://localhost:8070"
    assert s.grobid_timeout_seconds == 300.0
    assert s.max_pages == 300


def test_metadata_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CONTACT_EMAIL",
        "SEMANTIC_SCHOLAR_API_KEY",
        "OPENALEX_API_KEY",
        "METADATA_RATE_LIMITS",
    ):
        monkeypatch.delenv(f"CITEPROOF_{name}", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.contact_email is None  # không gửi email nào khi chưa cấu hình
    assert s.semantic_scholar_api_key is None
    assert s.openalex_api_key is None
    assert s.metadata_rate_limits == {"crossref": 5.0, "openalex": 10.0, "semantic_scholar": 0.3}
    assert s.metadata_breaker_threshold == 3
    assert s.metadata_cache_ttl_days == 30
    assert s.metadata_not_found_ttl_days == 7
    assert s.metadata_timeout_seconds == 15.0
    s.metadata_rate_limits["crossref"] = 1.0
    assert Settings(_env_file=None).metadata_rate_limits["crossref"] == 5.0  # type: ignore[call-arg]


def test_reference_check_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("REFERENCE_CONCURRENCY", "WEB_ARCHIVE_LOOKUP"):
        monkeypatch.delenv(f"CITEPROOF_{name}", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.reference_concurrency == 4
    assert s.web_archive_lookup is False  # QĐ-42: chưa nêu archive.org trên màn hình đồng ý


def test_semantic_scholar_key_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITEPROOF_SEMANTIC_SCHOLAR_API_KEY", "s2-secret")
    monkeypatch.setenv("CITEPROOF_METADATA_RATE_LIMITS", '{"crossref": 1.5}')
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.semantic_scholar_api_key is not None
    assert s.semantic_scholar_api_key.get_secret_value() == "s2-secret"
    assert "s2-secret" not in repr(s)
    assert s.metadata_rate_limits == {"crossref": 1.5}


def test_openalex_key_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CITEPROOF_OPENALEX_API_KEY", "oa-secret")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.openalex_api_key is not None
    assert s.openalex_api_key.get_secret_value() == "oa-secret"
    assert "oa-secret" not in repr(s)
    assert "oa-secret" not in str(s.model_dump())


def test_source_and_consent_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CITEPROOF_MAX_SOURCE_BYTES", raising=False)
    monkeypatch.delenv("CITEPROOF_CONSENT_TEXT_UPDATED_AT", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.max_source_bytes == 31_457_280
    # Màn hình đồng ý thêm nguồn tải bản mở (QĐ-43): người đồng ý trước mốc này đồng ý lại.
    assert s.consent_text_updated_at == datetime(2026, 11, 24, tzinfo=UTC)
