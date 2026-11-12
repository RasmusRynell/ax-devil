//! What a viewer is told of each feed: the connection's state, and for video whether it still delivers.
//!
//! Pure and clock-free like the live gate: every call passes the time in. The shared channel carries reason codes;
//! bounded MQTT alternatives are formatted separately under each audience's host-detail rights.

use std::{sync::Mutex, time::Duration};

use api_types::live::{LiveFeedStatus, LiveReason, LiveStatusWire};
use camera::Status;
use overlay_sync::Mono;
use tokio::sync::watch;

use super::connector::reason;

/// How long a live video may deliver nothing before it is shown as stalled.
pub const STALL_AFTER: Duration = Duration::from_secs(5);
/// How often a feed looks for a stall.
pub const STALL_CHECK: Duration = Duration::from_secs(1);

/// One connection's state as the viewer sees it.
struct FeedState {
    link: Status,
    /// Failures in a row since the connection was last live.
    attempt: u32,
    /// When the connection last turned live.
    since: Mono,
    last_data: Option<Mono>,
    /// The connection whose data last arrived, so data a reconnect left queued does not count for the new one.
    delivered: Option<u32>,
    /// Whether silence on a live connection is a stall. Video only: a quiet MQTT or DataHub topic is normal.
    stalls: bool,
}

impl FeedState {
    fn new(stalls: bool, now: Mono) -> Self {
        Self {
            link: Status::Connecting,
            attempt: 0,
            since: now,
            last_data: None,
            delivered: None,
            stalls,
        }
    }

    fn set(&mut self, link: &Status, now: Mono) {
        match (&self.link, link) {
            (Status::Live { epoch: seen }, Status::Live { epoch }) if seen == epoch => return,
            (_, Status::Live { .. }) => {
                self.attempt = 0;
                self.since = now;
                self.last_data = None;
            }
            (_, Status::Reconnecting { .. }) => self.attempt += 1,
            // A restart's connections count their epochs anew.
            (_, Status::Connecting) => {
                self.attempt = 0;
                self.delivered = None;
            }
            _ => {}
        }
        self.link = link.clone();
    }

    /// Whether the connection is live and its data has arrived.
    fn delivers(&self) -> bool {
        matches!(self.link, Status::Live { epoch } if self.delivered == Some(epoch))
    }

    /// Whether the connection is live but has delivered nothing yet.
    fn waits(&self) -> bool {
        matches!(self.link, Status::Live { .. }) && !self.delivers()
    }

    fn wire(&self, now: Mono) -> LiveFeedStatus {
        match &self.link {
            Status::Connecting => LiveFeedStatus::Connecting,
            Status::Live { .. } => {
                let quiet_since = self
                    .last_data
                    .map_or(self.since, |data| data.max(self.since));
                if self.stalls && now.0.saturating_sub(quiet_since.0) >= STALL_AFTER {
                    LiveFeedStatus::Stalled {
                        seconds: u32::try_from(STALL_AFTER.as_secs()).unwrap_or(u32::MAX),
                    }
                } else {
                    LiveFeedStatus::Live
                }
            }
            Status::Reconnecting { error, .. } => LiveFeedStatus::Reconnecting {
                attempt: self.attempt,
                reason: reason(error.kind),
            },
            Status::Failed(error) => LiveFeedStatus::Failed {
                reason: reason(error.kind),
            },
            // Closed on request; nobody is told, but a reader that still looks sees the connection gone.
            Status::Closed => LiveFeedStatus::Failed {
                reason: LiveReason::Lost,
            },
        }
    }
}

/// Where the overlay comes from, for the status.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OverlayLink {
    /// Video only.
    None,
    /// Metadata inside the RTSP stream: it stands where the video connection stands.
    Embedded,
    /// Its own connection, MQTT or DataHub.
    Separate,
}

struct Inner {
    video: FeedState,
    overlay: Option<FeedState>,
    embedded: bool,
    behind: bool,
}

