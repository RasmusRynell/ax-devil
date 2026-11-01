//! Shared helpers of the media tests: the committed fixture videos and how to read their frames.

#![allow(dead_code)] // Each test file uses a part of this.

use foundation::{CancelToken, FrameFailure, HostPath, UserError, VideoFrame};
use media::{Decoder, FrameIndex};
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Path of a committed fixture video.
pub fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../fixtures/video")
        .join(name)
}

/// Builds the index of `path` without any cache.
pub fn index_of(path: &Path) -> Arc<FrameIndex> {
    let index = FrameIndex::load_or_build(path, None, &CancelToken::new(), &mut |_| {});
    Arc::new(index.unwrap_or_else(|e| panic!("{} did not index: {e:?}", path.display())))
}

/// Opens a decoder with `threads` decode threads.
pub fn open(path: &Path, index: &Arc<FrameIndex>, threads: usize) -> Decoder {
    Decoder::open(path, index.clone(), threads, CancelToken::new())
        .unwrap_or_else(|e| panic!("{} did not open: {e:?}", path.display()))
}

/// Every result of `next_frame` until the end of the stream.
pub fn read_all(decoder: &mut Decoder) -> Vec<Result<VideoFrame, FrameFailure>> {
    let mut results = Vec::new();
    while let Some(next) = decoder.next_frame().transpose() {
        results.push(next);
        assert!(results.len() < 100_000, "the decoder never reaches the end");
    }
    results
}

/// The luma of a flat frame, the value every sample of its Y plane has. Fails when the frame is not flat.
pub fn flat_luma(frame: &VideoFrame) -> u8 {
    let luma = frame.plane(0);
    assert!(
        luma.iter().all(|sample| *sample == luma[0]),
        "frame {} is not flat",
        frame.id().sequence_id
    );
    luma[0]
}

/// The luma `generate.sh` gives frame `sequence_id`.
pub fn expected_luma(sequence_id: u64) -> u8 {
    (sequence_id * 7 % 256) as u8
}

/// The reason and location of an error, which a user must be shown.
pub fn assert_explained(error: &UserError, location: &Path) {
    assert!(!error.reason.is_empty(), "the error has no reason");
    assert_eq!(error.location, Some(HostPath::new(location).into()));
}
