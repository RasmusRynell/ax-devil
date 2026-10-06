#!/usr/bin/env bash
# Install the built release in fresh Ubuntu 24.04 containers, following the documented steps.
# Usage: [DIST=dir] tools/release-check/run.sh [tool|pip|py310|source]...   (default: all)
# DIST holds already built artifacts to check, such as the ones CI publishes; otherwise they are built here.
set -euo pipefail

repository=$(cd "$(dirname "$0")/../.." && pwd)
scenarios=("$@")
[ ${#scenarios[@]} -gt 0 ] || scenarios=(tool pip py310 source)

context=$(mktemp -d)
trap 'rm -rf "$context"' EXIT

# The tree a fresh clone would contain: tracked and new files, never ignored local data.
mkdir "$context/repo"
git -C "$repository" ls-files -z -co --exclude-standard |
    tar -C "$repository" --null --ignore-failed-read -T - -cf - 2>/dev/null | tar -C "$context/repo" -xf -
git -C "$context/repo" init -q
git -C "$context/repo" add -A
git -C "$context/repo" -c user.name=release-check -c user.email=release-check@localhost commit -qm candidate

# The release candidate, plus a later patch release for the upgrade check. Both stand in for PyPI.
if [ -n "${DIST:-}" ]; then
    mkdir "$context/dist" && cp "$DIST"/* "$context/dist/"
else
    uv build -q --out-dir "$context/dist" "$context/repo"
fi
cp -r "$context/repo" "$context/next"
version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$context/repo/pyproject.toml" | head -1)
sed -i "s/^version = \"$version\"/version = \"$version.post1\"/" "$context/next/pyproject.toml"
uv build -q --wheel --out-dir "$context/dist-next" "$context/next"
rm -rf "$context/next"
cp "$(dirname "$0")/in-container.sh" "$context/"
chmod -R a+rX "$context"

failed=()
for scenario in "${scenarios[@]}"; do
    echo "=== $scenario ==="
    if docker run --rm -v "$context:/release:ro" ubuntu:24.04 bash /release/in-container.sh "$scenario"; then
        echo "=== $scenario: passed ==="
    else
        echo "=== $scenario: FAILED ==="
        failed+=("$scenario")
    fi
done
[ ${#failed[@]} -eq 0 ] || { echo "Failed: ${failed[*]}"; exit 1; }
echo "All release checks passed: ${scenarios[*]}"
