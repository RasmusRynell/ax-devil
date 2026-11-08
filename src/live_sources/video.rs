//! The video side of a feed: encoded units wait in the live gate until their overlay is settled (or 1.5 s, D28) and
//! leave one per unit that arrives, as it arrives or once the overlay settles it, then decode in arrival order and go
//! to every viewer. A unit without a capture time cannot be synchronized and is dropped.
//! While nobody watches, units pass the gate but are not decoded, so the Events list goes on recording. The video is
//! live while pictures are shown (or, with nobody watching, units arrive): a stream that delivers only pictures that
//! cannot be decoded stalls.

use std::sync::{Arc, atomic::Ordering};

use camera::{AccessUnit, Status};
use foundation::{FrameId, MediaTime, VideoFrame};
use overlay_sync::{LIVE_DEADLINE, LiveGate, MatchBasis};
use playback::SharedFrame;
use scene_store::{TrackRead, TrackSnapshot};
use tokio::{
    sync::{mpsc, watch},
    time::{MissedTickBehavior, interval, sleep_until},
};

use super::{
    connector::UnitDecoder,
    feed::{Released, Shared},
    status::STALL_CHECK,
};

/// How many units the gate holds: about the deadline's worth at 66 frames a second. A faster stream releases its oldest
/// unit early instead of waiting out the deadline.
const GATE_BOUND: usize = 100;

/// Reads one video connection until it is stopped.
pub struct VideoTask {
    shared: Arc<Shared>,
    units: Option<mpsc::Receiver<AccessUnit>>,
    link: Option<watch::Receiver<Status>>,
    stop: watch::Receiver<bool>,
    gate: LiveGate<AccessUnit>,
    decoder: Option<Box<dyn UnitDecoder>>,
    epoch: Option<u32>,
    attempt: u64,
    next_sequence: u64,
    warned_capture: bool,
    warned_decode: bool,
}

impl VideoTask {
    /// A task reading `units` and following `link` until `stop` turns true, for connection number `attempt` of the feed.
    pub fn new(
        shared: Arc<Shared>,
        units: Option<mpsc::Receiver<AccessUnit>>,
        link: watch::Receiver<Status>,
        stop: watch::Receiver<bool>,
        attempt: u64,
    ) -> Self {
        Self {
            shared,
            units,
            link: Some(link),
            stop,
            gate: LiveGate::new(LIVE_DEADLINE, GATE_BOUND),
            decoder: None,
            epoch: None,
            attempt,
            next_sequence: 1,
            warned_capture: false,
            warned_decode: false,
        }
    }

    /// Runs until stopped.
    pub async fn run(mut self) {
        let mut tick = interval(STALL_CHECK);
        tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
        let (mut units, mut link, mut stop) =
            (self.units.take(), self.link.take(), self.stop.clone());
        if let Some(link) = &mut link {
            self.report(link);
        }
        let shared = Arc::clone(&self.shared);
        loop {
            let wake = self.gate.next_wake().map(|time| shared.clock.instant(time));
            tokio::select! {
                // A connection's state is read before the data that follows it.
                biased;
                _ = stop.changed() => break,
                changed = async {
                    match &mut link {
                        Some(link) => link.changed().await,
                        None => std::future::pending().await,
                    }
                } => match (changed, &mut link) {
                    (Ok(()), Some(link)) => self.report(link),
                    _ => link = None,
                },
                unit = async {
                    match &mut units {
                        Some(units) => units.recv().await,
                        None => std::future::pending().await,
                    }
                } => match unit {
                    Some(unit) => self.accept(unit).await,
                    // The connection ended; its status says why.
                    None => units = None,
                },
                _ = tick.tick() => shared.board.tick(shared.clock.now()),
                () = shared.published.notified() => self.release().await,
                () = async {
                    match wake {
                        Some(wake) => sleep_until(wake).await,
                        None => std::future::pending().await,
                    }
                } => self.release().await,
            }
        }
    }

    fn report(&self, link: &mut watch::Receiver<Status>) {
        let now = self.shared.clock.now();
        let status = link.borrow_and_update().clone();
        if matches!(
            status,
            Status::Live { .. } | Status::Reconnecting { .. } | Status::Failed(_)
        ) {
            self.log(&status);
        }
        self.shared.board.video_link(&status, now);
    }

    fn log(&self, status: &Status) {
        match status {
            Status::Reconnecting { error, .. } => {
                tracing::warn!("Live video is reconnecting: {error}")
            }
            Status::Failed(error) => tracing::warn!("Live video failed: {error}"),
            _ => {}
        }
    }

