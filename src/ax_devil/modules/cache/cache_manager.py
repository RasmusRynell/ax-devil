"""Cache management utilities for ax-devil application."""

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from ax_devil.modules.settings.config_manager import ConfigManager
from ax_devil.modules.settings.logging_config import get_logger

logger = get_logger(__name__)


class CacheInfo:
    """Represents information about a cache directory."""

    def __init__(self, path: Path, cache_type: str = "general"):
        self.path = path
        self.cache_type = cache_type
        self.files: list[Path] = []
        self.total_size = 0
        self.file_count = 0

    def scan(self) -> None:
        """Scan the cache directory for files and calculate sizes."""
        if not self.path.exists():
            return

        self.files = []
        self.total_size = 0
        self.file_count = 0

        try:
            for file_path in self.path.rglob("*"):
                if file_path.is_file():
                    self.files.append(file_path)
                    try:
                        self.total_size += file_path.stat().st_size
                        self.file_count += 1
                    except OSError:
                        # File might be inaccessible, skip it
                        continue
        except OSError as e:
            logger.warning(f"Could not scan cache directory {self.path}: {e}")


@dataclass(frozen=True)
class CacheStatsSnapshot:
    """Represents a scoped view of cache statistics."""

    all: dict[str, CacheInfo]
    scoped: dict[str, CacheInfo]


