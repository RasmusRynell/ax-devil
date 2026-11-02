//! Values every other backend crate shares: Media Time and frame identity, the frame index types, decoded video
//! frames, cancellation, workspace item ids, the user-facing error value and the parser of the MOT Challenge
//! `seqinfo.ini`, which both its playlist resolver and its decoder read. No I/O.

mod cancel;
mod error;
mod frame;
mod id;
mod seqinfo;
mod time;

pub use cancel::{CancelToken, Cancelled, Interrupt};
pub use error::{ErrorCode, HostPath, Location, UserError};
pub use frame::{ColorDesc, FrameFailure, FrameLayout, Matrix, PixelFormat, Plane, VideoFrame};
pub use id::ItemId;
pub use seqinfo::{SeqInfo, parse_seqinfo};
pub use time::{FrameId, FrameRate, FrameSize, Keyframes, MediaTime, Timeline, TimelineError};
