.PHONY: check

TRACE_VIEWER_PYTHON = tools/trace-viewer/server.py tools/trace-viewer/schemas.py tools/trace-viewer/test_server.py
TRACE_VIEWER_UV = uv run --with 'mcp>=1.28,<2' --with uvicorn

check:
	cd ../.. && uv run ruff format --check $(TRACE_VIEWER_PYTHON)
	cd ../.. && uv run ruff check $(TRACE_VIEWER_PYTHON)
	cd ../.. && $(TRACE_VIEWER_UV) mypy --strict $(TRACE_VIEWER_PYTHON)
	cd ../.. && $(TRACE_VIEWER_UV) pytest tools/trace-viewer/test_server.py
	cd ../.. && node tools/trace-viewer/parser.test.mjs
	cd ../.. && node tools/trace-viewer/agent.test.mjs
