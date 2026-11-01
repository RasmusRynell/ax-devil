//! Values every other backend crate shares: Media Time and frame identity, the frame index types, decoded video
//! frames, cancellation and the user-facing error value. No I/O.

mod cancel;
mod error;
mod frame;
mod time;

pub use cancel::{CancelToken, Cancelled, Interrupt};
pub use error::UserError;
pub use frame::{ColorDesc, FrameFailure, FrameLayout, Matrix, PixelFormat, Plane, VideoFrame};
pub use time::{FrameId, FrameRate, FrameSize, Keyframes, MediaTime, Timeline, TimelineError};
