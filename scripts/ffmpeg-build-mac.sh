#!/usr/bin/env bash
# Builds a minimal LGPL FFmpeg for macOS from its source tarball: shared libraries only, the formats and codecs the
# app and its test fixtures use plus what export writes (PNG, H.264 in MP4), no TLS, nothing GPL or non-free.
#
#   scripts/ffmpeg-build-mac.sh PREFIX      installs include/ and lib/ under PREFIX, an empty or missing folder
#   scripts/ffmpeg-build-mac.sh source DIR  puts the source tarball it builds from, ffmpeg-source-macos-<version>.tar.gz, in DIR
#   scripts/ffmpeg-build-mac.sh notice      prints the license notice for this build
set -euo pipefail

VERSION=9.0.2
# ffmpeg.org often times out from CI runners; this is the release tag on FFmpeg's GitHub mirror, as scripts/ffmpeg-fetch.sh uses.
URL="https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n$VERSION.tar.gz"
SHA256=6e374ed621e48faa40639307dff48ba6fe574a509977956d2cce9669b7cc27e9
# The install names are @rpath/..., so the backend finds the libraries from its own rpath.
# shellcheck disable=SC2054 # the commas belong to the options
CONFIGURE=(
  --enable-shared --disable-static --disable-programs --disable-doc --disable-autodetect --disable-debug
  --enable-network --disable-everything --disable-avdevice --disable-avfilter --enable-zlib
  --install-name-dir=@rpath
  --enable-protocol=file,tcp,udp,rtp,http
  --enable-demuxer=mov,matroska,nut,avi,mpegts,h264,hevc,image2,mjpeg,rtsp,rtp,sdp
  --enable-decoder=h264,hevc,mjpeg,png,rawvideo,mpeg4,vp9
  --enable-parser=h264,hevc,mjpeg,png,vp9
  # Export: PNG stills, JPEG thumbnails, and H.264 in MP4 through the system encoder (x264 is GPL; none is built here).
  --enable-encoder=png,mjpeg,h264_videotoolbox --enable-muxer=mp4 --enable-videotoolbox
)

# Downloads $1 to $2 and fails unless it has the SHA-256 $3.
download() {
  curl --proto '=https' --tlsv1.2 -sSfL --retry 10 --retry-delay 20 --retry-all-errors --connect-timeout 30 -o "$2" "$1"
  if command -v sha256sum >/dev/null; then
    echo "$3  $2" | sha256sum -c -
  else
    echo "$3  $2" | shasum -a 256 -c -
  fi
}

if [[ ${1:-} == notice ]]; then
  cat <<NOTICE
FFmpeg for macOS
The macOS build uses FFmpeg $VERSION, LGPL version 2.1 or later, built from the official source $URL
(attached as ffmpeg-source-macos-$VERSION.tar.gz) by scripts/ffmpeg-build-mac.sh with
./configure ${CONFIGURE[*]}
The libraries in Contents/Resources/bin (libavcodec, libavformat, libavutil, libswscale and libswresample) are linked
dynamically: replace them with libraries of the same version and install name.
NOTICE
  exit 0
fi

if [[ ${1:-} == source ]]; then
  dir="${2:?usage: scripts/ffmpeg-build-mac.sh source DIR}"
  mkdir -p "$dir"
  download "$URL" "$dir/ffmpeg-source-macos-$VERSION.tar.gz" "$SHA256"
  exit 0
fi

prefix="${1:?usage: scripts/ffmpeg-build-mac.sh PREFIX}"
if [[ -n $(ls -A "$prefix" 2>/dev/null) ]]; then
  echo "$prefix is not empty" >&2
  exit 1
fi
mkdir -p "$prefix"
prefix="$(cd "$prefix" && pwd)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
download "$URL" "$work/ffmpeg.tar.gz" "$SHA256"
mkdir "$work/src"
tar -xf "$work/ffmpeg.tar.gz" -C "$work/src" --strip-components=1

cd "$work/src"
./configure --prefix="$prefix" "${CONFIGURE[@]}" | tee "$work/configure.log"
# A GPL or non-free build must never ship; the summary names the license.
grep -q '^License: LGPL version 2.1 or later' "$work/configure.log" || {
  echo "configure did not report an LGPL build" >&2
  exit 1
}
make -j"$(sysctl -n hw.ncpu 2>/dev/null || nproc)"
make install
cp COPYING.LGPLv2.1 "$prefix/LICENSE.txt"
