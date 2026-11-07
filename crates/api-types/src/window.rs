//! Wire type for the window routes.

use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// Where a window was and how big: the body of `GET` and `PUT /api/windows/{name}`.
#[derive(Serialize, Deserialize, TS)]
pub struct WindowState {
    /// Left edge of the window when not maximized, in screen pixels.
    pub x: i32,
    /// Top edge of the window when not maximized, in screen pixels.
    pub y: i32,
    /// Width of the window when not maximized.
    pub width: u32,
    /// Height of the window when not maximized.
    pub height: u32,
    /// Whether the window was maximized.
    pub maximized: bool,
}
