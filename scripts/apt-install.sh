#!/usr/bin/env bash
# Install apt packages on a CI runner from whichever Ubuntu mirror answers.
# Usage: scripts/apt-install.sh PACKAGE...
#
# Runner images list their mirrors in apt-mirrors.txt, and any of them, including the ubuntu.com hosts, can stop
# resolving from the runners. This probes each listed mirror plus mirrors.kernel.org once, keeps only the reachable
# ones in the mirror list, points direct sources at the first, sets apt retries and short timeouts, then installs.
# A third-party mirror is safe: apt verifies package signatures against the distribution keys.
# Safe to run twice; the probes take at most MAX_CANDIDATES * PROBE_TIMEOUT seconds (30).
set -euo pipefail

EXTRA_MIRROR="https://mirrors.kernel.org/ubuntu/"
MAX_CANDIDATES=6
PROBE_TIMEOUT=5
APT_MIRRORS_FILE="${APT_MIRRORS_FILE:-/etc/apt/apt-mirrors.txt}"
APT_SOURCES_LIST="${APT_SOURCES_LIST:-/etc/apt/sources.list}"
APT_SOURCES_DIR="${APT_SOURCES_DIR:-/etc/apt/sources.list.d}"
APT_CONF_FILE="${APT_CONF_FILE:-/etc/apt/apt.conf.d/99ci-retries}"
OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
# Called as: $APT_PROBE_CMD URL; succeeds only when the URL answers.
APT_PROBE_CMD="${APT_PROBE_CMD:-curl -fsS -o /dev/null --connect-timeout $PROBE_TIMEOUT --max-time $PROBE_TIMEOUT}"

if [ -z "${SUDO+x}" ]; then
  if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
fi

if [ "$#" -eq 0 ]; then
  echo "usage: $0 PACKAGE..." >&2
  exit 2
fi

codename="$(. "$OS_RELEASE_FILE" && echo "${VERSION_CODENAME:-}")"
if [ -z "$codename" ]; then
  echo "error: no VERSION_CODENAME in $OS_RELEASE_FILE" >&2
  exit 1
fi

# Candidates: the image's own mirrors in order, then the extra one; the extra one always keeps a slot.
candidates=()
if [ -f "$APT_MIRRORS_FILE" ]; then
  while IFS= read -r url; do
    [ "${#candidates[@]}" -lt $((MAX_CANDIDATES - 1)) ] && candidates+=("${url%/}/")
  done < <(awk 'NF && $1 !~ /^#/ && !seen[$1]++ {print $1}' "$APT_MIRRORS_FILE")
fi
candidates+=("$EXTRA_MIRROR")

reachable=()
for url in "${candidates[@]}"; do
  already=0
  for kept in "${reachable[@]+"${reachable[@]}"}"; do [ "$kept" = "$url" ] && already=1; done
  [ "$already" -eq 1 ] && continue
  # shellcheck disable=SC2086
  if $APT_PROBE_CMD "${url}dists/$codename/InRelease" >/dev/null 2>&1; then
    echo "mirror reachable: $url"
    reachable+=("$url")
  else
    echo "mirror not reachable: $url"
  fi
done

if [ "${#reachable[@]}" -gt 0 ]; then
  if [ -f "$APT_MIRRORS_FILE" ]; then
    with_priority=0
    grep -q 'priority:' "$APT_MIRRORS_FILE" && with_priority=1
    n=0
    for url in "${reachable[@]}"; do
      n=$((n + 1))
      if [ "$with_priority" -eq 1 ]; then printf '%s\tpriority:%d\n' "$url" "$n"; else printf '%s\n' "$url"; fi
    done | $SUDO tee "$APT_MIRRORS_FILE" >/dev/null
  fi
  # Sources that name an Ubuntu host directly: jammy .list files and noble deb822 .sources files.
  for file in "$APT_SOURCES_LIST" "$APT_SOURCES_DIR"/*.list "$APT_SOURCES_DIR"/*.sources; do
    if [ -f "$file" ] && grep -qE '//(azure\.archive|archive|security)\.ubuntu\.com/ubuntu/' "$file"; then
      echo "Pointing $file at ${reachable[0]}"
      $SUDO sed -i -E "s#https?://(azure\.archive|archive|security)\.ubuntu\.com/ubuntu/#${reachable[0]}#g" "$file"
    fi
  done
else
  echo "no Ubuntu mirror answered; leaving the apt sources unchanged" >&2
fi

printf 'Acquire::Retries "5";\nAcquire::http::Timeout "20";\nAcquire::https::Timeout "20";\n' \
  | $SUDO tee "$APT_CONF_FILE" >/dev/null

if ! $SUDO apt-get update; then
  echo "error: apt-get update failed; no reachable Ubuntu mirror (tried: ${candidates[*]})" >&2
  exit 1
fi
$SUDO apt-get install -y --no-install-recommends "$@"
