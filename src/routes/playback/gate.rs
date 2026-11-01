//! Enforcing a connection's [`Authority`] on its socket: revocation ends the stream and closes it with
//! [`REVOKED`]; what a viewer asks for, and every frame and frame overlay sent to it, is bounded by [`clamp_demand`]
//! as the authority is now.

use super::{End, Flow};
use axum::extract::ws::{CloseFrame, Message};
use foundation::Timeline;
use overlays::OverlayEvent;
use playback::{Authority, Direction, Priority, Session, Want, clamp_demand};
use std::{
    ops::RangeInclusive,
    sync::{Arc, Mutex, OnceLock, PoisonError},
};
use tokio::sync::{Notify, watch};

/// The close code of a stream whose authority was revoked: the viewer must not reconnect on its own.
pub(super) const REVOKED: u16 = 4403;

/// The close code of a socket whose ticket was unknown, used, expired or unreadable.
pub(super) const REFUSED: u16 = 4401;

/// The message that closes a socket whose ticket was not valid.
pub(super) fn refused_close() -> Message {
    Message::Close(Some(CloseFrame {
        code: REFUSED,
        reason: "ticket not valid".into(),
    }))
}

/// The message that closes a revoked stream.
pub(super) fn revoked_close() -> Message {
    Message::Close(Some(CloseFrame {
        code: REVOKED,
        reason: "revoked".into(),
    }))
}

/// Data the writer dropped because the authority no longer reached it, until the stream gives it back to the overlays.
#[derive(Default)]
struct Rejected {
    data: Mutex<Vec<OverlayEvent>>,
    wake: Notify,
}

/// The authority over one open video: what its viewer may ask for, judged against the server clock the presenter's
/// clock intents are stamped in. It remembers what the viewer asked, so a change of authority or of the presenter's
/// clock narrows or widens what is applied without waiting for the viewer to ask again. A clone shares the video's
/// timeline and judges frames, as the writer does; it asks for nothing.
#[derive(Clone)]
pub(super) struct Gate {
    authority: watch::Receiver<Authority>,
    now_us: Arc<dyn Fn() -> i64 + Send + Sync>,
    timeline: Arc<OnceLock<Timeline>>,
    rejected: Arc<Rejected>,
    /// The viewer's last want, as it asked.
    asked: Option<Want>,
    /// The last want given to the session.
    applied: Option<Want>,
}

impl Gate {
    /// A gate on `authority`. `now_us` is the server's clock in microseconds, the one the Session service keeps.
    pub(super) fn new(
        authority: watch::Receiver<Authority>,
        now_us: impl Fn() -> i64 + Send + Sync + 'static,
    ) -> Self {
        Self {
            authority,
            now_us: Arc::new(now_us),
            timeline: Arc::default(),
            rejected: Arc::default(),
            asked: None,
            applied: None,
        }
    }

    /// Tells the gate which video it judges. Wants and frames are judged against its timeline.
    pub(super) fn open(&self, timeline: Timeline) {
        let _ = self.timeline.set(timeline);
    }

    /// Whether the authority is revoked. A holder that went away without revoking counts as revoked: no one can
    /// vouch for the stream any more.
    pub(super) fn is_revoked(&self) -> bool {
        self.authority.has_changed().is_err()
            || matches!(*self.authority.borrow(), Authority::Revoked)
    }

    /// Whether the viewer may be sent every one of `frames` now. What a viewer may have is one run of frames, so
    /// the ends decide, both against the one authority and clock this reads.
    pub(super) fn admits(&self, frames: &RangeInclusive<u64>) -> bool {
        let Some(timeline) = self.timeline.get() else {
            return false;
        };
        let authority = *self.authority.borrow();
        let now = (self.now_us)();
        let admits = |frame| {
            let one = Want {
                playhead: frame,
                ahead: 0,
                behind: 0,
                direction: Direction::Forward,
                priority: Priority::default(),
            };
            clamp_demand(&authority, timeline, now, one)
                .is_some_and(|allowed| allowed.playhead == frame)
        };
        admits(*frames.start()) && admits(*frames.end())
    }

    /// Records data the writer dropped, and wakes [`changed`](Self::changed) so the stream gives it back.
    pub(super) fn reject(&self, data: OverlayEvent) {
        self.rejected
            .data
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push(data);
        self.rejected.wake.notify_one();
    }

    /// The data the writer dropped since this was last called.
    pub(super) fn take_rejected(&self) -> Vec<OverlayEvent> {
        std::mem::take(
            &mut *self
                .rejected
                .data
                .lock()
                .unwrap_or_else(PoisonError::into_inner),
        )
    }

    /// Gives the session `want` as the authority allows it now; [`End::Revoked`] once it is revoked.
    pub(super) fn ask(&mut self, session: &Session, want: Want) -> Flow<()> {
        self.asked = Some(want);
        self.apply(session, true)
    }

    /// Gives the session the viewer's last want as the authority allows it now, if that differs from what it has.
    /// Called when the authority changes and as a presenter's clock advances.
    pub(super) fn refresh(&mut self, session: &Session) -> Flow<()> {
        self.apply(session, false)
    }

    /// The want the session was last given: the viewer's, as the authority allowed it. What else the viewer is sent
    /// for its frames, such as their overlays, follows this window and not what the viewer asked.
    pub(super) fn applied(&self) -> Option<Want> {
        self.applied
    }

    /// Whether the authority changed since the session was last given a want.
    pub(super) fn has_changed(&self) -> bool {
        self.authority.has_changed().unwrap_or(true)
    }

    /// Resolves when the authority changes, its holder goes away or the writer dropped data.
    pub(super) async fn changed(&mut self) {
        tokio::select! {
            _ = self.authority.changed() => {}
            () = self.rejected.wake.notified() => {}
        }
    }

    /// Resolves once the authority is revoked. An error means the holder is gone, which is a revocation too.
    pub(super) async fn revoked(&mut self) {
        let _ = self
            .authority
            .wait_for(|authority| matches!(authority, Authority::Revoked))
            .await;
    }

    fn apply(&mut self, session: &Session, always: bool) -> Flow<()> {
        if self.is_revoked() {
            return Err(End::Revoked);
        }
        let authority = *self.authority.borrow_and_update();
        let (Some(asked), Some(timeline)) = (self.asked, self.timeline.get()) else {
            return Ok(());
        };
        let want =
            clamp_demand(&authority, timeline, (self.now_us)(), asked).ok_or(End::Revoked)?;
        if always || self.applied != Some(want) {
            self.applied = Some(want);
            session.want(want);
        }
        Ok(())
    }
}
