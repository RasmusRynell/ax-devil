#!/usr/bin/env python3
"""A playlist resolver: one entry per run folder under `root`, each with its video and, when it has one, `gt.txt`.

The model resolver plugin: read the request line from stdin, answer `scan` with a `choice` per run folder and
`resolve` with a `playlist` line and an `entry` per run. A problem with a setting is an `error` naming it. Folders
are listed, never opened as media. See the write-plugin skill for the format.
"""

import json
import os
import sys


def emit(line):
    print(json.dumps(line, separators=(",", ":")), flush=True)


def runs(root):
    return sorted(name for name in os.listdir(root) if os.path.isdir(os.path.join(root, name)))


request = json.loads(sys.stdin.readline())
root = request["settings"].get("root", "")
if not os.path.isdir(root):
    emit({"error": {"reason": "Not a folder", "field": "root"}})
    sys.exit(0)

if request["op"] == "scan":
    for name in runs(root):
        emit({"choice": {"value": name, "detail": f"{len(os.listdir(os.path.join(root, name)))} files"}})
elif request["op"] == "resolve":
    chosen = request["settings"].get("runs")
    emit({"playlist": {"name": os.path.basename(root)}})
    for name in runs(root):
        if chosen is not None and name not in chosen:
            continue
        folder = os.path.join(root, name)
        truth = os.path.join(folder, "gt.txt")
        overlays = []
        if os.path.isfile(truth):
            overlays.append({"label": "GT", "slot": "gt", "path": truth, "handler": "MOT_FILE"})
        else:
            emit({"warning": {"problem": f"{name} has no gt.txt", "consequence": "It opens without overlays."}})
        emit({"entry": {"name": name, "video": {"file": os.path.join(folder, "video.mp4")}, "overlays": overlays}})
else:
    emit({"error": {"reason": f"Unknown operation {request['op']}"}})
