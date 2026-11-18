//! The frames the backend holds decoded for the viewer's video, told to the viewer so its timeline can shade them.
//! The cache is the video's, not the viewer's, so every viewer is told the same; the message carries nothing of the
//! host and is ungated.

use super::{Outgoing, text};
use api_types::playback::ServerMessage;
use playback::FrameSet;
use std::time::Duration;
use tokio::time::{Interval, MissedTickBehavior};

/// The least time between two `Cached` messages.
const EVERY: Duration = Duration::from_millis(500);

/// What one viewer has been told of the video's cache.
pub(super) struct CachedFeed {
    tick: Interval,
    /// The frames the viewer was last told.
    told: FrameSet,
}

impl CachedFeed {
    /// A feed that has told nothing; its first tick is due at once.
    pub(super) fn new() -> Self {
        let mut tick = tokio::time::interval(EVERY);
        tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
        Self {
            tick,
            told: FrameSet::new(),
        }
    }

    /// Completes when the cache is due to be looked at again. Safe to cancel: a tick that was due is not lost.
    pub(super) async fn tick(&mut self) {
        self.tick.tick().await;
    }

    /// The message for `held`, or `None` when the viewer was already told exactly that.
    pub(super) fn message(&mut self, held: FrameSet) -> Option<Outgoing> {
        if held == self.told {
            return None;
        }
        let ranges = held
            .ranges()
            .iter()
            .map(|range| [*range.start(), *range.end()])
            .collect();
        self.told = held;
        Some(text(&ServerMessage::Cached { ranges }).into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::extract::ws::Message;

    fn told(feed: &mut CachedFeed, held: FrameSet) -> Option<String> {
        match feed.message(held)?.message {
            Message::Text(sent) => Some(sent.as_str().to_owned()),
            other => panic!("the cache is told as text, not {other:?}"),
        }
    }

    #[tokio::test]
    async fn the_viewer_is_told_the_cache_when_it_changes_and_not_again() {
        let mut feed = CachedFeed::new();
        assert_eq!(told(&mut feed, FrameSet::new()), None, "nothing cached");

        let held = FrameSet::from_ranges([0..=4, 9..=9]);
        assert_eq!(
            told(&mut feed, held.clone()).as_deref(),
            Some(r#"{"type":"Cached","ranges":[[0,4],[9,9]]}"#)
        );
        assert_eq!(
            told(&mut feed, held),
            None,
            "the same frames are not told twice"
        );
        assert_eq!(
            told(&mut feed, FrameSet::new()).as_deref(),
            Some(r#"{"type":"Cached","ranges":[]}"#),
            "an emptied cache is told"
        );
    }
}
