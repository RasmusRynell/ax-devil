//! Playback policy: which frames to decode and keep around each viewer's playhead, and their delivery.
//!
//! A [`Registry`] holds one source per video. A source has its equal share of the [`CachePool`] allowance,
//! [`DECODE_WORKERS`] decode threads that each own a [`SpanDecoder`], and a job board: every [`Want`] of every
//! [`Session`] replans the spans to decode, and workers abandon spans the new plan no longer wants at the next frame.
//! [`clamp_demand`] bounds a [`Want`] by the viewer's [`Authority`]. Frames reach a viewer by pull:
//! [`Session::next_delivery`]. Nothing here reads a clock.
//!
//! Errors raised here carry the video's file as their location; a decoder's own errors carry whatever the
//! decoder put there (the media crate also uses the path).

mod authority;
mod cache;
mod decoder;
mod frames;
mod plan;
mod registry;
mod session;
mod source;
mod window;
mod worker;

#[cfg(test)]
mod testing;
#[cfg(test)]
mod tests;

pub use authority::{Authority, ClockIntent, clamp_demand};
pub use cache::CachePool;
pub(crate) use cache::SourceCache;
pub use decoder::{DecoderFactory, SourceSpec, SpanDecoder};
pub use frames::FrameSet;
pub(crate) use plan::Span;
pub use plan::{Direction, Priority, Rank, Want, rank};
pub use registry::{Registry, SourceKey};
pub use session::{Delivery, Session};
pub use source::DECODE_WORKERS;

use std::sync::{Mutex, MutexGuard, PoisonError};

/// Locks `mutex`, ignoring poisoning: no critical section here leaves its data half-updated.
pub(crate) fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}
