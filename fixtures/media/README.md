# Media fixtures

`two_video_streams.mkv`: two video streams, the first 64x48, the second 128x96, each with 20 frames. Frame N
of each is flat luma (7 N) mod 256, as in `fixtures/video/generate.sh`, whose `luma` source and `PLAIN` and `H264`
options this command repeats. Generated for this repository (MIT) with FFmpeg n9.0.x (libx264):

```bash
luma() { printf "color=c=gray:s=%s:r=30,format=yuv420p,geq=lum='mod(N*7\\,256)':cb=128:cr=128" "$1"; }
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "$(luma 64x48)" -f lavfi -i "$(luma 128x96)" \
  -an -map_metadata -1 -fflags +bitexact -flags:v +bitexact -map 0:v -map 1:v -frames:v 20 \
  -disposition:v:0 0 -disposition:v:1 default -c:v libx264 -qp 1 -pix_fmt yuv420p \
  -x264-params keyint=10:min-keyint=10:scenecut=0:bframes=0 two_video_streams.mkv
```

The second stream carries the default flag because FFmpeg's best-stream choice prefers it; without the flag it also
picks the first stream and the test would not tell the two choices apart.

`cover_art_first.mp4`: the 64x48 video of 20 frames and, listed before it, a 16x16 JPEG cover picture (an
`attached_pic` stream). The MP4 muxer writes the cover last, so the second step moves the `udta` box (the cover) ahead
of the first `trak` box inside `moov`; the size is unchanged, so no sample offset moves. Same generator and license:

```bash
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "$(luma 16x16)" -frames:v 1 -fflags +bitexact cover.jpg
ffmpeg -hide_banner -loglevel error -y -i cover.jpg -f lavfi -i "$(luma 64x48)" \
  -an -map_metadata -1 -fflags +bitexact -flags:v +bitexact -map 1:v -map 0:v -frames:v:0 20 \
  -c:v:0 libx264 -qp 1 -pix_fmt yuv420p -x264-params keyint=10:min-keyint=10:scenecut=0:bframes=0 \
  -c:v:1 copy -disposition:v:1 attached_pic cover_last.mp4
python3 - <<'PY'
import struct
data = open("cover_last.mp4", "rb").read()
def boxes(start, end):
    while start < end:
        size, kind = struct.unpack(">I4s", data[start:start + 8])
        yield kind, start, start + size
        start += size
_, moov_start, moov_end = next(b for b in boxes(0, len(data)) if b[0] == b"moov")
kids = list(boxes(moov_start + 8, moov_end))
_, udta_start, udta_end = next(k for k in kids if k[0] == b"udta")
_, trak_start, _ = next(k for k in kids if k[0] == b"trak")
moved = data[:trak_start] + data[udta_start:udta_end] + data[trak_start:udta_start] + data[udta_end:]
open("cover_art_first.mp4", "wb").write(moved)
PY
```
