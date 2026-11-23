import pytest

from citeproof.core.settings import Settings


@pytest.fixture
def settings(database_url: str, tmp_path: object) -> Settings:
    return Settings(database_url=database_url, env="local", storage_dir=tmp_path)  # type: ignore[arg-type]
