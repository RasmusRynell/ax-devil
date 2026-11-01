#!/usr/bin/env bash
# Packages the desktop app for one OS: builds the release backend with the frontend embedded, stages it with only
# the FFmpeg libraries it loads, and runs electron-builder. Linux needs patchelf and macOS needs install_name_tool,
# so the libraries load from beside the backend. Output lands in desktop/dist.
#
#   FFMPEG_DIR=/path/to/ffmpeg scripts/package-desktop.sh linux|win|mac
#
# FFMPEG_DIR is a shared (dynamic) FFmpeg build with include/ and lib/ (also bin/ on Windows). SKIP_BUILD=1 reuses
# the release binary already built against it.
# MACOSX_DEPLOYMENT_TARGET must be the one FFmpeg was built with.
set -euo pipefail
shopt -s extglob
cd "$(dirname "$0")/.."

os="${1:-}"
: "${FFMPEG_DIR:?set FFMPEG_DIR to the shared FFmpeg build}"
# Windows tools want C:/... paths, which `pwd -W` gives in Git Bash.
FFMPEG_DIR="$(cd "$FFMPEG_DIR" && { pwd -W 2>/dev/null || pwd; })"
export FFMPEG_DIR
libraries=(avcodec avformat avutil swscale swresample)
case "$os" in
linux) executable=ax-devil builder=(--linux) ;;
win) executable=ax-devil.exe builder=(--win) ;;
mac) executable=ax-devil builder=(--mac --arm64) ;;
*)
  echo "usage: FFMPEG_DIR=<ffmpeg> scripts/package-desktop.sh linux|win|mac" >&2
  exit 2
  ;;
esac

# The version has one source, Cargo.toml.
version="$(sed -n 's/^version = "\(.*\)"/\1/p' Cargo.toml | head -1)"

# The steps of `make build`, spelled out because a Windows runner has no make to count on.
if [[ -z ${SKIP_BUILD:-} ]]; then
  pnpm install --frozen-lockfile
  scripts/cargo run --locked -q -p api-types --bin export-types
  pnpm --dir frontend build
  scripts/cargo build --locked --release
fi
# rust-embed builds without a frontend instead of failing, so a release without one would serve nothing.
if [[ ! -f frontend/dist/index.html ]]; then
  echo "frontend/dist/index.html is missing: the backend would embed no frontend." >&2
  exit 1
fi

bin=desktop/staging/bin
rm -rf desktop/staging
mkdir -p "$bin"
cp "target/release/$executable" "$bin/"
staged=("$bin/$executable")
# FFmpeg's license text goes with its libraries.
cp "$FFMPEG_DIR/LICENSE.txt" "$bin/FFMPEG-LICENSE.txt"

# The library files by their runtime names: libavcodec.so.63 and libavcodec.63.dylib, not their full versions.
stage() {
  local pattern="$1" found=0 file
  for file in "$FFMPEG_DIR"/$pattern; do
    [[ -e $file ]] || continue
    cp -L "$file" "$bin/"
    staged+=("$bin/$(basename "$file")")
    found=1
  done
  ((found)) || {
    echo "no $pattern in $FFMPEG_DIR" >&2
    exit 1
  }
}
for name in "${libraries[@]}"; do
  case "$os" in
  linux) stage "lib/lib$name.so.+([0-9])" ;;
  win) stage "bin/$name-[0-9]*.dll" ;;
  mac) stage "lib/lib$name.+([0-9]).dylib" ;;
  esac
done

# Prints what the backend loads, so the log shows that only the pinned libraries and system ones are referenced.
case "$os" in
linux)
  # shellcheck disable=SC2016 # $ORIGIN is for the loader, not the shell
  for file in "${staged[@]}"; do patchelf --set-rpath '$ORIGIN' "$file"; done
  ldd "$bin/$executable"
  ;;
win)
  # The import table lists DLL names as plain text; dumpbin is not on the PATH of a Git Bash shell.
  grep -aoE '[A-Za-z0-9_.-]+\.dll' "$bin/$executable" | sort -fu
  ;;
mac)
  # Rewriting load commands invalidates signatures, which Apple silicon requires, so each file is signed again.
  for file in "${staged[@]}"; do
    install_name_tool -add_rpath @loader_path "$file"
    codesign --force -s - "$file"
  done
  otool -L "$bin/$executable"
  # Everything but system libraries must resolve beside the backend.
  for file in "${staged[@]}"; do
    if otool -L "$file" | tail -n +2 | awk '{print $1}' | grep -Ev '^(/usr/lib/|/System/|@rpath/|@loader_path/)'; then
      echo "$file loads a library from outside the package" >&2
      exit 1
    fi
  done
  ;;
esac

pnpm --dir desktop exec electron-builder --config electron-builder.yml "${builder[@]}" --publish never \
  "-c.extraMetadata.version=$version"
