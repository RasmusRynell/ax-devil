//! Values every other backend crate shares: Media Time and frame identity, the frame index types, decoded video
//! frames, cancellation, the one reading of a stream URL, workspace item and bookmark ids, the user-facing error and warning values and the parser of the MOT Challenge
//! `seqinfo.ini`, which its playlist resolver reads. No I/O.

mod address;
mod cancel;
mod error;
mod frame;
mod host;
mod id;
mod seqinfo;
mod time;

pub use address::{Url, UrlRefusal, check_stream_url, login, read_url, url_without_password};
pub use cancel::{CancelToken, Cancelled, Interrupt};
pub use error::{ErrorCode, HostPath, Location, UserError, Warning};
pub use frame::{ColorDesc, FrameFailure, FrameLayout, Matrix, PixelFormat, Plane, VideoFrame};
pub use host::is_plain_host;
pub use id::{BookmarkId, ItemId};
pub use seqinfo::{SeqInfo, parse_seqinfo};
pub use time::{FrameId, FrameRate, FrameSize, Keyframes, MediaTime, Timeline, TimelineError};

/// Visits every type this crate sends to the frontend, for `export-types`.
pub fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    error::visit_api_types(types);
    time::visit_api_types(types);
}
