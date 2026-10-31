.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
CARGO := scripts/cargo
ISOLATED := scripts/isolated
PORT ?= 7330
UI_PORT ?= 7331
TEST ?=
ROOT_FILES := desktop/*.cjs scripts/*.cjs vite.config.ts package.json pnpm-workspace.yaml
.PHONY: help web desktop build check check-fast format tests tests-frontend tests-backend tests-integration \
	tests-desktop _types _frontend-build _static _licenses _dev _dev-rust

help:
	@printf '%s\n' \
	  'Commands:' \
	  '  web         Start website development with frontend and Rust reload' \
	  '  desktop     Start desktop development with frontend and Rust reload' \
	  '  build       Build the release app with the frontend embedded; do not launch' \
	  '  tests       Run all test suites' \
	  '  check       Run every static check and all test suites' \
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

tests-backend: _frontend-build
	$(ISOLATED) $(CARGO) test --workspace --no-default-features --locked $(TEST)
	$(ISOLATED) $(CARGO) test --workspace --locked $(TEST)

tests-integration: _dev _dev-rust
	$(ISOLATED) node --test scripts/dev.test.cjs

tests-desktop: build desktop/node_modules/.electron-ready
	timeout 60s $(ISOLATED) --display pnpm --dir desktop exec electron . --ozone-platform=x11 --smoke-test

tests: tests-frontend tests-backend tests-integration tests-desktop

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
