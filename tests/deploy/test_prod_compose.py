"""Kiểm tra tĩnh cấu hình triển khai pilot (không khởi động Docker)."""

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from citeproof.core.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ("scripts/backup.sh", "scripts/restore.sh")


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    result: dict[str, Any] = compose["services"]
    return result


def test_db_and_grobid_not_published(services: dict[str, Any]) -> None:
    for name in ("db", "grobid", "app", "worker"):
        assert "ports" not in services[name], name


def test_caddy_publishes_80_443_only(services: dict[str, Any]) -> None:
    assert sorted(services["caddy"]["ports"]) == ["443:443", "80:80"]
    assert {"caddy_data", "caddy_config"} <= {
        v.split(":")[0] for v in services["caddy"]["volumes"] if not v.startswith(".")
    }


def test_trusted_proxy_matches_caddy_address(services: dict[str, Any]) -> None:
    address = services["caddy"]["networks"]["citeproof"]["ipv4_address"]
    assert address == "172.30.0.10"
    assert services["app"]["environment"]["CITEPROOF_TRUSTED_PROXIES"] == address
    assert services["app"]["environment"]["CITEPROOF_ENV"] == "pilot"
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    assert compose["networks"]["citeproof"]["ipam"]["config"][0]["subnet"] == "172.30.0.0/24"


def test_services_restart_policy(services: dict[str, Any]) -> None:
    for name in ("caddy", "app", "worker", "grobid", "db", "backup"):
        assert services[name]["restart"] == "unless-stopped", name
    assert services["grobid"]["mem_limit"] == "4g"
    for name in ("app", "worker", "migrate"):
        assert services[name]["env_file"] in (".env.production", [".env.production"]), name


def test_backup_mounts_storage_read_only(services: dict[str, Any]) -> None:
    volumes = services["backup"]["volumes"]
    assert any(v.startswith("citeproof_storage:") and v.endswith(":ro") for v in volumes)
    assert any(v.startswith("./backups:") for v in volumes)
    assert services["backup"]["image"] == "pgvector/pgvector:pg17"


def test_db_mounts_init_dir_for_eval_database(services: dict[str, Any]) -> None:
    assert any("./docker/db/init" in v for v in services["db"]["volumes"])


def test_env_example_lists_every_setting() -> None:
    for name in (".env.example", ".env.production.example"):
        text = (ROOT / name).read_text()
        missing = [
            f"CITEPROOF_{field.upper()}"
            for field in Settings.model_fields
            if f"CITEPROOF_{field.upper()}=" not in text
        ]
        assert not missing, (name, missing)


def test_shell_scripts_parse() -> None:
    for script in SCRIPTS:
        result = subprocess.run(["bash", "-n", str(ROOT / script)], capture_output=True)
        assert result.returncode == 0, (script, result.stderr)


def test_restore_waits_for_db_health() -> None:
    text = (ROOT / "scripts/restore.sh").read_text()
    assert "up -d --wait db" in text


def test_backup_files_are_private() -> None:
    lines = (ROOT / "scripts/backup.sh").read_text().splitlines()
    assert "umask 077" in lines


def test_backup_storage_runs_before_db_dumps_and_eval_is_optional() -> None:
    text = (ROOT / "scripts/backup.sh").read_text()
    assert "set -euo pipefail" in text
    assert text.index("tar czf") < text.index("pg_dump -Fc")
    assert "select 1 from pg_database where datname='$database'" in text


def test_backup_loop_logs_failure_instead_of_hiding_it(services: dict[str, Any]) -> None:
    command = services["backup"]["command"][0]
    assert "/scripts/backup.sh || echo" in command
    assert "BACKUP FAILED" in command
    assert "|| true" not in command
    assert "PGPASSWORD" not in command