impl Inner {
    fn wire(&self, now: Mono) -> LiveStatusWire {
        LiveStatusWire {
            alternatives: None,
            video: self.video.wire(now),
            overlay: self.overlay.as_ref().map(|overlay| overlay.wire(now)),
            behind: self.behind,
            waiting: self.overlay.as_ref().is_some_and(FeedState::waits),
        }
    }
}

/// The feeds' status, shared by the tasks that learn it and published to every viewer on change.
pub struct Board {
    inner: Mutex<Inner>,
    tx: watch::Sender<LiveStatusWire>,
}

impl Board {
    /// A board for a video with `overlay`, with every connection still connecting at `now`.
    pub fn new(overlay: OverlayLink, now: Mono) -> Self {
        let inner = Inner {
            video: FeedState::new(true, now),
            overlay: match overlay {
                OverlayLink::None => None,
                OverlayLink::Embedded | OverlayLink::Separate => Some(FeedState::new(false, now)),
            },
            embedded: overlay == OverlayLink::Embedded,
            behind: false,
        };
        let (tx, _) = watch::channel(inner.wire(now));
        Self {
            inner: Mutex::new(inner),
            tx,
        }
    }

    /// Every change of the status, and its current value.
    pub fn subscribe(&self) -> watch::Receiver<LiveStatusWire> {
        self.tx.subscribe()
    }

    /// The status now.
    #[cfg(test)]
    pub fn current(&self) -> LiveStatusWire {
        self.tx.borrow().clone()
    }

    fn update(&self, now: Mono, change: impl FnOnce(&mut Inner)) {
        let mut inner = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        change(&mut inner);
        let wire = inner.wire(now);
        self.tx.send_if_modified(|shown| {
            let changed = *shown != wire;
            *shown = wire;
            changed
        });
    }

    /// The video connection's state changed.
    pub fn video_link(&self, link: &Status, now: Mono) {
        self.update(now, |inner| {
            inner.video.set(link, now);
            if inner.embedded
                && let Some(overlay) = &mut inner.overlay
            {
                overlay.set(link, now);
            }
        });
    }

    /// The overlay's own connection changed.
    pub fn overlay_link(&self, link: &Status, now: Mono) {
        self.update(now, |inner| {
            if let Some(overlay) = &mut inner.overlay {
                overlay.set(link, now);
            }
        });
    }

    /// Formats bounded source alternatives under this viewer's own rights, never on the shared status channel.
    pub fn alternatives(&self, audience: api_types::Audience) -> Option<String> {
        if !audience.host_details {
            return None;
        }
        let inner = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let Status::Failed(error) = &inner.overlay.as_ref()?.link else {
            return None;
        };
        let (keys, remaining) = error.source_alternatives.as_ref()?;
        let listed = if keys.is_empty() {
            "none".to_owned()
        } else {
            keys.iter()
                .map(|key| camera::escaped(key))
                .collect::<Vec<_>>()
                .join(", ")
        };
        Some(if *remaining > 0 {
            format!("{listed} (+{remaining} more)")
        } else {
            listed
        })
    }

    /// A video unit arrived.
    pub fn video_data(&self, now: Mono) {
        self.update(now, |inner| inner.video.last_data = Some(now));
    }

    /// An overlay document of connection `epoch` arrived.
    pub fn overlay_data(&self, epoch: u32, now: Mono) {
        self.update(now, |inner| {
            if let Some(overlay) = &mut inner.overlay {
                overlay.delivered = Some(epoch);
            }
        });
    }

    /// Whether the overlay's decoder is behind.
    pub fn behind(&self, behind: bool, now: Mono) {
        self.update(now, |inner| inner.behind = behind);
    }

    /// Looks again at what only time changes: a video that stopped delivering.
    pub fn tick(&self, now: Mono) {
        self.update(now, |_| {});
    }

    /// Every connection is being made again.
    pub fn restart(&self, now: Mono) {
        self.update(now, |inner| {
            inner.video.set(&Status::Connecting, now);
            if let Some(overlay) = &mut inner.overlay {
                overlay.set(&Status::Connecting, now);
            }
            inner.behind = false;
        });
    }

