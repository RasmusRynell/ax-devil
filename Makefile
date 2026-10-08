.PHONY: sync lock check format test test-integration run run-dev

TEST_WORKERS ?= auto

sync:
	uv sync

lock:
	uv lock

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run python scripts/check_logging.py
	uv run mypy src/ax_devil tests tools

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest -n $(TEST_WORKERS) --maxprocesses=8 --dist=loadfile

test-integration:
	uv run pytest -m integration

run:
	uv run ax-devil --log-level INFO

run-dev:
	uv run ax-devil --log-level DEBUG
