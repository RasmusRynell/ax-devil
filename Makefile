.DEFAULT_GOAL := help
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
CARGO := scripts/cargo
ISOLATED := scripts/isolated
PORT ?= 7330
UI_PORT ?= 7331
TEST ?=
SMOKE ?=
CRATE ?=
ROOT_FILES := desktop/*.cjs scripts/*.cjs vite.config.ts package.json pnpm-workspace.yaml
# The loader paths of a FFmpeg outside the system's (package builds): Linux, macOS and Windows each read one of them.
FFMPEG_LOADER := $(if $(FFMPEG_DIR),LD_LIBRARY_PATH="$(FFMPEG_DIR)/lib$${LD_LIBRARY_PATH:+:$$LD_LIBRARY_PATH}" DYLD_LIBRARY_PATH="$(FFMPEG_DIR)/lib$${DYLD_LIBRARY_PATH:+:$$DYLD_LIBRARY_PATH}" PATH="$$(cygpath -u '$(FFMPEG_DIR)/bin' 2>/dev/null || echo '$(FFMPEG_DIR)/bin'):$$PATH")
.PHONY: help web desktop build app cli check check-fast format package smoke-packaged verify-release-version test \
	test-frontend test-backend test-integration test-desktop test-camera audit bench bench-export bench-present bench-tables bench-browser \
	bench-browser-smoke _check _types _frontend-build _static _clippy _clippy-no-default _licenses _dev _dev-rust

help:
	@printf '%s\n' \
	  'Commands:' \
	  '  web         Start website development with frontend and Rust reload' \
	  '  desktop     Start desktop development with frontend and Rust reload' \
	  '  build       Build the release app with the frontend embedded; do not launch' \
	  '  package     Package the desktop app: make package OS=linux|win|mac FFMPEG_DIR=<shared FFmpeg>' \
	  '  app         Run the release app with ARGS (ISOLATE=1: private home and display): make app ARGS="playlist mot_challenge <folder>"' \
	  '  cli         Run the release backend with ARGS (ISOLATE=1: private home): make cli ARGS=serve PORT=7400' \
	  '  smoke-packaged  Smoke-test the packaged app that make package left in desktop/dist: make smoke-packaged OS=linux|win|mac' \
	  '  verify-release-version  Fail unless TAG names the version in Cargo.toml and desktop/package.json: make verify-release-version TAG=v1.2.3' \
	  '  test        Run all test suites' \
	  '  test-camera  Run the real-camera checks (AX_DEVIL_TARGET_*, AX_DEVIL_MQTT_BROKER_*); not part of check or CI' \
	  '  check       The gate: every static check, all test suites, and a check that they left no generated file changed' \
	  '  audit       Check the locked dependencies for known vulnerabilities (needs the network); not part of check, CI runs it weekly' \
	  '  bench       Run the Rust benchmarks (sample item encode); numbers on stdout' \
	  '  bench-export  Time export (1080p, 4K) through the real render page, on SwiftShader and the GPU when it can render there' \
	  '  bench-present  Time the frontend presentation path in plain Node (AX_BENCH_SIZES, _CATALOGS, _MODES, _REPEAT)' \
	  '  bench-tables  Time the per-frame work of the objects list (filter, search, sort, column fit) in plain Node (AX_BENCH_SIZES, _RUNS)' \
	  '  bench-browser  Time the real renderer in Electron (SwiftShader; BENCH_DISPLAY=real for the local GPU; BENCH_* narrow it; BENCH_SUITE=tables times the objects list)' \
	  '  bench-browser-smoke  One second per bench-browser mode, to keep it working; not part of check' \
	  '  check-fast  Run the static checks, file-size check on changed files only; no tests' \
	  '  format      Format Rust and TypeScript/JavaScript, and apply lint fixes' \
	  '' \
	  'Test suites (isolated home; test-desktop on a private display; make test runs all four):' \
	  '  test-frontend     React component tests' \
	  '  test-backend      Rust tests, with and without the embedded frontend' \
	  '  test-integration  Development supervisor lifecycle' \
	  '  test-desktop      Electron app on a private display, screenshots in artifacts/screenshots; SMOKE=<groups or scenarios> runs only those (a scenario brings the ones before it in its group): make test-desktop SMOKE=media' \
	  '  Filter frontend/backend tests: make test-frontend TEST=App' \
	  '  One backend crate: make test-backend CRATE=scene' \
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

# cargo-deny at the pinned version, and watchexec 2.x, are used from PATH when installed; otherwise they are built into .tools/.
CARGO_DENY_VERSION := 0.20.2
ifeq ($(shell cargo-deny --version 2>/dev/null),cargo-deny $(CARGO_DENY_VERSION))
CARGO_DENY :=
else
CARGO_DENY := .tools/bin/cargo-deny
endif
# No parentheses in the check (a ")" would end $(shell ...) early), and no early-exiting reader such as head or grep -q:
# with pipefail from .SHELLFLAGS, watchexec would get SIGPIPE on its later lines and the check would fail at random.
export WATCHEXEC := $(or $(shell watchexec --version 2>/dev/null | sed -n 1p | grep -q '^watchexec 2[.]' && command -v watchexec),.tools/bin/watchexec)

.tools/bin/watchexec:
	$(CARGO) install watchexec-cli --version 2.8.0 --locked --root .tools

.tools/bin/cargo-deny:
	$(CARGO) install cargo-deny --version $(CARGO_DENY_VERSION) --locked --root .tools

# API types do not depend on the server or the embedded frontend.
# The export crate links FFmpeg, so a FFmpeg outside the system's is put on the loader path. The binary runs
# directly: macOS drops DYLD_* from a script started through /usr/bin/env, as cargo's wrapper is.
_types:
	$(CARGO) build --locked -q -p api-types --bin export-types
	$(FFMPEG_LOADER) target/debug/export-types

_frontend-build: node_modules/.installed _types
	pnpm --dir frontend build

build: _frontend-build
	$(CARGO) build --locked --release

package:
	scripts/package-desktop.sh $(OS)

smoke-packaged:
	scripts/smoke-packaged.sh $(OS)

verify-release-version:
	scripts/check-release-version.sh "$(TAG)"

# The release app or backend with arguments. ISOLATE=1 gives it a private home, and the app a private display.
app: build desktop/node_modules/.electron-ready
	$(if $(ISOLATE),$(ISOLATED) --display )pnpm --dir desktop exec electron . $(if $(ISOLATE),--ozone-platform=x11 )$(ARGS)

cli: build
	$(if $(ISOLATE),$(ISOLATED) )target/release/ax-devil $(ARGS)

web: _dev
	PORT=$(PORT) UI_PORT=$(UI_PORT) node scripts/dev.cjs web

desktop: _dev desktop/node_modules/.electron-ready
	PORT=$(PORT) UI_PORT=$(UI_PORT) node scripts/dev.cjs desktop

_dev: node_modules/.installed $(WATCHEXEC) _types

_dev-rust: _types
	$(CARGO) build --locked --no-default-features --bin ax-devil

# Tests are separate from static checks; shared prerequisites run once per invocation.
test-frontend: node_modules/.installed
	$(ISOLATED) pnpm --dir frontend exec vp test run $(TEST)

# The packages the --no-default-features test and clippy passes cover, after the workspace pass: the embedded frontend
# is a default feature of the root crate alone (scripts/check-architecture.cjs enforces it). camera joins it because its
# dev-dependencies enable features of shared crates (tokio-rustls); without it the dependencies build twice.
NO_DEFAULT_PACKAGES := -p ax-devil -p camera

# The crates whose tests need the built frontend and Electron: grep their tests for frontend/dist or
# AX_DEVIL_REQUIRE_ELECTRON. Both variables stay exported for every CRATE: AX_DEVIL_REQUIRE_ELECTRON is read only by
# ax-devil's tests, and AX_DEVIL_REQUIRE_PYTHON by the tests of ax-devil and plugins, where it turns a missing python3
# into a failure; other crates ignore them.
FRONTEND_TEST_CRATES := ax-devil
# The real-Electron export tests and the Python plugin tests skip on a machine without Electron, the render page or
# python3; here they are all provided, so a skip must fail.
test-backend: export AX_DEVIL_REQUIRE_ELECTRON = 1
test-backend: export AX_DEVIL_REQUIRE_PYTHON = 1
test-backend: $(if $(or $(filter $(CRATE),$(FRONTEND_TEST_CRATES)),$(if $(CRATE),,x)),_frontend-build desktop/node_modules/.electron-ready)
	$(ISOLATED) $(CARGO) test $(if $(CRATE),-p $(CRATE),--workspace) --locked $(TEST)
# The second pass compiles the code and tests that the embedded frontend turns off (src/tests.rs, tests/it/access.rs):
# the development backend of `make web` and `make desktop` (scripts/dev-backend runs _dev-rust). Other crates have no default features, so CRATE=<them> skips it.
	$(if $(or $(filter ax-devil,$(CRATE)),$(if $(CRATE),,x)),$(ISOLATED) $(CARGO) test $(if $(CRATE),-p ax-devil,$(NO_DEFAULT_PACKAGES)) --no-default-features --locked $(TEST))

test-integration: _dev _dev-rust
	$(ISOLATED) node --test scripts/dev.test.cjs desktop/args.test.cjs desktop/links.test.cjs desktop/remote.test.cjs desktop/window-state.test.cjs desktop/backend.test.cjs desktop/uncaught.test.cjs desktop/workspace-dialogs.test.cjs desktop/fit-coverage.test.cjs scripts/check-architecture.test.cjs

# The debug backend, which test-backend has already compiled: a release build here would only repeat the package jobs'.
test-desktop: export AX_DEVIL_BACKEND = $(CURDIR)/target/debug/ax-devil$(if $(filter Windows_NT,$(OS)),.exe)
test-desktop: _frontend-build desktop/node_modules/.electron-ready
	$(CARGO) build --locked --bin ax-devil
	node scripts/smoke-desktop.cjs '$(SMOKE)'

# Needs a real camera, so it is not part of `test` or `check`; each test skips itself when AX_DEVIL_TARGET_ADDR is unset.
test-camera:
	$(ISOLATED) $(CARGO) test --locked -p camera --test it real_camera:: -- --ignored --test-threads=1

test: test-frontend test-backend test-integration $(if $(SKIP_DESKTOP),,test-desktop)

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

# Whitespace, file size, architecture, formatting and lint. CHANGED=--changed limits the file-size check.
# The whitespace check reads every tracked file against the empty tree, so a clean checkout is covered; .gitattributes
# lists the files it skips (-whitespace).
_static: node_modules/.installed _types
	git diff --check "$$(git hash-object -t tree /dev/null)"
	node scripts/check-file-tokens.cjs $(CHANGED)
	$(CARGO) metadata --locked --format-version 1 | node scripts/check-architecture.cjs
	node scripts/check-docs.cjs
	pnpm exec vp fmt --check $(ROOT_FILES)
	pnpm exec vp lint --deny-warnings desktop scripts
	pnpm --dir frontend exec vp check
	$(CARGO) fmt --all --check

_clippy: _static
	$(CARGO) clippy --workspace --all-targets --locked -- -D warnings

_clippy-no-default:
	$(CARGO) clippy $(NO_DEFAULT_PACKAGES) --all-targets --no-default-features --locked -- -D warnings

_licenses: node_modules/.installed $(CARGO_DENY)
	$(if $(CARGO_DENY),PATH="$(CURDIR)/.tools/bin:$$PATH" )$(CARGO) deny --locked check licenses
	node scripts/check-js-licenses.cjs

# Known vulnerabilities in the locked dependencies. Advisories change without a code change and need the network,
# so this is not part of check; .github/workflows/audit.yml runs it weekly.
audit: $(CARGO_DENY)
	$(if $(CARGO_DENY),PATH="$(CURDIR)/.tools/bin:$$PATH" )$(CARGO) deny --locked check advisories
	pnpm audit

check-fast: CHANGED := --changed
check-fast: _static _clippy

# The prerequisites run in this order (without -j), so the cheap checks fail before the test suites start.
_check: _clippy _frontend-build _licenses _clippy-no-default test

# The gate. Regenerating files must not change the tree: a stale generated file fails here, on a developer's machine
# and in CI alike, by comparing the tree before and after.
TREE_STATE := { git status --porcelain -uall; git diff; } | git hash-object --stdin
check:
	before="$$($(TREE_STATE))"; \
	$(MAKE) --no-print-directory _check; \
	if [[ "$$($(TREE_STATE))" != "$$before" ]]; then echo 'make check changed tracked or new files (stale generated files):' >&2; git status --short >&2; exit 1; fi

format: node_modules/.installed _types
	$(CARGO) fmt --all
	pnpm exec vp fmt $(ROOT_FILES)
	pnpm exec vp lint --fix desktop scripts
	pnpm --dir frontend exec vp check --fix
