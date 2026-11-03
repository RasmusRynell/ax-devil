"""Compare timestamps read directly from a video and VOD .od or MOTE .xml data.

Run from the project root (PyAV is already a project dependency):
    .venv/bin/python tools/show_timestamps.py /path/to/video /path/to/detections.od
    .venv/bin/python tools/show_timestamps.py /path/to/video /path/to/mote.xml

Outside this project, install PyAV with `pip install av`, then run with Python.

The table lists timestamps in milliseconds, in time order, with record counts
for each source. EXACT marks timestamps shared by a video frame and data sample.
For example, video frames every 40 ms and VOD samples every 100 ms coincide
at 200 ms, but the VOD sample at 100 ms falls between frames at 80 and 120 ms.
This compares timestamps only; it does not establish which image VOD processed.

Reads both files completely, including timestamps outside their shared range.
The footer shows video frame and data sample totals, plus shared exact timestamps.
A small table shows how far unmatched data samples are from the video frame
before and after them, with a separate sample count for each direction.
Matching uses exact timestamps, without tolerance or sticky overlay reuse.
To save the full table, append `> timestamps.txt` to the command above.
Video times are relative to the first frame, as in ax-devil. Data timestamps
are used as stored (nanoseconds). Exact fractions avoid floating-point errors.
Neither input file is modified.

The minimal OD reader expects 4-byte big-endian message lengths and a protobuf
Scene whose first field is the uint64 timestamp (field 1). It is specific to
this VOD format, not a general protobuf reader. MOTE files contain one XML
scene per line; frame time is in nanoseconds. Blank lines and INFO: headers
are skipped. Empty scenes still count as timestamped samples.
"""

import argparse
import struct
import sys
import xml.etree.ElementTree as ET
from bisect import bisect_left
from collections import Counter
from fractions import Fraction
from pathlib import Path

import av

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("video")
parser.add_argument("data", type=Path, help="VOD .od or MOTE .xml file")
args = parser.parse_args()
formats = {".od": "VOD", ".xml": "MOTE"}
if args.data.suffix.lower() not in formats:
    parser.error("Data must be a VOD .od or MOTE .xml file")
label = formats[args.data.suffix.lower()]

# Video presentation timestamps, relative to the first frame, as in ax-devil.
video: Counter[Fraction] = Counter()
origin: Fraction | None = None
with av.open(args.video) as container:
    for frame in container.decode(video=0):
        if frame.pts is None or frame.time_base is None:
            continue
        timestamp = frame.pts * frame.time_base
        if origin is None:
            origin = timestamp
        video[timestamp - origin] += 1

data: Counter[Fraction] = Counter()
if label == "MOTE":
    with args.data.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip() or line.lstrip().startswith("INFO:"):
                continue
            root = ET.fromstring(line.strip())
            mote_frame = root.find("{http://www.axis.com}frame")
            if mote_frame is None:
                raise ValueError("MOTE XML missing <frame> element")
            data[Fraction(int(mote_frame.attrib["time"]), 1_000_000_000)] += 1
else:
    # VOD: length-prefixed protobuf Scene, starting with a nanosecond timestamp.
    with args.data.open("rb") as source:
        while header := source.read(4):
            if len(header) != 4:
                raise ValueError("Incomplete OD message length")
            size = struct.unpack(">I", header)[0]
            message = source.read(size)
            if len(message) != size or not message or message[0] != 0x08:
                raise ValueError("Expected a complete VOD Scene with timestamp as its first field")
            timestamp_ns = 0
            for shift, byte in enumerate(message[1:]):
                timestamp_ns |= (byte & 0x7F) << (7 * shift)
                if byte < 0x80:
                    break
            else:
                raise ValueError("Incomplete timestamp")
            data[Fraction(timestamp_ns, 1_000_000_000)] += 1

# Counts also expose multiple records sharing a timestamp within either source.
sys.stdout.write(f"Timestamp (ms)   Video frames   {label} samples   Match\n")
sys.stdout.write("--------------   ------------   -----------   -----\n")
for timestamp in sorted(video | data):
    video_mark = str(video[timestamp]) if timestamp in video else ""
    data_mark = str(data[timestamp]) if timestamp in data else ""
    match = "EXACT" if timestamp in video and timestamp in data else ""
    sys.stdout.write(f"{float(timestamp * 1000):14.6f}   {video_mark:12}   {data_mark:11}   {match}\n")

sys.stdout.write(f"\nVideo frames: {sum(video.values())} | {label} samples: {sum(data.values())}\n")
sys.stdout.write(f"Exact matching timestamps: {len(video.keys() & data.keys())}\n")

video_times = sorted(video)
before: Counter[Fraction] = Counter()
after: Counter[Fraction] = Counter()
missing_before = 0
missing_after = 0
for timestamp, count in data.items():
    if timestamp in video:
        continue
    index = bisect_left(video_times, timestamp)
    if index > 0:
        before[(timestamp - video_times[index - 1]) * 1000] += count
    else:
        missing_before += count
    if index < len(video_times):
        after[(video_times[index] - timestamp) * 1000] += count
    else:
        missing_after += count

sys.stdout.write(f"\n{label} samples without an exact frame match\n")
sys.stdout.write("How far is each sample from the video frame before and after it?\n\n")
sys.stdout.write("Time gap         Frame before       Frame after\n")
for milliseconds in sorted(before.keys() | after.keys()):
    gap = f"{float(milliseconds):.6f}".rstrip("0").rstrip(".")
    sys.stdout.write(f"{gap:>8} ms      {before[milliseconds]:>5} samples      {after[milliseconds]:>5} samples\n")
if missing_before or missing_after:
    sys.stdout.write(f"No frame exists    {missing_before:>5} samples      {missing_after:>5} samples\n")
if not before and not after and not missing_before and not missing_after:
    sys.stdout.write(f"All {label} samples match a video frame exactly.\n")
