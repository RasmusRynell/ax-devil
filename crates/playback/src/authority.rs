//! What the holder of a stream may ask of the backend, and the pure rule that enforces it on each [`Want`]. The
//! route owns the socket; this module only decides frames, so it knows no session or sharing concept.

use crate::{Priority, Want};
use foundation::{MediaTime, Timeline};
use serde::{Deserialize, Serialize};
use std::ops::RangeInclusive;
use ts_rs::TS;

/// A follower may be this far behind the presenter's frame (media time).
const BEHIND_US: i64 = 1_000_000;
/// A playing follower may buffer this far ahead of the presenter's frame, at the intent's speed.
const AHEAD_US: i64 = 2_000_000;
/// A paused follower may be this many frames either side of the presenter's frame.
const PAUSED_FRAMES: u64 = 4;
/// A playing follower may always buffer at least this many frames ahead of the presenter's frame, whatever the speed.
const AHEAD_FLOOR_FRAMES: u64 = 120;
/// No holder may keep more than this many frames on either side of its playhead, whatever it asks.
const MAX_WINDOW_FRAMES: u32 = 65_536;

/// What a presenter says the Session's clock is doing, for one viewer. Times are microseconds.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ClockIntent {
    /// The frame the presenter asked for.
    #[ts(type = "number")]
    pub frame: u64,
    /// Whether the clock runs.
    pub playing: bool,
    /// Speed in hundredths: 100 is real time.
    pub speed_centi: u32,
    /// When the presenter stamped the intent, in server time.
    #[ts(type = "number")]
    pub at_us: i64,
}

/// What a stream's holder may demand.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Authority {
    /// The holder chooses its own frames, anywhere in the video.
    Own {
        /// How urgent its frames are against other holders of the same source.
        priority: Priority,
    },
    /// The holder follows the presenter: it may buffer around the presenter's frame but not scrub.
    Follow {
        /// What the presenter's clock is doing.
        intent: ClockIntent,
    },
    /// The stream is over for good: nothing more is decoded or sent.
    Revoked,
}

impl Default for Authority {
    fn default() -> Self {
        Self::Own {
            priority: Priority::Normal,
        }
    }
}

/// The frame a follower is on at `now_server_us`: the intent's frame while paused, otherwise the frame due after the
/// time elapsed since the intent, at its speed. The same rule as `followFrame` in the frontend.
fn follow_frame(timeline: &Timeline, intent: &ClockIntent, now_server_us: i64) -> u64 {
    let anchor = intent.frame.min(timeline.last());
    if !intent.playing {
        return anchor;
    }
    let anchor_us = timeline.time(anchor).map_or(0, MediaTime::as_us);
    let elapsed = scaled(
        now_server_us.saturating_sub(intent.at_us),
        intent.speed_centi,
    );
    timeline
        .at_or_before(MediaTime::from_us(anchor_us.saturating_add(elapsed)))
        .unwrap_or(0)
}

/// `us` at `speed_centi` hundredths of real time.
fn scaled(us: i64, speed_centi: u32) -> i64 {
    let scaled = i128::from(us) * i128::from(speed_centi) / 100;
    i64::try_from(scaled).unwrap_or(if scaled < 0 { i64::MIN } else { i64::MAX })
}

/// The playheads `authority` allows, or `None` when it allows nothing. A follower's frames come from the presenter's
/// intent and the constants above alone, never from what the holder asked.
fn allowed(
    authority: &Authority,
    timeline: &Timeline,
    now_server_us: i64,
) -> Option<RangeInclusive<u64>> {
    let last = timeline.last();
    match authority {
        Authority::Revoked => None,
        Authority::Own { .. } => Some(0..=last),
        Authority::Follow { intent } => {
            let presenter = follow_frame(timeline, intent, now_server_us);
            if !intent.playing {
                return Some(
                    presenter.saturating_sub(PAUSED_FRAMES)
                        ..=presenter.saturating_add(PAUSED_FRAMES).min(last),
                );
            }
            let at = timeline.time(presenter).map_or(0, MediaTime::as_us);
            let low = timeline
                .at_or_before(MediaTime::from_us(at.saturating_sub(BEHIND_US)))
                .unwrap_or(0);
            let reach = scaled(AHEAD_US, intent.speed_centi);
            let high = timeline
                .at_or_before(MediaTime::from_us(at.saturating_add(reach)))
                .unwrap_or(presenter)
                .max(presenter.saturating_add(AHEAD_FLOOR_FRAMES));
            Some(low..=high.min(last))
        }
    }
}

/// `want` as `authority` allows it at `now_server_us` (the clock that stamped the intent): the playhead is clamped
/// to the allowed frames, the whole range the holder keeps around it (`behind` to `ahead`) stays inside them for a
/// follower and is bounded for everyone, and the priority is the authority's. A holder can therefore neither scrub a
/// follow, nor have frames delivered outside it, nor claim urgency. `None` once the authority is revoked. A video's
/// first frame is requested through a `Want` as well, so this also bounds the frame a stream opens on.
pub fn clamp_demand(
    authority: &Authority,
    timeline: &Timeline,
    now_server_us: i64,
    want: Want,
) -> Option<Want> {
    let frames = allowed(authority, timeline, now_server_us)?;
    let playhead = want.playhead.clamp(*frames.start(), *frames.end());
    let (priority, room_behind, room_ahead) = match authority {
        Authority::Own { priority } => (*priority, u64::MAX, u64::MAX),
        Authority::Follow { .. } | Authority::Revoked => (
            Priority::Normal,
            playhead - frames.start(),
            frames.end() - playhead,
        ),
    };
    let bounded = |asked: u32, room: u64| {
        asked
            .min(MAX_WINDOW_FRAMES)
            .min(u32::try_from(room).unwrap_or(u32::MAX))
    };
    Some(Want {
        playhead,
        ahead: bounded(want.ahead, room_ahead),
        behind: bounded(want.behind, room_behind),
        priority,
        ..want
    })
}
