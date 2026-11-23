"""`python -m citeproof.pipeline`: chạy một worker với các stage noop (M1)."""

import asyncio
import os
import signal
import socket

from citeproof.core.logging import configure_logging
from citeproof.core.settings import Settings, get_settings
from citeproof.db.session import make_engine, make_session_factory
from citeproof.pipeline.stages import StageRegistry
from citeproof.pipeline.worker import run_worker


async def _serve(settings: Settings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    engine = make_engine(settings.database_url)
    try:
        await run_worker(
            make_session_factory(engine),
            StageRegistry.noop(),
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
