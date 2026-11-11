"""Accepted metadata at the object budget, shared by ingestion and dialog fit regressions."""
import json
import sys

json.loads(sys.stdin.readline())
metadata = {f"field{n:02}": f"Camera calibration measurement {n}" for n in range(31)}
metadata["description"] = "Recorded at the loading dock during the evening calibration. " * 240
metadata["path"] = "/recordings/" + "camera_calibration_2026_" * 4 + ".mp4"
print(json.dumps({"playlist": {"name": "Calibration", "metadata": metadata}}))
print(json.dumps({"entry": {"name": "Clip", "video": {"file": metadata["path"]}}}))
