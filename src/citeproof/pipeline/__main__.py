"""`python -m citeproof.pipeline`: chạy một worker.

Dùng `build_registry` ở mọi môi trường; ở local mỗi stage được bọc bởi lớp thử
nghiệm `pipeline.demo`. Client dùng chung (HTTP, LLM) mở một lần và đóng khi dừng.
"""

import asyncio
import os
import signal
import socket

from citeproof.core.logging import configure_logging
from citeproof.core.settings import Settings, get_settings
from citeproof.db.session import make_engine, make_session_factory
from citeproof.pipeline.deps import open_deps
from citeproof.pipeline.registry import build_registry
from citeproof.pipeline.worker import run_worker


async def _serve(settings: Settings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    try:
        async with open_deps(settings, session_factory) as deps:
            await run_worker(
                session_factory,
                build_registry(deps),
                f"{socket.gethostname()}:{os.getpid()}",
                settings,
                stop,
            )
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(_serve(settings))


if __name__ == "__main__":
    main()
