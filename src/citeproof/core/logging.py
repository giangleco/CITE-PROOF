"""Logging JSON ra stderr; chỉ cho phép các trường trong allowlist."""

import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

ALLOWED_LOG_FIELDS: frozenset[str] = frozenset(
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

# Chỉ logger của ta và uvicorn được ghi nguyên văn message; logger bên thứ ba
# (vd. httpx ghi cả URL kèm query string) chỉ còn tên logger làm `event`.
_VERBATIM_LOGGERS: tuple[str, ...] = ("citeproof", "uvicorn")
_QUIET_LOGGERS: tuple[str, ...] = ("httpx", "httpcore")

_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("log_context")
_HANDLER_MARK = "_citeproof_handler"


def _is_verbatim(name: str) -> bool:
    return any(name == prefix or name.startswith(f"{prefix}.") for prefix in _VERBATIM_LOGGERS)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "event": record.getMessage() if _is_verbatim(record.name) else record.name,
        }
        for key, value in _context.get({}).items():
            if key in ALLOWED_LOG_FIELDS:
                payload[key] = value
        for key in ALLOWED_LOG_FIELDS:
            if key in record.__dict__:
                payload[key] = record.__dict__[key]
        if record.exc_info and record.exc_info[0] is not None:
            payload.setdefault("error_type", record.exc_info[0].__name__)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    """Cài handler JSON lên root logger; gọi lại sẽ thay handler cũ."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


@contextmanager
def log_context(**fields: str) -> Iterator[None]:
    token = _context.set({**_context.get({}), **fields})
    try:
        yield
    finally:
        _context.reset(token)
