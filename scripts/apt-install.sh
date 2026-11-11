#!/usr/bin/env bash
# Install apt packages on a CI runner without depending on the Azure mirror.
# Usage: scripts/apt-install.sh PACKAGE...
#
# The runner image lists azure.archive.ubuntu.com first in its mirror file, and that host stalls or fails to resolve.
# This drops it so apt uses the next entries, rewrites sources that name it directly, sets retries and short timeouts,
# then updates and installs. Safe to run twice; a no-op for the mirror steps on images without the Azure host.
set -euo pipefail

AZURE_HOST="azure.archive.ubuntu.com"
APT_MIRRORS_FILE="${APT_MIRRORS_FILE:-/etc/apt/apt-mirrors.txt}"
APT_SOURCES_LIST="${APT_SOURCES_LIST:-/etc/apt/sources.list}"
APT_SOURCES_DIR="${APT_SOURCES_DIR:-/etc/apt/sources.list.d}"
APT_CONF_FILE="${APT_CONF_FILE:-/etc/apt/apt.conf.d/99ci-retries}"

if [ -z "${SUDO+x}" ]; then
  if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
fi

if [ "$#" -eq 0 ]; then
  echo "usage: $0 PACKAGE..." >&2
  exit 2
fi

if [ -f "$APT_MIRRORS_FILE" ] && grep -q "$AZURE_HOST" "$APT_MIRRORS_FILE"; then
  echo "Removing $AZURE_HOST from $APT_MIRRORS_FILE"
  $SUDO sed -i "/$AZURE_HOST/d" "$APT_MIRRORS_FILE"
  if ! grep -q '[^[:space:]]' "$APT_MIRRORS_FILE"; then
    echo "http://archive.ubuntu.com/ubuntu/" | $SUDO tee "$APT_MIRRORS_FILE" >/dev/null
  fi
fi

# Sources that name the Azure host directly: jammy .list files and noble deb822 .sources files.
for file in "$APT_SOURCES_LIST" "$APT_SOURCES_DIR"/*.list "$APT_SOURCES_DIR"/*.sources; do
  if [ -f "$file" ] && grep -q "$AZURE_HOST" "$file"; then
    echo "Pointing $file at archive.ubuntu.com"
    $SUDO sed -i "s#//$AZURE_HOST/#//archive.ubuntu.com/#g" "$file"
  fi
done

printf 'Acquire::Retries "5";\nAcquire::http::Timeout "20";\nAcquire::https::Timeout "20";\n' \
  | $SUDO tee "$APT_CONF_FILE" >/dev/null

if ! $SUDO apt-get update; then
  echo "error: apt-get update failed even without the $AZURE_HOST mirror" >&2
  exit 1
fi
$SUDO apt-get install -y --no-install-recommends "$@"
