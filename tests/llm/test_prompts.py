from pathlib import Path

import jinja2
import pytest

from citeproof.llm.prompts import PromptLoader, PromptTemplate

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "prompts"


@pytest.fixture
def tmp_prompts() -> Path:
    return FIXTURES


@pytest.fixture
def loader(tmp_prompts: Path) -> PromptLoader:
    return PromptLoader(tmp_prompts)


def test_system_part_is_verbatim_and_user_is_rendered(loader: PromptLoader) -> None:
    p1 = loader.render("demo", "v1", claim="A")
    p2 = loader.render("demo", "v1", claim="B")
    assert p1.system == p2.system
    assert "{{ not_a_var }}" in p1.system
    assert not p1.system.endswith("\n")
    assert "A" in p1.user
    assert "B" in p2.user
    assert (p1.task, p1.prompt_version) == ("demo", "v1")


def test_load_is_cached(loader: PromptLoader) -> None:
    assert loader.load("demo", "v1") is loader.load("demo", "v1")
    assert isinstance(loader.load("demo", "v1"), PromptTemplate)


def test_missing_variable_raises(loader: PromptLoader) -> None:
    with pytest.raises(jinja2.UndefinedError):
        loader.render("demo", "v1")


def test_missing_file_raises(loader: PromptLoader) -> None:
    with pytest.raises(FileNotFoundError, match="nope"):
        loader.load("demo", "nope")


@pytest.mark.parametrize("content", ["only system\n", "a\n=== USER ===\nb\n=== USER ===\nc\n"])
def test_bad_separator_raises(tmp_path: Path, content: str) -> None:
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "v1.md").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        PromptLoader(tmp_path).load("t", "v1")


@pytest.mark.parametrize("newline", [b"\r\n", b"\r"])
def test_crlf_and_cr_prompt_files_load_like_lf(tmp_path: Path, newline: bytes) -> None:
    lf = b"line one\nline two\n=== USER ===\nClaim: {{ claim }}\nend\n"
    for name, data in (("lf", lf), ("other", lf.replace(b"\n", newline))):
        (tmp_path / name).mkdir()
        (tmp_path / name / "v1.md").write_bytes(data)
    loader = PromptLoader(tmp_path)
    a, b = loader.load("lf", "v1"), loader.load("other", "v1")
    assert b.system == a.system == "line one\nline two"
    assert b.user_template == a.user_template
    assert "\r" not in loader.render("other", "v1", claim="X").user
