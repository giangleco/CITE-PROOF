"""Kiểm tra tĩnh docker-compose.yml chạy trên máy (không khởi động Docker)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
APP_SERVICES = ("migrate", "app", "worker")


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    result: dict[str, Any] = compose["services"]
    return result


@pytest.mark.parametrize("name", APP_SERVICES)
def test_app_services_load_optional_dotenv(services: dict[str, Any], name: str) -> None:
    assert services[name]["env_file"] == [{"path": ".env", "required": False}]


@pytest.mark.parametrize("name", APP_SERVICES)
def test_container_urls_override_dotenv(services: dict[str, Any], name: str) -> None:
    assert "@db:5432" in services[name]["environment"]["CITEPROOF_DATABASE_URL"]


def test_worker_reaches_grobid_by_service_name(services: dict[str, Any]) -> None:
    assert services["worker"]["environment"]["CITEPROOF_GROBID_URL"] == "http://grobid:8070"


def test_worker_can_reach_host_llm(services: dict[str, Any]) -> None:
    assert "host.docker.internal:host-gateway" in services["worker"]["extra_hosts"]
