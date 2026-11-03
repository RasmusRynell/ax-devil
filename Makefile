.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
CARGO := scripts/cargo
ISOLATED := scripts/isolated
PORT ?= 7330
UI_PORT ?= 7331
TEST ?=
ROOT_FILES := desktop/*.cjs scripts/*.cjs vite.config.ts package.json pnpm-workspace.yaml
.PHONY: help web desktop build check check-fast format package tests tests-frontend tests-backend tests-integration \
	tests-desktop bench bench-export bench-present bench-tables bench-browser bench-browser-smoke _types _frontend-build _static _licenses _dev _dev-rust

help:
	@printf '%s\n' \
	  'Commands:' \
	  '  web         Start website development with frontend and Rust reload' \
	  '  desktop     Start desktop development with frontend and Rust reload' \
	  '  build       Build the release app with the frontend embedded; do not launch' \
	  '  package     Package the desktop app: make package OS=linux|win|mac FFMPEG_DIR=<shared FFmpeg>' \
	  '  tests       Run all test suites' \
	  '  check       Run every static check and all test suites' \
	  '  bench       Run the Rust benchmarks (sample item encode); numbers on stdout' \
	  '  bench-export  Time export (1080p, 4K) through the real render page, on SwiftShader and the GPU when it can render there' \
	  '  bench-present  Time the frontend presentation path in plain Node (AX_BENCH_SIZES, _CATALOGS, _MODES, _REPEAT)' \
	  '  bench-tables  Time the per-frame work of the objects list (filter, search, sort, column fit) in plain Node (AX_BENCH_SIZES, _RUNS)' \
	  '  bench-browser  Time the real renderer in Electron (SwiftShader; BENCH_DISPLAY=real for the local GPU; BENCH_* narrow it; BENCH_SUITE=tables times the objects list)' \
	  '  check-fast  Run the static checks, file-size check on changed files only; no tests' \
	  '  format      Format Rust and TypeScript/JavaScript, and apply lint fixes' \
	  '' \
	  'Test suites (isolated home; tests-desktop on a private display):' \
	  '  tests-frontend     React component tests' \
	  '  tests-backend      Rust tests, with and without the embedded frontend' \
	  '  tests-integration  Development supervisor lifecycle' \
	  '  tests-desktop      Electron app on a private display, screenshots in artifacts/screenshots' \
	  '  Filter frontend/backend tests: make tests-frontend TEST=App' \
	  '' \
	  'Dependencies, including Rust when missing, install on first use; there is no setup step.' \
	  'Optional ports: make web PORT=7330 UI_PORT=7331'

node_modules/.installed: package.json pnpm-lock.yaml pnpm-workspace.yaml frontend/package.json desktop/package.json
	pnpm install --frozen-lockfile
	touch $@

# Electron fetches its binary on first use, into the user's download cache; fetch it before tests move HOME.
desktop/node_modules/.electron-ready: node_modules/.installed
	cd desktop && node -e 'require("electron")' >/dev/null
	touch $@

.tools/bin/watchexec:
	$(CARGO) install watchexec-cli --version 2.8.0 --locked --root .tools

.tools/bin/cargo-deny:
	$(CARGO) install cargo-deny --version 0.20.2 --locked --root .tools

# API types do not depend on the server or the embedded frontend.
_types:
	$(CARGO) run --locked -q -p api-types --bin export-types

_frontend-build: node_modules/.installed _types
	pnpm --dir frontend build

build: _frontend-build
	$(CARGO) build --locked --release

package:
	scripts/package-desktop.sh $(OS)

web: _dev
	PORT=$(PORT) UI_PORT=$(UI_PORT) node scripts/dev.cjs web

desktop: _dev desktop/node_modules/.electron-ready
	PORT=$(PORT) UI_PORT=$(UI_PORT) node scripts/dev.cjs desktop

_dev: node_modules/.installed .tools/bin/watchexec _types

_dev-rust: _types
	$(CARGO) build --locked --no-default-features --bin ax-devil

# Tests are separate from static checks; shared prerequisites run once per invocation.
tests-frontend: node_modules/.installed
	$(ISOLATED) pnpm --dir frontend exec vp test run $(TEST)

# The real-Electron export tests and the Python plugin tests skip on a machine without Electron, the render page or
# python3; here they are all provided, so a skip must fail.
tests-backend: export AX_DEVIL_REQUIRE_ELECTRON = 1
tests-backend: export AX_DEVIL_REQUIRE_PYTHON = 1
tests-backend: _frontend-build desktop/node_modules/.electron-ready
	$(ISOLATED) $(CARGO) test --workspace --no-default-features --locked $(TEST)
	$(ISOLATED) $(CARGO) test --workspace --locked $(TEST)

tests-integration: _dev _dev-rust
	$(ISOLATED) node --test scripts/dev.test.cjs desktop/args.test.cjs desktop/window-state.test.cjs desktop/backend.test.cjs desktop/uncaught.test.cjs desktop/workspace-dialogs.test.cjs scripts/check-architecture.test.cjs

tests-desktop: build desktop/node_modules/.electron-ready
	timeout 180s $(ISOLATED) --display pnpm --dir desktop exec electron . --ozone-platform=x11 --smoke-test

tests: tests-frontend tests-backend tests-integration tests-desktop

# Timed with std::time::Instant; numbers go to stdout, nothing is asserted.
bench:
	$(ISOLATED) $(CARGO) bench --locked -p overlays

# Export through the real render page: first frame, then frames per second, SwiftShader and (if the card can render in the hidden page) GPU.
bench-export: _frontend-build desktop/node_modules/.electron-ready
	$(ISOLATED) $(CARGO) bench --locked --no-default-features -p ax-devil --bench export

# Plain Node, no test runner: p50/p95 per catalog, object count and mode; AX_BENCH_* narrow it (frontend/src/present/runBench.ts).
bench-present: node_modules/.installed
	node --import ./frontend/src/bench/node.ts --expose-gc --min-semi-space-size=64 --max-semi-space-size=64 frontend/src/present/runBench.ts

# Plain Node: p50/p95 of the per-frame work behind the objects list at 1k to 50k objects (frontend/src/bench/tablesNode.ts).
bench-tables: node_modules/.installed
	node --import ./frontend/src/bench/node.ts frontend/src/bench/tablesNode.ts

# The real VideoRenderer in Electron (desktop/bench.cjs). Only BENCH_DISPLAY=real, on the machine's own display and GPU,
# gives numbers that count; the default private display uses SwiftShader and only proves the bench runs.
# BENCH_SUITE=tables times the objects list instead of the renderer: scroll, resize and playback with the panel open.
bench-browser: node_modules/.installed desktop/node_modules/.electron-ready
	pnpm --dir frontend exec vp build --config vite.bench.config.ts
ifeq ($(BENCH_DISPLAY),real)
	pnpm --dir desktop exec electron ./bench.cjs
else
	$(ISOLATED) --display pnpm --dir desktop exec electron ./bench.cjs --ozone-platform=x11
endif

# One second per mode at one lane of 100 objects: keeps the bench from rotting. Not part of check.
bench-browser-smoke:
	BENCH_SMOKE=1 BENCH_DISPLAY=swiftshader $(MAKE) --no-print-directory bench-browser

# File size, architecture, formatting, lint and types. CHANGED=--changed limits the file-size check.
_static: node_modules/.installed _types
	node scripts/check-file-tokens.cjs $(CHANGED)
	$(CARGO) metadata --locked --format-version 1 | node scripts/check-architecture.cjs
	pnpm exec vp fmt --check $(ROOT_FILES)
	pnpm exec vp lint --deny-warnings desktop scripts
	pnpm --dir frontend exec vp check
	$(CARGO) fmt --all --check
	$(CARGO) clippy --workspace --all-targets --locked -- -D warnings

_licenses: node_modules/.installed .tools/bin/cargo-deny
	PATH="$(CURDIR)/.tools/bin:$$PATH" $(CARGO) deny --locked check licenses
	node scripts/check-js-licenses.cjs

check-fast: CHANGED := --changed
check-fast: _static

check: _static _frontend-build _licenses
	$(CARGO) clippy --workspace --all-targets --no-default-features --locked -- -D warnings
	$(MAKE) --no-print-directory tests

format: node_modules/.installed _types
	$(CARGO) fmt --all
	pnpm exec vp fmt $(ROOT_FILES)
	pnpm exec vp lint --fix desktop scripts
	pnpm --dir frontend exec vp check --fix
