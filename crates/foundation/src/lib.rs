//! Values every other backend crate shares: Media Time and frame identity, the frame index types, decoded video
//! frames, cancellation, workspace item ids and the user-facing error value. No I/O.

mod cancel;
mod error;
mod frame;
mod id;
mod time;

pub use cancel::{CancelToken, Cancelled, Interrupt};
pub use error::{ErrorCode, HostPath, Location, UserError};
pub use frame::{ColorDesc, FrameFailure, FrameLayout, Matrix, PixelFormat, Plane, VideoFrame};
pub use id::ItemId;
pub use time::{FrameId, Keyframes, MediaTime, Timeline, TimelineError};
