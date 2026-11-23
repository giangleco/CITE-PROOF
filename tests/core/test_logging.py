import json
import logging

import pytest

from citeproof.core.logging import ALLOWED_LOG_FIELDS, configure_logging, log_context


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
            }
        )
        == ALLOWED_LOG_FIELDS
    )
