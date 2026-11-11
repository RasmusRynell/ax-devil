#!/usr/bin/env bash
# Runs the desktop smoke test of the app that `make package` left in desktop/dist, one launch per scenario group (the
# plan comes from scripts/smoke-desktop.cjs), each on a fresh private home (and, on Linux, a private X display), against
# this checkout's fixtures. Screenshots go to artifacts/screenshots.
#
# The app is a GUI program: on Windows it may not write to a pipe at all, and its exit code is not reliable. So it runs
# in the background with its output in files, and the verdict is the file named by AX_DEVIL_SMOKE_RESULT, which the
# app's smoke mode writes: PASS, or FAIL: and the reason. Everything lands in artifacts/smoke-logs, per group.
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
read -r stop_seconds grace_seconds < <(node -e 'const b = require("./desktop/smoke-budget.cjs"); console.log(b.KILL_AFTER_MS / 1000, b.EXIT_GRACE_MS / 1000)')

# A path for the app and its tools: C:/... on Windows, unchanged elsewhere.
native() { if [[ $os == win ]]; then cygpath -m "$1"; else echo "$1"; fi; }
export AX_DEVIL_SMOKE_RESULT
flags=(--smoke-test --enable-logging)

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
# A private profile for one launch; Windows programs take theirs from these variables, not from HOME. On Linux
# scripts/isolated gives every launch its own.
fresh_home() {
  [[ $os != linux ]] || return 0
  local home
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
}

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
  echo "FAIL: the package does not declare the ax-devil:// scheme" | tee "$logs/result-scheme.txt"
  exit 1
fi

# Kills the app and everything it started.
alive() { [[ " $(jobs -pr | tr '\n' ' ') " == *" $pid "* ]]; }
kill_tree() {
  alive || return 0
  if [[ $os == win ]]; then
    taskkill //F //T //PID "$(cat "/proc/$pid/winpid" 2>/dev/null || echo "$pid")"
  else
    signal_tree TERM
    for ((n = 0; n < grace_seconds; n++)); do
      alive || break
      sleep 1
    done
    if alive; then signal_tree KILL; fi
  fi
}
signal_tree() {
  local error
  if ! error=$(kill -"$1" -- "-$pid" 2>&1); then
    [[ $error == *"No such process"* ]] || { echo "$error" >&2; return 1; }
  fi
}

# One launch of the app for one group: its scenarios in order, a fresh home, its own logs. Returns the verdict. The app
# fails a hung scenario by name, with its screenshot, well before the plan's deadline; the kill is only for an app that
# cannot report. The CI step that runs this script has its own, longer cap (see ci.yml).
run_group() {
  local group="$1" names="$2" deadline="$3" seconds=0 expired=0 log
  local result="$logs/result-$group.txt"
  AX_DEVIL_SMOKE_RESULT="$(native "$result")"
  export AX_DEVIL_SMOKE_RUN="$names"
  fresh_home
  # Job control gives the app its own process group, which kill_tree ends as a whole.
  set -m
  "${run[@]}" "${flags[@]}" "--log-file=$(native "$logs/electron-$group.log")" </dev/null >"$logs/app-$group.log" 2>&1 &
  pid=$!
  set +m

  until [[ -s $result ]] || ((seconds >= deadline)); do
    # An app that ended without a verdict gets one more second for the file to appear.
    if ! alive; then
      sleep 1
      break
    fi
    sleep 1
    seconds=$((seconds + 1))
  done
  # A launch still without a verdict at its deadline has failed for good: a PASS written while it is ended does not count.
  if [[ ! -s $result ]] && ((seconds >= deadline)); then expired=1; fi
  # After the verdict the app quits by itself; what is left is ended.
  for ((n = 0; n < stop_seconds; n++)); do
    alive || break
    sleep 1
  done
  kill_tree
  wait "$pid" || true

  for log in "app-$group.log" "electron-$group.log"; do
    echo "--- $log"
    cat "$logs/$log" 2>/dev/null || echo "(none)"
  done
  echo "--- result $group"
  if ((expired)); then
    echo "FAIL: no result after $seconds seconds" | tee "$result"
  elif [[ -s $result ]]; then
    cat "$result"
  else
    echo "FAIL: no result after $seconds seconds" | tee "$result"
  fi
  ((!expired)) && [[ $(head -n 1 "$result") == PASS ]]
}

# Every group runs, each on a fresh launch, even after one fails; the launcher owns the groups.
failed=0
summary=()
# The plan is one row per launch: group, scenarios and the launcher's own deadline in seconds for it; the launcher owns the names.
# Every row is checked before anything launches, so a stray line in the plan fails the smoke instead of becoming a launch.
plan="$(node scripts/smoke-desktop.cjs --plan '')" || { echo "FAIL: could not plan the smoke groups" >&2; exit 1; }
plan="${plan//$'\r'/}" # node prints CRLF on Windows
[[ -n $plan ]] || { echo "FAIL: no smoke groups" >&2; exit 1; }
rows=()
while IFS= read -r row; do
  [[ $row =~ ^[^[:space:]]+[[:space:]][^[:space:]]+[[:space:]][0-9]+$ ]] || { echo "FAIL: malformed smoke plan row: $row" >&2; exit 1; }
  rows+=("$row")
done <<<"$plan"
for row in "${rows[@]}"; do
  read -r group names deadline <<<"$row"
  echo "=== $group: $names (deadline ${deadline}s)"
  if run_group "$group" "$names" "$deadline"; then
    summary+=("PASS $group")
  else
    summary+=("FAIL $group")
    failed=1
  fi
done
printf '%s\n' "${summary[@]}"
exit "$failed"
