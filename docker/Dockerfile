FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.3 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 citeproof
WORKDIR /app
COPY --from=builder --chown=citeproof:citeproof /app/.venv /app/.venv
COPY --chown=citeproof:citeproof src/ src/
COPY --chown=citeproof:citeproof migrations/ migrations/
COPY --chown=citeproof:citeproof alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# Volume storage được tạo với quyền của thư mục này ở lần gắn đầu tiên.
RUN mkdir -p /app/var/storage && chown -R citeproof:citeproof /app/var
USER citeproof
EXPOSE 8000
CMD ["python", "-m", "citeproof.api"]
