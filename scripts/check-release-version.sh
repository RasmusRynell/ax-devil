#!/usr/bin/env bash
# Fails unless the release tag (v1.2.3) names the version in Cargo.toml and in desktop/package.json.
#
#   scripts/check-release-version.sh TAG
set -euo pipefail
cd "$(dirname "$0")/.."

tag="${1:?usage: scripts/check-release-version.sh TAG}"
cargo="$(sed -n 's/^version = "\(.*\)"/\1/p' Cargo.toml | head -1)"
desktop="$(node -p 'require("./desktop/package.json").version')"
if [[ ${tag#v} != "$cargo" || $desktop != "$cargo" ]]; then
  echo "Version mismatch: tag $tag, Cargo.toml $cargo, desktop/package.json $desktop" >&2
  exit 1
fi
