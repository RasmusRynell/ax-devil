"""Video frame reading prototype package.

This package provides efficient video frame reading with caching and threading support.

Key Components:
- VideoFrameReader: Main class for threaded video frame reading with caching
- WorkerState: Enum for worker state management
- FrameCache: Thread-safe LRU cache for video frames
- PyAvAbstraction: PyAV-based video reading implementation
- FrameIndex: Frame indexing for reliable video navigation

Import from the submodules: this package stays light so `video_open_config` loads without PyAV or Qt.
"""
