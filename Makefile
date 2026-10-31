.PHONY: sync lock check check-fast format shaders test test-failed test-integration run run-dev

TEST_WORKERS ?= auto

sync:
	uv sync

lock:
	uv lock

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run python scripts/check_logging.py
	uv run python scripts/check_architecture.py
	uv run mypy src/ax_devil tests tools

check-fast:
	uv run python scripts/check_fast.py
	uv run python scripts/check_architecture.py
	uv run mypy src/ax_devil tests tools

format:
	uv run ruff check --fix .
	uv run ruff format .

SHADER_DIR := src/ax_devil/modules/video_player/engine/quick/shaders

shaders:
	for stage in vert frag; do \
		uv run pyside6-qsb --glsl "100 es,120,150,330" --hlsl 50 --msl 12 -b -o $(SHADER_DIR)/overlay.$$stage.qsb $(SHADER_DIR)/overlay.$$stage; \
	done

test:
	uv run pytest -n $(TEST_WORKERS) --maxprocesses=8 --dist=loadfile

test-failed:
	uv run pytest --lf -x --lfnf=none || test $$? -eq 5

test-integration:
	uv run pytest -m integration

run:
	uv run ax-devil --log-level INFO

run-dev:
	uv run ax-devil --log-level DEBUG
