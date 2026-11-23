import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest

from citeproof.core.logging import ALLOWED_LOG_FIELDS, configure_logging, log_context
from citeproof.integrations.http_safe import SafeHttpClient


def _last_record(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    rec: dict[str, object] = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    return rec


def test_log_line_is_json_with_required_fields(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    with log_context(run_id="r1"):
        logging.getLogger("citeproof.test").info("stage_started")
    rec = _last_record(capsys)
    assert rec["event"] == "stage_started"
    assert rec["run_id"] == "r1"
    assert {"timestamp", "level", "module"} <= rec.keys()


def test_non_allowlisted_extra_is_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    logging.getLogger("citeproof.test").info("x", extra={"text": "bản thảo bí mật", "stage": "S1"})
    rec = _last_record(capsys)
    assert "text" not in rec
    assert rec["stage"] == "S1"


def test_configure_logging_is_idempotent(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    configure_logging("INFO")
    logging.getLogger("citeproof.test").info("once")
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 1


def test_context_resets_after_exit(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    with log_context(run_id="r1"):
        pass
    logging.getLogger("citeproof.test").info("after")
    assert "run_id" not in _last_record(capsys)


def test_non_allowlisted_context_is_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    with log_context(secret="x", job_id="j1"):
        logging.getLogger("citeproof.test").info("m")
    rec = _last_record(capsys)
    assert "secret" not in rec
    assert rec["job_id"] == "j1"


def test_allowlist_contents() -> None:
    assert (
        frozenset(
            {
                "run_id",
                "document_id",
                "job_id",
                "stage",
                "worker_id",
                "task",
                "model",
                "status",
                "duration_ms",
                "error_type",
                "cache_key",
            }
        )
        == ALLOWED_LOG_FIELDS
    )


class _Body(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"ok"


async def _public(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


async def test_http_client_logs_never_leak_url(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    client = SafeHttpClient(
        resolver=_public,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=_Body())),
    )
    resp = await client.get("http://example.org/hidden-path?token=SECRET&q=private")
    assert resp.status_code == 200
    err = capsys.readouterr().err
    assert "SECRET" not in err
    assert "private" not in err
    assert "hidden-path" not in err


def test_configure_logging_quiets_http_libraries() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() == logging.WARNING


@pytest.mark.parametrize("name", ["thirdparty.lib", "citeprooflib", "uvicornx", "root"])
def test_foreign_logger_message_is_dropped(capsys: pytest.CaptureFixture[str], name: str) -> None:
    configure_logging("INFO")
    logging.getLogger(name).warning("GET http://x/?token=%s", "SECRET")
    err = capsys.readouterr().err
    assert "SECRET" not in err
    assert json.loads(err.strip().splitlines()[-1])["event"] == name


@pytest.mark.parametrize("name", ["citeproof", "citeproof.x", "uvicorn", "uvicorn.error"])
def test_own_and_uvicorn_messages_are_verbatim(
    capsys: pytest.CaptureFixture[str], name: str
) -> None:
    configure_logging("INFO")
    logging.getLogger(name).info("Started server process")
    assert _last_record(capsys)["event"] == "Started server process"
