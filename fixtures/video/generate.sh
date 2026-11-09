#!/usr/bin/env bash
# Regenerates the committed test videos with the FFmpeg command line (n9.0.x with libx264 and libx265).
# Every file is made by this script from synthetic sources, so it has no external provenance (see ../README.md).
#
# Frame N of every luma-coded video is a flat picture whose luma is exactly (7 * N) mod 256. Flat pictures at QP 1
# decode to exactly that (x264 and x265 switch B-frames off in lossless mode, which these fixtures need), so a
# test can tell which frame it decoded from the mean of the Y plane. 7 is coprime to 256, so the first 256 frames
# all differ.
set -euo pipefail
cd "$(dirname "$0")"

GLOBAL=(-hide_banner -loglevel error -y)
# Output options that keep encoder versions and other metadata out of the files.
PLAIN=(-an -map_metadata -1 -fflags +bitexact -flags:v +bitexact)
# A flat luma-coded source: frame size, frame rate.
luma() { printf "color=c=gray:s=%s:r=%s,format=yuv420p,geq=lum='mod(N*7\\,256)':cb=128:cr=128" "$1" "$2"; }
H264=(-c:v libx264 -qp 1 -pix_fmt yuv420p)

# 64x48, 30 fps, 90 frames, a GOP of 10 with two B-frames between references.
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(luma 64x48 30)" "${PLAIN[@]}" -frames:v 90 "${H264[@]}" \
  -x264-params keyint=10:min-keyint=10:scenecut=0:bframes=2:b-adapt=0 -movflags +faststart cfr_h264_gop10_bf2.mp4

# A GOP of 60, 180 frames: seeking has to decode up to 59 frames to reach a target.
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(luma 64x48 30)" "${PLAIN[@]}" -frames:v 180 "${H264[@]}" \
  -x264-params keyint=60:min-keyint=60:scenecut=0:bframes=2:b-adapt=0 -movflags +faststart long_gop_h264.mp4

# Open GOP: the B-frames after each non-IDR I-frame in decoding order are shown before it and refer to the
# previous group.
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(luma 64x48 30)" "${PLAIN[@]}" -frames:v 90 "${H264[@]}" \
  -x264-params open-gop=1:keyint=30:min-keyint=30:scenecut=0:bframes=3:b-adapt=0 open_gop_h264.mkv

# Variable frame rate: frame N is shown at (33 * N + 17 * floor(N / 10)) milliseconds, 60 frames, so every tenth
# frame comes 50 ms after the one before it and the others 33 ms after.
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(luma 64x48 1000)" "${PLAIN[@]}" -frames:v 60 -vf "setpts='33*N+17*trunc(N/10)'" \
  -fps_mode passthrough "${H264[@]}" -x264-params keyint=20:min-keyint=20:scenecut=0:bframes=2:b-adapt=0 vfr_h264.mkv

# A second codec.
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(luma 64x48 30)" "${PLAIN[@]}" -frames:v 90 -c:v libx265 -qp 1 -pix_fmt yuv420p \
  -x265-params keyint=30:min-keyint=30:scenecut=0:bframes=2:b-adapt=0:log-level=error \
  -movflags +faststart -tag:v hvc1 hevc_gop30.mp4

# The first frame's container timestamp is 10 s, not 0.
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(luma 64x48 30)" "${PLAIN[@]}" -frames:v 60 "${H264[@]}" \
  -x264-params keyint=10:min-keyint=10:scenecut=0:bframes=2:b-adapt=0 -output_ts_offset 10 -movflags +faststart pts_offset.mp4

# The 90-frame video cut inside its last packet: the container lists every frame, the last cannot be decoded.
cut=$(( $(stat -c %s cfr_h264_gop10_bf2.mp4) - 30 ))
head -c "$cut" cfr_h264_gop10_bf2.mp4 > truncated.mp4

# An 8x8 picture of 4x4-pixel blocks, alternately luma 16 and 235 (black and white in full range): enlarged on screen
# its block edges show whether the viewer samples the nearest source pixel or blends neighbours.
ffmpeg "${GLOBAL[@]}" -f lavfi -i "color=c=gray:s=8x8:r=30,format=yuv420p,geq=lum='if(mod(floor(X/4)+floor(Y/4)\\,2)\\,235\\,16)':cb=128:cr=128" \
  "${PLAIN[@]}" -frames:v 30 "${H264[@]}" -x264-params keyint=10:min-keyint=10:scenecut=0:bframes=0 -movflags +faststart checker_h264_8x8.mp4

# Not a video at all.
printf 'This file is named like a video but holds only text.\n' > not_video.mp4

# One flat colour, 0x4080C0, as 10-bit 4:4:4 BT.709 limited range at width 18 (not a multiple of 16), and as raw
# 24-bit RGB at width 16. Both must decode to RGBA (0x40, 0x80, 0xC0, 0xFF).
rgb() { printf "color=c=0x4080C0:s=%s:r=30,format=rgb24" "$1"; }
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(rgb 18x16)" "${PLAIN[@]}" -frames:v 4 \
  -vf "scale=out_color_matrix=bt709:out_range=tv,format=yuv444p10le" -c:v libx264 -qp 0 \
  -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv yuv444p10_w18.mkv
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(rgb 16x16)" "${PLAIN[@]}" -frames:v 4 -c:v rawvideo -pix_fmt rgb24 rgb24_w16.nut
# The same colour at 63x47, a size no 4:2:0 video can have: a still export must keep it.
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(rgb 63x47)" "${PLAIN[@]}" -frames:v 1 -c:v rawvideo -pix_fmt rgb24 rgb24_63x47.nut

# An image sequence: 12 JPEGs, 64x48, numbered from 1 as %06d.jpg, image k showing frame k - 1 of the luma scheme
# above (full-range JPEG, quality 1, so the flat luma survives). Playback gives them a frame rate; the files hold none.
mkdir -p seq
rm -f seq/*.jpg
ffmpeg "${GLOBAL[@]}" -f lavfi \
  -i "$(luma 64x48 30),setparams=range=pc,format=yuvj420p" "${PLAIN[@]}" -frames:v 12 \
  -c:v mjpeg -pix_fmt yuvj420p -q:v 1 -start_number 1 seq/%06d.jpg

# Raw Annex B streams, 25 fps, a keyframe every 5 frames and no B-frames, with parameter sets in every keyframe: the
# access units of a live stream (read back as packets by crates/media/src/stream.rs).
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(luma 64x48 25)" "${PLAIN[@]}" -frames:v 12 "${H264[@]}" \
  -x264-params keyint=5:min-keyint=5:scenecut=0:bframes=0 -f h264 stream_h264_gop5.h264
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(luma 96x64 25)" "${PLAIN[@]}" -frames:v 1 "${H264[@]}" \
  -x264-params keyint=5:min-keyint=5:scenecut=0:bframes=0 -f h264 stream_h264_96x64.h264
ffmpeg "${GLOBAL[@]}" -f lavfi -i "$(luma 64x48 25)" "${PLAIN[@]}" -frames:v 12 -c:v libx265 -qp 1 -pix_fmt yuv420p \
  -x265-params keyint=5:min-keyint=5:scenecut=0:bframes=0:open-gop=0:repeat-headers=1:info=0:log-level=error \
  -f hevc stream_hevc_gop5.hevc