    /// Whether a retry can help at `now`: a connection has failed for good, or the video went silent. This is the
    /// state the viewer is shown a Retry action for.
    pub fn needs_retry(&self, now: Mono) -> bool {
        let wire = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .wire(now);
        let failed = |status: &LiveFeedStatus| matches!(status, LiveFeedStatus::Failed { .. });
        failed(&wire.video)
            || matches!(wire.video, LiveFeedStatus::Stalled { .. })
            || wire.overlay.as_ref().is_some_and(failed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use camera::{CameraError, ErrorKind};

    const STALLED: LiveFeedStatus = LiveFeedStatus::Stalled { seconds: 5 };

    fn at(seconds: u64) -> Mono {
        Mono(Duration::from_secs(seconds))
    }

    fn failure(kind: ErrorKind) -> CameraError {
        CameraError::fake(kind, "")
    }

    fn reconnecting(kind: ErrorKind) -> Status {
        Status::Reconnecting {
            error: failure(kind),
            next_in: Duration::from_millis(250),
        }
    }

    #[test]
    fn video_stalls_after_five_silent_seconds_and_recovers_with_data() {
        let board = Board::new(OverlayLink::None, at(0));
        board.video_link(&Status::Live { epoch: 1 }, at(0));
        board.tick(at(4));
        assert_eq!(board.current().video, LiveFeedStatus::Live);
        board.tick(at(5));
        assert_eq!(board.current().video, STALLED);
        board.video_data(at(6));
        assert_eq!(board.current().video, LiveFeedStatus::Live);
        board.tick(at(10));
        assert_eq!(board.current().video, LiveFeedStatus::Live);
        board.tick(at(11));
        assert_eq!(board.current().video, STALLED);
    }

    #[test]
    fn a_quiet_overlay_is_never_stalled_and_reconnects_count_up_until_data_flows() {
        let board = Board::new(OverlayLink::Separate, at(0));
        board.overlay_link(&Status::Live { epoch: 1 }, at(0));
        board.tick(at(60));
        assert_eq!(board.current().overlay, Some(LiveFeedStatus::Live));

        board.overlay_link(&reconnecting(ErrorKind::Lost), at(61));
        board.overlay_link(&reconnecting(ErrorKind::Unreachable), at(62));
        assert_eq!(
            board.current().overlay,
            Some(LiveFeedStatus::Reconnecting {
                attempt: 2,
                reason: LiveReason::Unreachable
            })
        );
        board.overlay_link(&Status::Live { epoch: 2 }, at(63));
        board.overlay_link(&reconnecting(ErrorKind::Lost), at(64));
        assert_eq!(
            board.current().overlay,
            Some(LiveFeedStatus::Reconnecting {
                attempt: 1,
                reason: LiveReason::Lost
            })
        );
        board.overlay_link(&Status::Failed(failure(ErrorKind::LoginRejected)), at(65));
        assert_eq!(
            board.current().overlay,
            Some(LiveFeedStatus::Failed {
                reason: LiveReason::LoginRejected
            })
        );
    }

    #[test]
    fn embedded_metadata_stands_where_the_video_stands_and_the_status_says_waiting_only_until_the_connection_delivered()
     {
        let board = Board::new(OverlayLink::Embedded, at(0));
        let waiting = |waiting| assert_eq!(board.current().waiting, waiting);
        waiting(false);
        board.video_link(&Status::Live { epoch: 1 }, at(0));
        assert_eq!(board.current().overlay, Some(LiveFeedStatus::Live));
        waiting(true);
        board.overlay_data(1, at(0));
        waiting(false);
        board.video_link(&reconnecting(ErrorKind::Silent), at(1));
        waiting(false);
        assert_eq!(
            board.current().overlay,
            Some(LiveFeedStatus::Reconnecting {
                attempt: 1,
                reason: LiveReason::Silent
            })
        );
        // The last connection's data does not count for the next; data that beats its own Live does.
        board.video_link(&Status::Live { epoch: 2 }, at(2));
        waiting(true);
        board.video_link(&reconnecting(ErrorKind::Lost), at(3));
        board.overlay_data(3, at(4));
        board.video_link(&Status::Live { epoch: 3 }, at(4));
        waiting(false);
    }
}
