//! How an ingest reaches the track and the opener.

use std::{
    fs::File,
    path::PathBuf,
    sync::{Arc, mpsc},
};

use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, UserError};
use overlay_sync::SampleKeys;
use scene_decoders::{DecoderSpec, LoadWarning};

use crate::{
    history::{HistoryUnavailable, Slot},
    ingest::First,
    track::{Decided, Generation, Growth, Ingest as State, Meta, Records, Track, TrackSnapshot},
};

/// How the ingest reaches the track and the opener.
pub(crate) struct Link {
    pub(crate) path: PathBuf,
    pub(crate) cache_bytes: usize,
    pub(crate) cancel: CancelToken,
    pub(crate) spec: &'static DecoderSpec,
    pub(crate) growth: Option<Arc<Growth>>,
    pub(crate) first: Option<mpsc::Sender<First>>,
    /// The generation of the latest snapshot.
    pub(crate) generation: u32,
}

/// What [`Link::present`] shows.
pub(crate) struct Presented<'a> {
    /// A handle of the artifact the records are in.
    pub(crate) file: File,
    pub(crate) keys: SampleKeys,
    /// Boundaries and checksums of the records readers do not have yet.
    pub(crate) records: (&'a [u64], &'a [u32]),
    pub(crate) warnings: Vec<LoadWarning>,
    pub(crate) ingest: State,
    pub(crate) decided: Decided,
    pub(crate) stored: Option<PathBuf>,
    /// Whether `records` start a new numbering of the samples.
    pub(crate) fresh: bool,
    /// Where the history is, when this ends the ingest.
    pub(crate) history: Option<Slot>,
}

impl Link {
    pub(crate) fn new(
        path: PathBuf,
        cache_bytes: usize,
        cancel: CancelToken,
        spec: &'static DecoderSpec,
        first: mpsc::Sender<First>,
    ) -> Self {
        Self {
            path,
            cache_bytes,
            cancel,
            spec,
            growth: None,
            first: Some(first),
            generation: 0,
        }
    }

    pub(crate) fn error(&self, code: ErrorCode, reason: String) -> UserError {
        UserError::new(code, reason).at(HostPath::new(&self.path))
    }

    /// Shows readers what the ingest has now. The first call creates the track and gives it to the opener.
    pub(crate) fn present(&mut self, shown: Presented<'_>, meta: impl FnOnce() -> Meta) {
        let Presented {
            file,
            keys,
            records,
            warnings,
            ingest,
            decided,
            stored,
            fresh,
            history,
        } = shown;
        let new_generation = fresh && self.growth.is_some();
        self.generation += u32::from(new_generation);
        // The records the track already serves keep their numbering unless this publication starts a new one.
        let numbering = match &self.growth {
            Some(growth) if !fresh => growth.snapshot().numbering.clone(),
            _ => Arc::new(Generation::new(
                file,
                Records::after_header(records),
                self.cache_bytes,
            )),
        };
        let snapshot = TrackSnapshot {
            keys,
            decided,
            ingest,
            generation: self.generation,
            warnings: warnings.into(),
            numbering,
        };
        let Some(growth) = self.growth.clone() else {
            let growth = Arc::new(Growth::new(snapshot, stored));
            let track = Arc::new(Track::new(
                self.path.clone(),
                growth.clone(),
                meta(),
                self.spec,
                self.cancel.clone(),
            ));
            if let Some(slot) = history {
                growth.set_history(slot);
            }
            self.growth = Some(growth);
            if let Some(first) = self.first.take() {
                // The opener may have gone; then the track drops here and stops the ingest.
                let _ = first.send(First::Track(track));
            }
            return;
        };
        if let Some(stored) = stored {
            growth.set_stored(stored);
        }
        if let Some(slot) = history {
            growth.set_history(slot);
        }
        if fresh {
            growth.publish(snapshot);
        } else {
            growth.extend(records.0, records.1, snapshot);
        }
    }

    /// Ends the ingest without a complete result: the opener learns why, or the track says it is incomplete.
    pub(crate) fn fail(&mut self, interrupt: Interrupt<UserError>) {
        if let Some(growth) = &self.growth {
            if let Interrupt::Failed(error) = interrupt {
                growth.set_history(Slot::Unavailable(HistoryUnavailable::Failed(error.clone())));
                let mut snapshot = (*growth.snapshot()).clone();
                snapshot.decided = Decided::All;
                snapshot.ingest = State::Incomplete(error);
                growth.publish(snapshot);
            }
            return;
        }
        if let Some(first) = self.first.take() {
            let _ = first.send(match interrupt {
                Interrupt::Failed(error) => First::Failed(error),
                Interrupt::Cancelled => First::Cancelled,
            });
        }
    }
}
