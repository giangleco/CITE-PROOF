.PHONY: check test fmt up migrate

check:
	uv run ruff check . && uv run ruff format --check . && uv run mypy

test:
	uv run pytest

fmt:
	uv run ruff format . && uv run ruff check --fix .

up:
	docker compose up -d

migrate:
	uv run alembic upgrade head
