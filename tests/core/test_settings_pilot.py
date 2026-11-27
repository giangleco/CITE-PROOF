import pytest
from pydantic import ValidationError

from citeproof.core.settings import Settings

_PLACEHOLDER = "CHANGE_ME_x"


def test_pilot_rejects_placeholder_secret() -> None:
    with pytest.raises(ValidationError, match="CITEPROOF_ANTHROPIC_API_KEY"):
        Settings(_env_file=None, env="pilot", anthropic_api_key=_PLACEHOLDER)


def test_pilot_rejects_placeholder_in_urls_and_lists_all_names() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            env="pilot",
            database_url="postgresql+psycopg://u:CHANGE_ME_db_password@db/x",
            eval_database_url="postgresql+psycopg://u:CHANGE_ME_db_password@db/y",
            openai_api_key=_PLACEHOLDER,
        )
    text = str(exc.value)
    for name in (
        "CITEPROOF_DATABASE_URL",
        "CITEPROOF_EVAL_DATABASE_URL",
        "CITEPROOF_OPENAI_API_KEY",
    ):
        assert name in text
    assert "CHANGE_ME_db_password" not in text


def test_pilot_without_placeholders_ok() -> None:
    Settings(_env_file=None, env="pilot", anthropic_api_key="sk-real")


@pytest.mark.parametrize("env", ["local", "test"])
def test_non_pilot_not_checked(env: str) -> None:
    settings = Settings(_env_file=None, env=env, anthropic_api_key=_PLACEHOLDER)  # type: ignore[arg-type]
    assert settings.env == env
