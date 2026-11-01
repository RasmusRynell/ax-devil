#!/usr/bin/env bash
# Downloads the pinned shared LGPL FFmpeg build (BtbN/FFmpeg-Builds) for Linux or Windows into a new folder, after
# checking it against the release's checksums.sha256. The folder holds include/, lib/ and, on Windows, bin/.
#
#   scripts/ffmpeg-fetch.sh linux|win DEST   DEST is an empty or missing folder
#   scripts/ffmpeg-fetch.sh notice           prints the license notice for these builds
set -euo pipefail

TAG=autobuild-2026-10-31-12-59
BASE="https://github.com/BtbN/FFmpeg-Builds/releases/download/$TAG"
LINUX_ASSET=ffmpeg-n9.0.2-39-g7adfbe40dc-linux64-lgpl-shared-9.0.tar.xz
WINDOWS_ASSET=ffmpeg-n9.0.2-39-g7adfbe40dc-win64-lgpl-shared-9.0.zip

if [[ ${1:-} == notice ]]; then
  cat <<NOTICE
The Linux and Windows builds use FFmpeg n9.0.2 as built by BtbN (LGPL version 3, shared libraries, no GPL or
non-free components):
  $BASE/$LINUX_ASSET
  $BASE/$WINDOWS_ASSET
Their build scripts and configure options are at https://github.com/BtbN/FFmpeg-Builds. The libraries (libavcodec,
libavformat, libavutil, libswscale, libswresample) are linked dynamically, so each can be replaced by a build of the
same version.
NOTICE
  exit 0
fi

os="${1:-}"
dest="${2:?usage: scripts/ffmpeg-fetch.sh linux|win DEST}"
case "$os" in
linux) asset="$LINUX_ASSET" ;;
win) asset="$WINDOWS_ASSET" ;;
*)
  echo "usage: scripts/ffmpeg-fetch.sh linux|win DEST" >&2
  exit 2
  ;;
esac
if [[ -n $(ls -A "$dest" 2>/dev/null) ]]; then
  echo "$dest is not empty" >&2
  exit 1
fi
mkdir -p "$dest"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl --proto '=https' --tlsv1.2 -sSfL -o "$work/$asset" "$BASE/$asset"
curl --proto '=https' --tlsv1.2 -sSfL -o "$work/checksums.sha256" "$BASE/checksums.sha256"
(cd "$work" && grep -F " $asset" checksums.sha256 | sha256sum -c -)

mkdir "$work/unpacked"
case "$asset" in
*.tar.xz) tar -xf "$work/$asset" -C "$work/unpacked" ;;
# Git Bash on Windows has no unzip; the runner image has 7-Zip.
*.zip)
  if command -v unzip >/dev/null; then
    unzip -q "$work/$asset" -d "$work/unpacked"
  else
    # 7-Zip is a Windows program: it gets Windows paths.
    7z x -y "-o$(cygpath -m "$work/unpacked")" "$(cygpath -m "$work/$asset")" >/dev/null
  fi
  ;;
esac
# The archive holds one folder; its contents are the build.
mv "$work"/unpacked/*/* "$dest"/
