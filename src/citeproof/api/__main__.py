"""`python -m citeproof.api`: chạy API với logging JSON."""

import uvicorn

from citeproof.api.app import create_app
from citeproof.core.logging import configure_logging
from citeproof.core.settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    # log_config=None: log của uvicorn đi qua handler JSON ở root logger.
    # proxy_headers=False: app tự xử lý X-Forwarded-For theo CITEPROOF_TRUSTED_PROXIES.
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",
        port=8000,
        log_config=None,
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
