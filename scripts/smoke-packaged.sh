#!/usr/bin/env bash
# Runs the desktop smoke test of the app that `make package` left in desktop/dist, on a private home
# (and, on Linux, a private X display), against this checkout's fixtures. Screenshots go to artifacts/screenshots.
#
# The app is a GUI program: on Windows it may not write to a pipe at all, and its exit code is not reliable. So it runs
# in the background with its output in files, and the verdict is the file named by AX_DEVIL_SMOKE_RESULT, which the
# app's smoke mode writes: PASS, or FAIL: and the reason. Everything lands in artifacts/smoke-logs.
#
#   make smoke-packaged OS=linux|win|mac
set -euo pipefail
cd "$(dirname "$0")/.."
os="${1:-}"
# The Windows and macOS apps open on the desktop they run on, which only a CI runner may lend.
if [[ ($os == win || $os == mac) && ${CI:-} != true ]]; then
  echo "native desktop smoke runs only on CI runners; use Linux/Xvfb locally" >&2
  exit 1
fi
dist="$PWD/desktop/dist"
logs="$PWD/artifacts/smoke-logs"
rm -rf "$logs"
mkdir -p "$logs"
export AX_DEVIL_SMOKE_ROOT="$PWD"

# A path for the app and its tools: C:/... on Windows, unchanged elsewhere.
native() { if [[ $os == win ]]; then cygpath -m "$1"; else echo "$1"; fi; }
result="$logs/result.txt"
export AX_DEVIL_SMOKE_RESULT
AX_DEVIL_SMOKE_RESULT="$(native "$result")"
flags=(--smoke-test --enable-logging "--log-file=$(native "$logs/electron.log")")

case "$os" in
linux)
  # No FUSE is needed to extract and run an AppImage. isolated gives the run a private home and X display.
  exe="$(echo "$dist"/ax-devil-*-linux-x86_64.AppImage)"
  run=(scripts/isolated --display env APPIMAGE_EXTRACT_AND_RUN=1 "$exe" --ozone-platform=x11)
  ;;
mac)
  exe="$dist/mac-arm64/ax-devil.app/Contents/MacOS/ax-devil"
  run=("$exe")
  ;;
win)
  exe="$dist/win-unpacked/ax-devil.exe"
  run=("$exe")
  ;;
*)
  echo "usage: make smoke-packaged OS=linux|win|mac" >&2
  exit 2
  ;;
esac
if [[ $os != linux ]]; then
  # A private profile; Windows programs take theirs from these variables, not from HOME.
  home="$(mktemp -d)"
  mkdir -p "$home"/{config,data,cache,state}
  export HOME="$home" XDG_CONFIG_HOME="$home/config" XDG_DATA_HOME="$home/data" XDG_CACHE_HOME="$home/cache"
  export XDG_STATE_HOME="$home/state"
  if [[ $os == win ]]; then
    export USERPROFILE APPDATA LOCALAPPDATA
    USERPROFILE="$(cygpath -w "$home")"
    APPDATA="$USERPROFILE\\AppData\\Roaming"
    LOCALAPPDATA="$USERPROFILE\\AppData\\Local"
    mkdir -p "$home/AppData/Roaming" "$home/AppData/Local"
  fi
fi

echo "launching $exe"
ls -l "$exe"

# The package declares the ax-devil:// scheme, which is how the system learns to hand it links: the AppImage's desktop
# entry on Linux, the bundle's Info.plist on macOS. A Windows zip declares nothing; the app registers at run time.
scheme_declared() {
  case "$os" in
  linux)
    local extracted
    extracted="$(mktemp -d)"
    (cd "$extracted" && "$exe" --appimage-extract '*.desktop' >/dev/null)
    grep -q 'x-scheme-handler/ax-devil' "$extracted"/squashfs-root/*.desktop
    ;;
  mac) grep -A3 CFBundleURLSchemes "$dist/mac-arm64/ax-devil.app/Contents/Info.plist" | grep -q '<string>ax-devil</string>' ;;
  esac
}
if ! scheme_declared; then
  echo "FAIL: the package does not declare the ax-devil:// scheme" | tee "$result"
  exit 1
fi

# Kills the app and everything it started.
kill_tree() {
  if [[ $os == win ]]; then
    taskkill //F //T //PID "$(cat "/proc/$pid/winpid" 2>/dev/null || echo "$pid")" >/dev/null 2>&1 || true
  else
    kill -TERM -- "-$pid" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$pid" 2>/dev/null || true
  fi
}
alive() { kill -0 "$pid" 2>/dev/null; }

# Job control gives the app its own process group, which kill_tree ends as a whole.
set -m
"${run[@]}" "${flags[@]}" </dev/null >"$logs/app.log" 2>&1 &
pid=$!
set +m

seconds=0
until [[ -s $result ]] || ((seconds >= 180)); do
  # An app that ended without a verdict gets one more second for the file to appear.
  if ! alive; then
    sleep 1
    break
  fi
  sleep 1
  seconds=$((seconds + 1))
done
# After the verdict the app quits by itself; what is left is ended.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  alive || break
  sleep 1
done
kill_tree

for log in app.log electron.log; do
  echo "--- $log"
  cat "$logs/$log" 2>/dev/null || echo "(none)"
done
echo "--- result"
if [[ -s $result ]]; then
  cat "$result"
else
  echo "FAIL: no result after $seconds seconds" | tee "$result"
fi
[[ $(head -n 1 "$result") == PASS ]]
