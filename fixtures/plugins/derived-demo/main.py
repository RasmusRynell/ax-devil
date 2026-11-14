#!/usr/bin/env python3
"""A derived-lane decoder and a task over lane files, the model plugin for `input: lanes`.

The request names each input lane's file (JSON Lines: a `lane` line, then a `sample` line per stored sample).
`decode` answers with one sample per frame that either lane has, the `model` lane's where both have one; `task`
counts a lane's samples and reports it as progress. The task's last progress text echoes the plugin setting and the
option exactly as received. See the write-plugin skill for the format.
"""

import json
import sys


def emit(line):
    print(json.dumps(line, separators=(",", ":")), flush=True)


def samples(path):
    """The sample lines of a lane file, by the frame they start at."""
    found = {}
    with open(path, encoding="utf-8") as lane:
        for line in lane:
            sample = json.loads(line).get("sample")
            if sample is not None:
                found[sample["time"]["start"]["frame"]] = sample
    return found


request = json.loads(sys.stdin.readline())
lanes = request["input"]["lanes"]

if request["op"] == "decode":
    merged = samples(lanes["truth"])
    merged.update(samples(lanes["model"]))
    for frame in sorted(merged):
        emit({"sample": merged[frame]})
elif request["op"] == "task":
    count = len(samples(lanes["lane"]))
    emit({"progress": {"done": 0, "total": count, "text": "Counting"}})
    if request["options"].get("out", "").endswith("/fail"):
        emit({"error": {"reason": "Nothing can be saved there"}})
        sys.exit(0)
    emit({"progress": {"done": count, "total": count, "text": f"{request['settings']['label']}|{request['options'].get('out', '')}"}})
    emit({"done": {}})
else:
    emit({"error": {"reason": f"Unknown operation {request['op']}"}})