    /// Holds a unit for the gate, or drops it, and shows what the gate lets out for it.
    async fn accept(&mut self, unit: AccessUnit) {
        let now = self.shared.clock.now();
        if self.epoch != Some(unit.epoch) {
            // A new connection: what is held belongs to the one before.
            self.gate.reset();
            self.decoder = None;
            self.epoch = Some(unit.epoch);
        }
        let Some(capture) = unit.capture else {
            if !self.warned_capture {
                self.warned_capture = true;
                tracing::warn!(
                    "The live video has no capture time, so its frames cannot be synchronized and are dropped"
                );
            }
            return;
        };
        let time_us = capture.0.div_euclid(1000);
        self.shared.video_at(time_us);
        let id = FrameId {
            sequence_id: self.next_sequence,
            timestamp: MediaTime::from_us(time_us),
        };
        self.next_sequence += 1;
        let decided = self.decided();
        let was_early = self.gate.released_early();
        let released = self.gate.push(id, now, unit, settled(decided.as_deref()));
        if !was_early && self.gate.released_early() {
            tracing::warn!(
                "Live playback buffer is releasing frames before their {} ms delay ({GATE_BOUND} frame capacity). High frame rates or bursts shorten overlay matching time; reduce the source frame rate. Reported once until synchronization resets.",
                LIVE_DEADLINE.as_millis()
            );
        }
        for (id, unit) in released {
            self.show(id, unit).await;
        }
    }

    /// Shows what the gate lets out now: units that waited out the deadline, and the unit the newest arrival owes
    /// once the overlay settles it.
    async fn release(&mut self) {
        let now = self.shared.clock.now();
        let decided = self.decided();
        for (id, unit) in self.gate.release(now, settled(decided.as_deref())) {
            self.show(id, unit).await;
        }
    }

    /// What the overlay has decided, while a frame should wait for it: a frame waits for any overlay the feed has.
    fn decided(&self) -> Option<Arc<TrackSnapshot>> {
        self.shared.track.as_ref().map(|track| track.snapshot())
    }

    /// Publishes the unit's place in time with the picture it decodes to, if any. Every unit is published, so the
    /// Events list does not depend on pictures: not on a viewer being unpaused, nor on the unit being decodable.
    async fn show(&mut self, id: FrameId, unit: AccessUnit) {
        let frame = self.decode(id, unit).await.map(SharedFrame::new);
        if let Some(picture) = &frame {
            self.shared.picture.send_replace(Some(picture.clone()));
        }
        let now = self.shared.clock.now();
        if frame.is_some() || self.shared.watching.load(Ordering::Acquire) == 0 {
            self.shared.board.video_data(now);
        }
        // A frame the overlay has not decided (it waited out the deadline) still draws the sample that is open for
        // it, which only the next timestamp or the session's end would publish.
        let snapshot = self.shared.track.as_ref().map(|track| {
            let snapshot = track.snapshot();
            if snapshot.decides(id, MatchBasis::Time) {
                snapshot
            } else {
                track.snapshot_with_open()
            }
        });
        self.shared.frames.send_replace(Some(Arc::new(Released {
            id,
            frame,
            snapshot,
            attempt: self.attempt,
        })));
    }

    /// The picture of `unit`; `None` when nobody watches (the decoder is dropped, so a viewer that comes back starts at
    /// a keyframe) or the unit gives none.
    async fn decode(&mut self, id: FrameId, unit: AccessUnit) -> Option<VideoFrame> {
        if self.shared.watching.load(Ordering::Acquire) == 0 {
            self.decoder = None;
            return None;
        }
        if self.decoder.is_none() {
            // Decoding starts at a keyframe, which carries the parameter sets.
            if !unit.keyframe {
                return None;
            }
            let sets = if unit.codec.parameter_sets.is_empty() {
                &unit.data
            } else {
                &unit.codec.parameter_sets
            };
            match self.shared.connector.decoder(unit.codec.codec, sets) {
                Ok(decoder) => self.decoder = Some(decoder),
                Err(error) => {
                    tracing::warn!("The live video cannot be decoded: {}", error.reason);
                    return None;
                }
            }
        }
        let decoder = self.decoder.as_mut()?;
        match decoder.decode(unit.data, id).await {
            Ok(frame) => frame,
            Err(failure) => {
                // The decoder skips to the next keyframe by itself.
                if !self.warned_decode {
                    self.warned_decode = true;
                    tracing::warn!(
                        "A live video frame could not be decoded: {}",
                        failure.error.reason
                    );
                } else {
                    tracing::debug!(
                        "A live video frame could not be decoded: {}",
                        failure.error.reason
                    );
                }
                None
            }
        }
    }
}

/// Whether a frame may leave as far as its overlay goes: it has `decided` the frame, or nothing is coming.
fn settled(decided: Option<&TrackSnapshot>) -> impl Fn(FrameId) -> bool {
    move |id| decided.is_none_or(|snapshot| snapshot.decides(id, MatchBasis::Time))
}
