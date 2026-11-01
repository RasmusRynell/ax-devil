#!/usr/bin/env bash
# Builds a minimal LGPL FFmpeg for macOS from its source tarball: shared libraries only, the formats and codecs the
# app and its test fixtures use, no TLS, nothing GPL or non-free.
#
#   scripts/ffmpeg-build-mac.sh PREFIX      installs include/ and lib/ under PREFIX, an empty or missing folder
#   scripts/ffmpeg-build-mac.sh notice      prints the license notice for this build
set -euo pipefail

VERSION=9.0.2
URL="https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz"
# ffmpeg.org publishes no checksums, only signatures; this is the hash of the tarball as first downloaded.
SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
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
)

if [[ ${1:-} == notice ]]; then
  cat <<NOTICE
The macOS build uses FFmpeg $VERSION, built from $URL
and linked dynamically (libavcodec, libavformat, libavutil, libswscale, libswresample), so each library can be
replaced by a build of the same version. FFmpeg is licensed under the LGPL version 2.1 or later; the configure options
were:
./configure ${CONFIGURE[*]}
NOTICE
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
curl --proto '=https' --tlsv1.2 -sSfL -o "$work/ffmpeg.tar.xz" "$URL"
echo "$SHA256  $work/ffmpeg.tar.xz" | shasum -a 256 -c -
mkdir "$work/src"
tar -xf "$work/ffmpeg.tar.xz" -C "$work/src" --strip-components=1

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
