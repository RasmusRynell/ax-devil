"""Video frame reading prototype package.

This package provides efficient video frame reading with caching and threading support.

Key Components:
- VideoFrameReader: Main class for threaded video frame reading with caching
- WorkerState: Enum for worker state management
- FrameCache: Thread-safe LRU cache for video frames
- PyAvAbstraction: PyAV-based video reading implementation
- FrameIndex: Frame indexing for reliable video navigation
"""

from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.decoded_frame import DecodedFrame
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_cache import FrameCache
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_index import FrameIndex
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.frame_worker import WorkerState
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.pyav_abstraction import PyAvAbstraction
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_frame_reader import VideoFrameReader
from ax_devil.modules.data_sources.file_data_provider.pyav_decoder.video_open_config import (
    ImageSequenceConfig,
    VideoOpenConfig,
)

__all__ = [
    "ImageSequenceConfig",
    "VideoFrameReader",
    "VideoOpenConfig",
    "DecodedFrame",
    "WorkerState",
    "FrameCache",
    "FrameIndex",
    "PyAvAbstraction",
]
