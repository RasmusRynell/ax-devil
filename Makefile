.PHONY: sync lock check format shaders test test-integration run run-dev

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

SHADER_DIR := src/ax_devil/modules/video_player/engine/quick/shaders

shaders:
	for stage in vert frag; do \
		uv run pyside6-qsb --glsl "100 es,120,150,330" --hlsl 50 --msl 12 -b -o $(SHADER_DIR)/overlay.$$stage.qsb $(SHADER_DIR)/overlay.$$stage; \
	done

test:
	uv run pytest -n $(TEST_WORKERS) --maxprocesses=8 --dist=loadfile

test-integration:
	uv run pytest -m integration

run:
	uv run ax-devil --log-level INFO

run-dev:
	uv run ax-devil --log-level DEBUG
