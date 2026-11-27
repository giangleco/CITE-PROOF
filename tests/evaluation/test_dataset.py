"""Đọc bộ dữ liệu JSONL (13 §B.3) và cấu hình biến thể."""

import hashlib
from pathlib import Path

import pytest
import yaml

from citeproof.core.enums import ClaimStatus, ContentLevel, ReferenceStatus, Verifier
from citeproof.core.errors import InputError
from citeproof.core.settings import Settings
from citeproof.evaluation.dataset import load_claims, load_references, load_run_config

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "eval"
CONFIGS = Path(__file__).resolve().parents[2] / "benchmark" / "configs"


def test_dataset_hash_stable() -> None:
    path = ROOT / "mini.jsonl"
    first = load_claims(path)
    second = load_claims(path)
    assert first.sha256 == hashlib.sha256(path.read_bytes()).hexdigest() == second.sha256
    assert first.name == "mini"


def test_mini_dataset_shape() -> None:
    dataset = load_claims(ROOT / "mini.jsonl")
    assert len(dataset.items) == 6
    assert {item.claim_language for item in dataset.items} == {"en", "vi"}
    first = dataset.items[0]
    assert first.label is ClaimStatus.SUPPORTED
    assert first.source_content_level is ContentLevel.FULL_TEXT
    assert first.source_path == "sources/s1.txt"
    # Nguồn đọc sẵn từ tệp cạnh bộ dữ liệu.
    assert first.source_text == (ROOT / "sources" / "s1.txt").read_text(encoding="utf-8")
    assert len({item.source_text for item in dataset.items}) == 2


def test_invalid_line_reports_line_number_only(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    secret = "Câu claim bí mật"
    path.write_text('{"id": "x", "claim_text": "' + secret + '"}\n', encoding="utf-8")
    with pytest.raises(InputError) as info:
        load_claims(path)
    assert info.value.code == "DATASET_INVALID"
    assert "1" in str(info.value)
    assert secret not in str(info.value)


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    line = (ROOT / "mini.jsonl").read_text(encoding="utf-8").splitlines()[0]
    path = tmp_path / "dup.jsonl"
    path.write_text(line + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(InputError) as info:
        load_claims(path)
    assert info.value.code == "DATASET_INVALID"


def test_source_path_must_stay_inside_dataset_dir(tmp_path: Path) -> None:
    line = (ROOT / "mini.jsonl").read_text(encoding="utf-8").splitlines()[0]
    path = tmp_path / "escape.jsonl"
    path.write_text(line.replace("sources/s1.txt", "../../etc/passwd") + "\n", encoding="utf-8")
    with pytest.raises(InputError) as info:
        load_claims(path)
    assert info.value.code == "DATASET_INVALID"


def test_load_references() -> None:
    dataset = load_references(ROOT / "mini_refs.jsonl")
    assert dataset.name == "mini_refs"
    assert [item.expected_status for item in dataset.items] == [
        ReferenceStatus.MATCHED,
        ReferenceStatus.NOT_FOUND_IN_SEARCHED_SOURCES,
        ReferenceStatus.MATCHED,
    ]


@pytest.mark.parametrize(
    ("name", "variant", "cross_lingual"),
    [
        ("v0", Verifier.V0, "X2"),
        ("v1", Verifier.V1, "X2"),
        ("v2-x1", Verifier.V2, "X1"),
        ("v2-x2", Verifier.V2, "X2"),
        ("v3", Verifier.V3, "X2"),
    ],
)
def test_benchmark_configs(name: str, variant: Verifier, cross_lingual: str) -> None:
    config = load_run_config(Settings(), CONFIGS / f"{name}.yaml")
    assert config.variant is variant
    assert config.cross_lingual == cross_lingual
    assert config.demo is None and config.scope is None
    # Hạn mức chi phí luôn ghi rõ trong cấu hình benchmark.
    raw = yaml.safe_load((CONFIGS / f"{name}.yaml").read_text(encoding="utf-8"))
    assert raw["limits"]["max_cost_usd"] == config.limits.max_cost_usd == 5.0


@pytest.mark.parametrize("key", ["demo", "scope", "provider"])
def test_config_rejects_run_bound_keys(tmp_path: Path, key: str) -> None:
    # `provider` do lần chạy tự đóng băng từ settings (spec §5.4), không đặt trong YAML.
    path = tmp_path / "bad.yaml"
    path.write_text(f"variant: V2\n{key}: null\n", encoding="utf-8")
    with pytest.raises(InputError) as info:
        load_run_config(Settings(), path)
    assert info.value.code == "CONFIG_INVALID"


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("variant: V2\nretrieval_kk: 3\n", encoding="utf-8")
    with pytest.raises(InputError) as info:
        load_run_config(Settings(), path)
    assert info.value.code == "CONFIG_INVALID"


def _anthropic() -> Settings:
    from pydantic import SecretStr

    return Settings(anthropic_api_key=SecretStr("k"))


@pytest.mark.parametrize("path", sorted(CONFIGS.glob("*.yaml")), ids=lambda p: p.stem)
def test_all_benchmark_configs_load_when_llm_configured(path: Path) -> None:
    load_run_config(_anthropic(), path)


def test_config_rejects_unpriced_model_when_llm_configured(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    path.write_text("variant: V2\nmodels:\n  verdict.judge: modelo-sin-gia\n", encoding="utf-8")
    with pytest.raises(InputError) as info:
        load_run_config(_anthropic(), path)
    assert info.value.code == "CONFIG_INVALID"
    assert "modelo-sin-gia" in str(info.value)
    load_run_config(Settings(), path)  # chưa cấu hình LLM: không kiểm giá
