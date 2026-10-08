"""Simplified video metadata analyzer for frame count validation."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ax_devil.modules.cache import CacheManager
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Fingerprint:
    """File fingerprint for caching."""

    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def from_path(cls, p: Path) -> "Fingerprint":
        """Read the current source fingerprint, including same-process replacements."""
        st = p.stat()
        return cls(st.st_ino, st.st_size, st.st_mtime_ns)

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.inode, self.size, self.mtime_ns)


class VideoAnalyzer:
    """Simplified video analyzer for frame count validation."""

    _FRAME_RE = re.compile(r"frame=\s*(\d+)")

    # Default probing strategies in order of preference
    _PROBE_STRATEGIES = [
        dict(count_frames=False, count_packets=False, deep=False),
        dict(count_frames=False, count_packets=False, deep=True),
        dict(count_frames=True, count_packets=False, deep=False),
        dict(count_frames=True, count_packets=False, deep=True),
        dict(count_frames=False, count_packets=True, deep=False),
        dict(count_frames=False, count_packets=True, deep=True),
    ]

    def __init__(
        self,
        *,
        ffprobe: Path | str = "ffprobe",
        ffmpeg: Path | str = "ffmpeg",
        cache_suffix: str = ".meta.json",
        deep_args: tuple[str, ...] = ("-probesize", "500M", "-analyzeduration", "500M"),
    ) -> None:
        self.ffprobe = str(ffprobe)
        self.ffmpeg = str(ffmpeg)
        self.cache_suffix = cache_suffix
        self.deep_args = deep_args

        # Use CacheManager for unified cache management
        self.cache_manager = CacheManager()
        self._logger = logger

    def _run(self, cmd: Sequence[str]) -> str:
        """Run a subprocess command and return output."""
        try:
            return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(e.output.strip()) from e

    def _ffprobe_stream(
        self,
        path: Path,
        *,
        count_frames: bool = False,
        count_packets: bool = False,
        deep: bool = False,
    ) -> dict[str, Any] | None:
        """Use ffprobe to get video stream information."""
        cmd: list[str] = [
            self.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate,avg_frame_rate,nb_frames,nb_read_frames,nb_read_packets,width,height,duration",
            "-of",
            "json",
        ]
        if count_frames:
            cmd.append("-count_frames")
        if count_packets:
            cmd.append("-count_packets")
        if deep:
            cmd += list(self.deep_args)
        cmd.append(str(path))

        try:
            raw = self._run(cmd)
            data = json.loads(raw or "{}")
        except (RuntimeError, json.JSONDecodeError):
            return None

        streams = data.get("streams", [])
        if not streams:
            return None
        s = streams[0]

        fps = self._parse_frame_rate(s.get("avg_frame_rate")) or self._parse_frame_rate(s.get("r_frame_rate"))

        # Extract frame count from various possible fields
        nb_frames = s.get("nb_frames")
        nb_read_frames = s.get("nb_read_frames")
        nb_read_packets = s.get("nb_read_packets") if count_packets else 0
        frames = int(nb_frames or nb_read_frames or nb_read_packets or 0)
        duration = float(s.get("duration") or (frames / fps if fps else 0.0))
        if not frames:
            return None

        tag = "ffprobe"
        if count_frames:
            tag += " -count_frames"
        if count_packets:
            tag += " -count_packets"
        if deep:
            tag += " (deep)"

        return {
            "source": tag,
            "fps": fps,
            "frame_count": frames,
            "width": int(s.get("width", 0)),
            "height": int(s.get("height", 0)),
            "duration_sec": duration,
        }

    def _parse_frame_rate(self, raw: Any) -> float:
        """Parse an ffprobe frame-rate field."""
        if raw is None:
            return 0.0
        try:
            num, den = map(int, str(raw).split("/"))
        except ValueError:
            return 0.0
        return num / den if den else 0.0

    def _ffmpeg_count(self, path: Path) -> dict[str, Any] | None:
        """Use ffmpeg to count frames by copying to null muxer."""
        cmd = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-progress",
            "pipe:1",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-f",
            "null",
            "-",
        ]
        try:
            out = self._run(cmd)
        except RuntimeError as e:
            self._logger.error(f"ffmpeg frame counting failed for {path}: {e}")
            return None

        matches = self._FRAME_RE.findall(out)
        frames: int | None = int(matches[-1]) if matches else None

        progress_entries: list[dict[str, str]] = []
        current_entry: dict[str, str] = {}
        for raw_line in out.strip().splitlines():
            if "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            key = key.strip()
            current_entry[key] = value.strip()
            if key == "progress":
                progress_entries.append(current_entry)
                current_entry = {}
        progress_info = progress_entries[-1] if progress_entries else {}

        if frames is None:
            frame_value = progress_info.get("frame")
            if frame_value is not None:
                try:
                    frames = int(frame_value)
                except ValueError:
                    frames = None

        meta = self._ffprobe_stream(path, deep=True) or {}
        fps = meta.get("fps", 0.0)

        if frames is None or frames <= 0:
            frames = int(meta.get("frame_count") or 0)

        if (frames is None or frames <= 0) and fps:
            out_time_us = progress_info.get("out_time_us")
            if out_time_us is not None:
                try:
                    frames = max(1, int(round(int(out_time_us) / 1_000_000 * fps)))
                except ValueError:
                    frames = None

        if frames is None or frames <= 0:
            preview = "\n".join(out.strip().splitlines()[-10:])
            message = "ffmpeg progress output did not include a usable frame count."
            self._logger.error(f"{message} Command: {' '.join(cmd)} Output preview:\n{preview or '<empty>'}")
            raise RuntimeError(f"{message} See logs for details.")

        return {
            "source": "ffmpeg-null",
            "fps": fps,
            "frame_count": frames,
            "width": meta.get("width", 0),
            "height": meta.get("height", 0),
            "duration_sec": frames / fps if fps else 0.0,
        }

    def _pyav_probe(self, path: Path) -> dict[str, Any] | None:
        """Use PyAV to extract metadata as final fallback."""
        try:
            import av

            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                fps = float(stream.average_rate) if stream.average_rate else 0.0
                width = stream.width
                height = stream.height

                # Count frames by iterating through packets
                frame_count = 0
                for packet in container.demux(stream):
                    for frame in packet.decode():
                        frame_count += 1

                if fps > 0 and width > 0 and height > 0 and frame_count > 0:
                    return {
                        "source": "pyav",
                        "fps": fps,
                        "frame_count": frame_count,
                        "width": width,
                        "height": height,
                        "duration_sec": frame_count / fps,
                    }
        except Exception:
            return None
        return None

    def _is_valid_metadata(self, meta: dict[str, Any]) -> bool:
        """Validate metadata has required fields with valid values."""
        required_fields = ["fps", "frame_count", "width", "height"]
        for field in required_fields:
            value = meta.get(field, 0)
            if not isinstance(value, (int, float)) or value <= 0:
                return False
        return True

    def _get_cache_path(self, p: Path) -> Path:
        """Get cache file path for a video file using unified CacheManager."""
        return self.cache_manager.get_cache_path("video_metadata", p, self.cache_suffix)

    def _load_cache(self, p: Path) -> dict[str, Any] | None:
        """Load cached metadata if available and valid."""
        cache_file = self._get_cache_path(p)
        if not cache_file.exists():
            return None
        try:
            cached = json.loads(cache_file.read_text())
            if tuple(cached["fingerprint"]) == Fingerprint.from_path(p).as_tuple():
                meta: dict[str, Any] = cached["metadata"]
                meta["source"] += " (cache)"
                return meta
        except (OSError, KeyError, json.JSONDecodeError, ValueError):
            pass
        return None

    def _save_cache(self, p: Path, meta: dict[str, Any]) -> None:
        """Save metadata to cache file."""
        cache_file = self._get_cache_path(p)

        payload = {
            "fingerprint": Fingerprint.from_path(p).as_tuple(),
            "metadata": meta,
        }
        tmp_fd, tmp_name = tempfile.mkstemp(dir=cache_file.parent, prefix=".meta_tmp_", text=True)
        try:
            with open(tmp_fd, "w") as fh:
                json.dump(payload, fh, indent=2)
            Path(tmp_name).rename(cache_file)
        except (OSError, json.JSONDecodeError):
            try:
                Path(tmp_name).unlink()
            except OSError:
                pass
            raise

    def analyze(self, path: Path, *, refresh: bool = False) -> dict[str, Any]:
        """Analyze video file and return metadata."""
        if not path.exists():
            raise FileNotFoundError(path)

        meta = None if refresh else self._load_cache(path)
        if meta:
            return meta

        # Try different strategies until one works
        for strat in self._PROBE_STRATEGIES:
            meta = self._ffprobe_stream(path, **strat)
            if meta and self._is_valid_metadata(meta):
                self._save_cache(path, meta)
                return meta

        # Second fallback: use ffmpeg
        try:
            meta = self._ffmpeg_count(path)
        except RuntimeError as exc:
            self._logger.error(f"ffmpeg frame counting failed for {path}: {exc}")
            meta = None
        if meta and self._is_valid_metadata(meta):
            self._save_cache(path, meta)
            return meta

        # Final fallback: use PyAV
        meta = self._pyav_probe(path)
        if meta and self._is_valid_metadata(meta):
            self._save_cache(path, meta)
            return meta

        raise RuntimeError("All probing methods failed")
