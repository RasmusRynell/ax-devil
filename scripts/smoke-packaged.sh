#!/usr/bin/env bash
# Runs the desktop smoke test of the app that scripts/package-desktop.sh left in desktop/dist, on a private home
# (and, on Linux, a private X display), against this checkout's fixtures. Screenshots go to artifacts/screenshots.
#
#   scripts/smoke-packaged.sh linux|win|mac
set -euo pipefail
cd "$(dirname "$0")/.."
export AX_DEVIL_SMOKE_ROOT="$PWD"
dist="$PWD/desktop/dist"
log="$(mktemp)"

case "${1:-}" in
linux)
  # No FUSE is needed to extract and run an AppImage.
  run=(scripts/isolated --display env APPIMAGE_EXTRACT_AND_RUN=1 "$dist"/ax-devil-*-linux-x86_64.AppImage --ozone-platform=x11)
  ;;
mac)
  run=(scripts/isolated "$dist/mac-arm64/ax-devil.app/Contents/MacOS/ax-devil")
  ;;
win)
  # Windows programs take their home from the profile variables, not from HOME.
  # shellcheck disable=SC2016 # the inner shell expands these
  run=(scripts/isolated bash -c '
    export USERPROFILE="$(cygpath -w "$HOME")" APPDATA="$(cygpath -w "$HOME/AppData/Roaming")" \
      LOCALAPPDATA="$(cygpath -w "$HOME/AppData/Local")"
    exec "$@"' bash "$dist/win-unpacked/ax-devil.exe")
  ;;
*)
  echo "usage: scripts/smoke-packaged.sh linux|win|mac" >&2
  exit 2
  ;;
esac

# The exit code of a Windows GUI program is not always passed on, so the PASS line decides as well. The test stops
# itself after a minute; the CI job has a timeout of its own.
"${run[@]}" --smoke-test 2>&1 | tee "$log"
grep -q '^PASS:' "$log"
