//! The video side of a feed: encoded units wait in the live gate until their overlay is settled (or 1.5 s, D28), then
//! decode in arrival order and go to every viewer. A unit without a capture time cannot be synchronized and is dropped.

use std::sync::{Arc, atomic::Ordering};

use camera::{AccessUnit, Status};
use foundation::{FrameId, MediaTime};
use overlay_sync::{LIVE_DEADLINE, LiveGate, MatchBasis};
use scene_store::TrackRead;
use tokio::{
    sync::{mpsc, watch},
    time::{MissedTickBehavior, interval, sleep_until},
};

use super::{
    connector::UnitDecoder,
    feed::{Released, Shared},
    status::STALL_CHECK,
};

/// How many units the gate holds: about the deadline's worth at 80 frames a second. A faster stream releases its oldest
/// unit early instead of waiting out the deadline.
const GATE_BOUND: usize = 120;

/// Reads one video connection until it is stopped.
pub struct VideoTask {
    shared: Arc<Shared>,
    units: Option<mpsc::Receiver<AccessUnit>>,
    link: Option<watch::Receiver<Status>>,
    stop: watch::Receiver<bool>,
    gate: LiveGate<AccessUnit>,
    decoder: Option<Box<dyn UnitDecoder>>,
    epoch: Option<u32>,
    next_sequence: u64,
    warned_capture: bool,
    warned_decode: bool,
}

impl VideoTask {
    /// A task reading `units` and following `link` until `stop` turns true.
    pub fn new(
        shared: Arc<Shared>,
        units: Option<mpsc::Receiver<AccessUnit>>,
        link: watch::Receiver<Status>,
        stop: watch::Receiver<bool>,
    ) -> Self {
        Self {
            shared,
            units,
            link: Some(link),
            stop,
            gate: LiveGate::new(LIVE_DEADLINE, GATE_BOUND),
            decoder: None,
            epoch: None,
            next_sequence: 0,
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
            let due = self
                .gate
                .next_deadline()
                .map(|time| shared.clock.instant(time));
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
                () = shared.published.notified() => {}
                () = async {
                    match due {
                        Some(due) => sleep_until(due).await,
                        None => std::future::pending().await,
                    }
                } => {}
            }
            self.release().await;
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

    /// Holds a unit for the gate, or drops it; a unit the gate lets out for being over its bound is shown at once.
    async fn accept(&mut self, unit: AccessUnit) {
        let now = self.shared.clock.now();
        // Only a unit that can be shown is data: a stream whose units all lack a capture time stalls.
        if unit.capture.is_some() {
            self.shared.board.video_data(now);
        }
        if self.epoch != Some(unit.epoch) {
            // A new connection: what is held belongs to the one before.
            self.gate.reset();
            self.decoder = None;
            self.epoch = Some(unit.epoch);
        }
        if self.shared.watching.load(Ordering::Acquire) == 0 {
            self.gate.reset();
            self.decoder = None;
            return;
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
        if let Some((id, unit)) = self.gate.push(id, now, unit) {
            self.show(id, unit).await;
        }
    }

    /// Decodes and publishes every unit the gate lets out.
    async fn release(&mut self) {
        let now = self.shared.clock.now();
        let snapshot = self.shared.track.as_ref().map(|track| track.snapshot());
        // A frame waits only for an overlay that is connected; otherwise nothing is coming for it.
        let expected = self.shared.board.overlay_expected();
        let settled = |id: FrameId| match &snapshot {
            Some(snapshot) if expected => snapshot.decides(id, MatchBasis::Time),
            _ => true,
        };
        for (id, unit) in self.gate.release(settled, now) {
            self.show(id, unit).await;
        }
    }

    async fn show(&mut self, id: FrameId, unit: AccessUnit) {
        if self.decoder.is_none() {
            // Decoding starts at a keyframe, which carries the parameter sets.
            if !unit.keyframe {
                return;
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
                    return;
                }
            }
        }
        let Some(decoder) = self.decoder.as_mut() else {
            return;
        };
        match decoder.decode(unit.data, id).await {
            Ok(Some(frame)) => {
                // A frame the overlay has not decided (it waited out the deadline) still draws the sample that is
                // open for it, which only the next timestamp or the session's end would publish.
                let snapshot = self.shared.track.as_ref().map(|track| {
                    let snapshot = track.snapshot();
                    if snapshot.decides(id, MatchBasis::Time) {
                        snapshot
                    } else {
                        track.snapshot_with_open()
                    }
                });
                self.shared
                    .frames
                    .send_replace(Some(Arc::new(Released { frame, snapshot })));
            }
            Ok(None) => {}
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
            }
        }
    }
}
