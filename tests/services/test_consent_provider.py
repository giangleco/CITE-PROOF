"""Đồng ý gắn với provider LLM: đổi provider thì phải đồng ý lại (spec §5.4a)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from citeproof.core.settings import Settings
from citeproof.db.models import User
from citeproof.llm.identity import provider_identity
from citeproof.services import auth

SessionFactory = async_sessionmaker[AsyncSession]
PW = "correct horse battery"


def _anthropic(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"llm_provider": "anthropic", "anthropic_api_key": SecretStr("k")}
    )


def _unconfigured(settings: Settings) -> Settings:
    return settings.model_copy(update={"llm_provider": "anthropic", "anthropic_api_key": None})


def _openai(settings: Settings, host: str = "x.example") -> Settings:
    return settings.model_copy(
        update={"llm_provider": "openai_compatible", "openai_base_url": f"https://{host}/v1"}
    )


def _user(settings: Settings, provider: str | None, *, age: timedelta = timedelta(0)) -> User:
    return User(
        consent_llm_at=datetime.now(UTC) - age,
        consent_llm_provider=provider,
    )


def _fresh(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"consent_text_updated_at": datetime.now(UTC) - timedelta(days=1)}
    )


def test_matching_provider_needs_no_consent(settings: Settings) -> None:
    s = _fresh(_anthropic(settings))
    assert not auth.needs_consent(_user(s, "anthropic"), s)


def test_provider_change_requires_consent(settings: Settings) -> None:
    base = _fresh(settings)
    user = _user(base, "anthropic")
    new = _openai(base)
    assert provider_identity(new) == "openai_compatible:x.example"
    assert auth.needs_consent(user, new)
    user.consent_llm_provider = provider_identity(new)
    assert not auth.needs_consent(user, new)


def test_other_host_requires_consent(settings: Settings) -> None:
    s = _fresh(_openai(settings, "a.example"))
    assert auth.needs_consent(_user(s, "openai_compatible:b.example"), s)


@pytest.mark.parametrize("make", [_anthropic, _unconfigured])
def test_null_provider_matches_anthropic_or_unconfigured(settings: Settings, make) -> None:  # type: ignore[no-untyped-def]
    s = _fresh(make(settings))
    assert not auth.needs_consent(_user(s, None), s)


def test_null_provider_requires_consent_for_openai(settings: Settings) -> None:
    s = _fresh(_openai(settings))
    assert auth.needs_consent(_user(s, None), s)


def test_null_consent_time_always_requires(settings: Settings) -> None:
    s = _fresh(_anthropic(settings))
    assert auth.needs_consent(User(consent_llm_at=None, consent_llm_provider="anthropic"), s)


def test_stale_consent_text_still_requires(settings: Settings) -> None:
    s = _anthropic(settings).model_copy(
        update={"consent_text_updated_at": datetime.now(UTC) - timedelta(hours=1)}
    )
    assert auth.needs_consent(_user(s, "anthropic", age=timedelta(days=1)), s)


@pytest.mark.db
async def test_record_consent_stores_provider(
    session_factory: SessionFactory, clean_db: None, settings: Settings
) -> None:
    for current, expected in (
        (_openai(settings), "openai_compatible:x.example"),
        (_anthropic(settings), "anthropic"),
        (_unconfigured(settings), None),
    ):
        async with session_factory() as s:
            user = await auth.create_user(
                s, email=f"{expected}@b.vn", password=PW, display_name="U"
            )
            await auth.record_consent(s, user, current)
            await s.commit()
            await s.refresh(user)
            assert user.consent_llm_provider == expected