class CacheManager:
    """Manages cache discovery and clearing operations."""

    def __init__(self) -> None:
        self.config_manager = ConfigManager()

    def generate_cache_filename(self, source_path: Path, suffix: str, content_hash: str | None = None) -> str:
        """Generate unique cache filename for a source file.

        Args:
            source_path: Path to source file
            suffix: File extension (e.g., '.ptsidx', '.json')
            content_hash: Optional content-based hash for cache invalidation

        Returns:
            Cache filename like 'video.mp4-a3f2b89c.ptsidx' or
            'video.mp4-a3f2b89c-content123.ptsidx' if content_hash provided
        """
        # Use absolute path for uniqueness across different directories
        abs_path = source_path.resolve()
        path_hash = hashlib.sha256(str(abs_path).encode()).hexdigest()[:16]
        safe_name = source_path.name.replace("/", "_").replace("\\", "_")

        # Add content hash if provided for cache invalidation
        if content_hash:
            return f"{safe_name}-{path_hash}-{content_hash[:16]}{suffix}"
        else:
            return f"{safe_name}-{path_hash}{suffix}"

    def get_cache_path(self, cache_type: str, source_path: Path, suffix: str, content_hash: str | None = None) -> Path:
        """Get full cache file path for a source file.

        Args:
            cache_type: Cache subdirectory ('frame_index', 'video_metadata', etc.)
            source_path: Path to source file
            suffix: File extension
            content_hash: Optional content-based hash for cache invalidation

        Returns:
            Full path like ~/.ax_devil/caches/frame_index/video.mp4-a3f2b89c.ptsidx or
            ~/.ax_devil/caches/frame_index/video.mp4-a3f2b89c-content123.ptsidx
        """
        cache_dir = self.get_cache_subdir(cache_type)
        filename = self.generate_cache_filename(source_path, suffix, content_hash)
        return cache_dir / filename

    def get_cache_subdir(self, cache_type: str) -> Path:
        """Get cache subdirectory, creating if needed."""
        subdir = self._get_base_cache_dir() / cache_type
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir

    def get_cache_directories(self) -> dict[str, CacheInfo]:
        """Discover all cache directories dynamically."""
        cache_dirs = {}
        base_dir = self._get_base_cache_dir()

        # Main/General cache directory
        cache_dirs["main"] = CacheInfo(base_dir, "main")

        # Auto-discover cache subdirectories (skip hidden directories)
        if base_dir.exists():
            for subdir in base_dir.iterdir():
                if subdir.is_dir() and not subdir.name.startswith("."):
                    cache_dirs[subdir.name] = CacheInfo(subdir, subdir.name)

        return cache_dirs

    def get_cache_stats(self, cache_type: str | None = None) -> CacheStatsSnapshot:
        """Get current cache statistics, optionally filtered by cache type."""

        cache_dirs = self.get_cache_directories()

        for cache_info in cache_dirs.values():
            cache_info.scan()

        scoped_dirs = cache_dirs if cache_type is None else self._filter_cache_stats(cache_dirs, cache_type)

        return CacheStatsSnapshot(all=cache_dirs, scoped=scoped_dirs)

    def _filter_cache_stats(self, cache_stats: dict[str, CacheInfo], cache_type: str | None) -> dict[str, CacheInfo]:
        """Filter cache statistics by cache type."""

        if not cache_type:
            return cache_stats

        return {name: info for name, info in cache_stats.items() if info.cache_type == cache_type}

    def aggregate_cache_stats(self, cache_stats: dict[str, CacheInfo]) -> tuple[int, int]:
        """Aggregate cache statistics into totals."""

        total_files = sum(info.file_count for info in cache_stats.values())
        total_size = sum(info.total_size for info in cache_stats.values())
        return total_files, total_size

    def format_cache_summary(self, cache_stats: dict[str, CacheInfo], include_total: bool = True) -> str:
        """Create a human readable summary for cache statistics."""

        lines: list[str] = []
        total_files = 0
        total_size = 0

        for name, info in cache_stats.items():
            if info.file_count == 0:
                lines.append(f"  {name}: Empty")
                continue

            size_str = self.format_size(info.total_size)
            lines.append(f"  {name}: {info.file_count} files, {size_str}")
            total_files += info.file_count
            total_size += info.total_size

        if include_total:
            lines.append(f"Total: {total_files} files, {self.format_size(total_size)}")

        return "\n".join(lines)

    def format_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        if size_bytes == 0:
            return "0 B"

        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(size_bytes)
        unit_index = 0

        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1

        if unit_index == 0:
            return f"{int(size)} {units[unit_index]}"
        else:
            return f"{size:.1f} {units[unit_index]}"

    def clear_cache(self, cache_type: str | None = None, force: bool = False) -> tuple[bool, str]:
        """Clear cache directories.

        Args:
            cache_type: Specific cache type to clear, or None for all
            force: Skip confirmation prompts

        Returns:
            Tuple of (success, message)
        """
        stats = self.get_cache_stats(cache_type)

        if not stats.all:
            return False, "No cache directories found."

        if cache_type and cache_type not in stats.scoped:
            return False, f"No cache directories found for type: {cache_type}"

        # Calculate totals
        total_files, _ = self.aggregate_cache_stats(stats.scoped)

        if total_files == 0:
            return False, "No cache files to clear."

        try:
            cleared_files = 0
            cleared_size = 0

            for cache_info in stats.scoped.values():
                if not cache_info.path.exists():
                    continue

                try:
                    # Remove all files in the directory
                    for file_path in cache_info.files:
                        try:
                            file_path.unlink()
                            cleared_files += 1
                            cleared_size += file_path.stat().st_size if file_path.exists() else 0
                        except OSError as e:
                            logger.warning(f"Could not remove {file_path}: {e}")

                    # Remove empty directories
                    if cache_info.path.exists():
                        try:
                            shutil.rmtree(cache_info.path)
                        except OSError:
                            # Directory not empty or permission issues, skip
                            pass

                except Exception as e:
                    logger.error(f"Error clearing cache directory {cache_info.path}: {e}")

            if cleared_files > 0:
                return True, (f"Cleared {cleared_files} files ({self.format_size(cleared_size)})")
            else:
                return False, "No cache files were cleared."

        except Exception as e:
            return False, f"Error clearing cache: {e}"

    def _get_base_cache_dir(self) -> Path:
        """Get the base cache directory from configuration."""
        storage_config = self.config_manager.get("storage", {})
        return Path(storage_config.get("cache_dir")).expanduser()
