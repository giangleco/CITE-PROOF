"""Danh sách router của giao diện web; `mount_web` gắn tất cả."""

from fastapi import APIRouter

from citeproof.web.routes import auth, dev, documents, report, runs

ROUTERS: list[APIRouter] = [
    auth.router,
    documents.router,
    runs.router,
    report.router,
    dev.router,
]
