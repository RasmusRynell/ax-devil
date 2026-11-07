#!/usr/bin/env bash
# Regenerates the committed MOT Challenge dataset by hand-written rows plus copies of the image sequence made by
# ../../video/generate.sh. Every file is generated for this repository, so it has no external provenance (see
# ../../README.md).
#
# Layout (the MOT Challenge one, 12 frames of 64x48 at 10 fps in every sequence):
#   train/MINI-01-DPM    images, det and gt   -> entry MINI-01, lanes DPM and GT
#   train/MINI-01-FRCNN  det only             -> lane FRCNN (an entry plays the first variant's images)
#   test/MINI-02         images and det       -> entry MINI-02, lane DET
# File frame 6 (video frame 5) has no rows in any file, so the files are dense around an empty frame.
# Rows are in 64x48 pixels. Untracked detections have track id -1. Ground truth has classes 1 (person),
# 7 (static person) and 99 (no such MOT class). DPM's det.txt has one malformed row (line 7, a box width that is not
# a number), so the lane warns about one skipped row.
set -euo pipefail
cd "$(dirname "$0")"

SEQ=../../video/seq
rm -rf train test

sequence() { # dir name
  mkdir -p "$1/det"
  printf '[Sequence]\nname=%s\nimDir=img1\nframeRate=10\nseqLength=12\nimWidth=64\nimHeight=48\nimExt=.jpg\n' "$2" \
    >"$1/seqinfo.ini"
}
images() { mkdir -p "$1/img1" && cp "$SEQ"/*.jpg "$1/img1/"; }

# Detections: frame,id,left,top,width,height,score,-1,-1,-1. Frames 1 to 5 and 7 to 12; a box walks right by 1 px a frame.
detections() { # dir left0 top0
  local frame left
  for frame in 1 2 3 4 5 7 8 9 10 11 12; do
    left=$(($2 + frame))
    printf '%d,-1,%d,%d,14,28,0.%d,-1,-1,-1\n' "$frame" "$left" "$3" $((95 - frame))
    printf '%d,3,%d,%d,16,24,0.%d,-1,-1,-1\n' "$frame" $((left + 30)) "$(($3 + 2))" $((50 + frame))
  done >"$1/det/det.txt"
}

sequence train/MINI-01-DPM MINI-01-DPM
images train/MINI-01-DPM
detections train/MINI-01-DPM 4 6
# Line 7 is frame 4's first row after frames 1 to 3: damage its width.
sed -i '7s/,14,28,/,wide,28,/' train/MINI-01-DPM/det/det.txt

mkdir -p train/MINI-01-DPM/gt
{
  for frame in 1 2 3 4 5 7 8 9 10 11 12; do
    printf '%d,1,%d,6,14,28,1,1,1\n' "$frame" $((4 + frame))
    printf '%d,2,%d,34,16,10,1,7,0.5\n' "$frame" $((10 + frame))
    printf '%d,3,%d,2,8,8,0,99,0.2\n' "$frame" $((48 - frame))
  done
} >train/MINI-01-DPM/gt/gt.txt

sequence train/MINI-01-FRCNN MINI-01-FRCNN
detections train/MINI-01-FRCNN 0 14

sequence test/MINI-02 MINI-02
images test/MINI-02
detections test/MINI-02 0 20
