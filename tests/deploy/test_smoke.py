"""scripts/smoke.py: mật khẩu không đi qua argv."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "smoke.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_script", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["smoke_script"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def smoke(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load()
    monkeypatch.delenv("CITEPROOF_SMOKE_PASSWORD", raising=False)
    return module


def _capture(monkeypatch: pytest.MonkeyPatch, smoke: ModuleType) -> list[str]:
    seen: list[str] = []

    def fake_login(client: httpx.Client, email: str, password: str) -> None:
        seen.append(password)
        raise RuntimeError("stop")

    monkeypatch.setattr(smoke, "login", fake_login)
    return seen


def test_password_option_removed(smoke: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        smoke.main(["--base-url", "http://x", "--email", "e", "--password", "p"])
    assert exc.value.code == 2
    assert "--password" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        smoke.main(["--help"])
    assert "--password" not in capsys.readouterr().out


def test_password_from_env(smoke: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture(monkeypatch, smoke)
    monkeypatch.setenv("CITEPROOF_SMOKE_PASSWORD", "from-env")
    assert smoke.main(["--base-url", "http://x", "--email", "e"]) == 1
    assert seen == ["from-env"]


def test_password_falls_back_to_getpass(smoke: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture(monkeypatch, smoke)
    monkeypatch.setattr(smoke.getpass, "getpass", lambda *a, **k: "typed")
    assert smoke.main(["--base-url", "http://x", "--email", "e"]) == 1
    assert seen == ["typed"]
