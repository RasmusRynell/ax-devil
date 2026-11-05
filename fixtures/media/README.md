# Media fixtures

`two_video_streams.mkv`: two video streams, the first 64x48 with 20 frames, the second 128x96 with 30 frames. Frame N
of each is flat luma (7 N) mod 256, as in `fixtures/video/generate.sh`, whose `luma` source and `PLAIN` and `H264`
options this command repeats. Generated for this repository (MIT) with FFmpeg n9.0.x (libx264):

```bash
luma() { printf "color=c=gray:s=%s:r=30,format=yuv420p,geq=lum='mod(N*7\\,256)':cb=128:cr=128" "$1"; }
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "$(luma 64x48)" -f lavfi -i "$(luma 128x96)" \
  -an -map_metadata -1 -fflags +bitexact -flags:v +bitexact -map 0:v -map 1:v -frames:v:0 20 -frames:v:1 30 \
  -disposition:v:0 0 -disposition:v:1 default -c:v libx264 -qp 1 -pix_fmt yuv420p \
  -x264-params keyint=10:min-keyint=10:scenecut=0:bframes=0 two_video_streams.mkv
```

The second stream carries the default flag because FFmpeg's best-stream choice prefers it; without the flag it also
picks the first stream and the test would not tell the two choices apart.
