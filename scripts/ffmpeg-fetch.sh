#!/usr/bin/env bash
# Downloads the pinned shared LGPL FFmpeg build (BtbN/FFmpeg-Builds) for Linux or Windows into a new folder, or the
# FFmpeg source it was built from. Every download is checked against the SHA-256 values below, which are committed
# here, not read from the download site.
#
#   scripts/ffmpeg-fetch.sh linux|win DEST   DEST is an empty or missing folder; it gets include/, lib/ and bin/
#   scripts/ffmpeg-fetch.sh source DIR       puts ffmpeg-source-btbn-<commit>.tar.gz in DIR
#   scripts/ffmpeg-fetch.sh notice           prints the license notice for these builds
set -euo pipefail

TAG=autobuild-2026-10-31-12-59
# The commit of BtbN/FFmpeg-Builds that the tag names.
TAG_COMMIT=5031c6f1082414e53688d39e521849905071070d
BASE="https://github.com/BtbN/FFmpeg-Builds/releases/download/$TAG"
LINUX_ASSET=ffmpeg-n9.0.2-39-g7adfbe40dc-linux64-lgpl-shared-9.0.tar.xz
LINUX_SHA256=5a58e2dffad4835ee3418939f2676d6898489f8e047299f5f62ee0da3f8dd5dc
WINDOWS_ASSET=ffmpeg-n9.0.2-39-g7adfbe40dc-win64-lgpl-shared-9.0.zip
WINDOWS_SHA256=e907b01fa4783913151237e37df2a10fa97d99a1598a021bf59d4ae62ffed4c0
# The FFmpeg commit both assets were built from (n9.0.2-39-g7adfbe40dc).
SOURCE_COMMIT=7adfbe40dcde598f93a6963d2f7b22cd75202ccc
SOURCE_URL="https://github.com/FFmpeg/FFmpeg/archive/$SOURCE_COMMIT.tar.gz"
SOURCE_SHA256=4babda050dcfccb1194dd2a9e75b5e5ec1aa33b3e99d4f6a7106b6337b6b4a0c

# Downloads $1 to $2 and fails unless it has the SHA-256 $3.
download() {
  curl --proto '=https' --tlsv1.2 -sSfL -o "$2" "$1"
  echo "$3  $2" | sha256sum -c -
}

if [[ ${1:-} == notice ]]; then
  cat <<NOTICE
FFmpeg for Linux and Windows
The Linux and Windows builds use FFmpeg n9.0.2 (commit $SOURCE_COMMIT), LGPL version 3, as built by BtbN:
  $BASE/$LINUX_ASSET
  $BASE/$WINDOWS_ASSET
The matching FFmpeg source is attached as ffmpeg-source-btbn-$SOURCE_COMMIT.tar.gz.
The build scripts, with the configure options of each library (also stored in the libraries themselves), are
https://github.com/BtbN/FFmpeg-Builds/tree/$TAG (commit $TAG_COMMIT)
The builds link third-party libraries statically; their sources and licenses are obtained by those scripts at that
revision.
The libraries in resources/bin (libavcodec, libavformat, libavutil, libswscale and libswresample; avcodec-NN.dll and
the like on Windows) are linked dynamically: replace them with libraries of the same version and soname.
NOTICE
  exit 0
fi

if [[ ${1:-} == source ]]; then
  dir="${2:?usage: scripts/ffmpeg-fetch.sh source DIR}"
  mkdir -p "$dir"
  download "$SOURCE_URL" "$dir/ffmpeg-source-btbn-$SOURCE_COMMIT.tar.gz" "$SOURCE_SHA256"
  exit 0
fi

os="${1:-}"
dest="${2:?usage: scripts/ffmpeg-fetch.sh linux|win DEST}"
case "$os" in
linux) asset="$LINUX_ASSET" sha256="$LINUX_SHA256" ;;
win) asset="$WINDOWS_ASSET" sha256="$WINDOWS_SHA256" ;;
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
download "$BASE/$asset" "$work/$asset" "$sha256"

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
