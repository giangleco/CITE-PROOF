.PHONY: check test fmt up migrate eval eval-replay

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

# Đánh giá trên CITEPROOF_EVAL_DATABASE_URL (spec §11). `eval` gọi LLM thật (cần
# CITEPROOF_ANTHROPIC_API_KEY) và ghi cache; `eval-replay` phát lại cache đó, không gọi LLM.
EVAL_ARGS = run --dataset tests/fixtures/eval/mini.jsonl --config benchmark/configs/v2-x2.yaml

eval:
	uv run python -m citeproof.evaluation $(EVAL_ARGS) --mode read_write

eval-replay:
	uv run python -m citeproof.evaluation $(EVAL_ARGS) --mode replay
