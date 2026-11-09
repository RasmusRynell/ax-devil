//! The sink a decoder feeds, and what it counts as skipped on the way.

use std::{cell::Cell, ops::ControlFlow};

use foundation::Warning;
use handler_api::{Framing, Header, OnEqualKey, SceneSink, SkippedRecords};
use scene::{Scene, SceneTime};

use super::Store;
use crate::link::Link;

/// The sink the decoder feeds.
pub(super) struct Feed<'a> {
    pub(super) link: Link,
    pub(super) store: Store,
    pub(super) framing: Framing,
    /// How many records the input has given; the last is the one the decoder is working on.
    pub(super) records: &'a Cell<u64>,
    /// The line of the record being read, from 1; 0 when the decoder reads the whole file.
    pub(super) position: &'a Cell<u64>,
    /// The records of a decoder that reads the file itself, which no input counts.
    pub(super) whole: Whole,
    /// The last record counted in `skipped` and the reason last given for it, so a record counts once however many
    /// Scenes or skips it gave, and one cause is named once.
    pub(super) counted: Option<(u64, String)>,
    /// The records the decoder gave up on; the warning names them with the count of lines read.
    pub(super) skipped: SkippedRecords,
}

/// The records of a decoder that reads the file itself (a plugin): a skip is one, and so are the consecutive
/// Scenes of one start key, which the host joins.
#[derive(Default)]
pub(super) struct Whole {
    /// How many records have been seen.
    count: u64,
    /// The start key of the record the last Scene joined; a skip clears it.
    key: Option<SceneTime>,
}

impl Whole {
    /// The place of a Scene with start `key`, from 0.
    fn sample(&mut self, key: SceneTime) -> u64 {
        if self.key != Some(key) {
            self.count += 1;
            self.key = Some(key);
        }
        self.count - 1
    }

    fn skip(&mut self) {
        self.count += 1;
        self.key = None;
    }
}

impl Feed<'_> {
    /// Where the file puts the Scene with start `key` that arrives now, from 0, for matching by frame. A record is
    /// placed by its own place among the file's records, so one that gave no Scene (skipped, or with nothing to
    /// draw) still holds its frame.
    fn ordinal(&mut self, key: SceneTime) -> u64 {
        match self.framing {
            Framing::Records => self.records.get().saturating_sub(1),
            Framing::Whole => self.whole.sample(key),
        }
    }

    /// The line to name for the record at `ordinal`: its own in a file read by lines, else its place among the
    /// records the decoder gave.
    fn line(&self, ordinal: u64) -> u64 {
        match self.framing {
            Framing::Records => self.position.get(),
            Framing::Whole => ordinal + 1,
        }
    }

    /// Counts the record at `ordinal` as skipped, once however many causes it has; a later, different cause only
    /// adds its reason.
    fn skip_record(&mut self, ordinal: u64, reason: String) {
        match &self.counted {
            Some((counted, last)) if *counted == ordinal => {
                if *last != reason {
                    self.skipped.also(reason.clone());
                }
            }
            _ => self.skipped.add(reason.clone()),
        }
        self.counted = Some((ordinal, reason));
    }

    /// The records the skipped warning counts against.
    pub(super) fn lines(&self) -> usize {
        let count = match self.framing {
            Framing::Records => self.records.get(),
            Framing::Whole => self.whole.count,
        };
        usize::try_from(count).unwrap_or(usize::MAX)
    }
}

impl SceneSink for Feed<'_> {
    fn header(&mut self, header: Header) {
        let store = &mut self.store;
        if store.header.is_some() || store.clock.is_some() {
            store.refused = Some("the decoder gave its header after its first sample".into());
            return;
        }
        store.header = Some(header);
    }

    fn sample(&mut self, scene: Scene, on_equal_key: OnEqualKey) -> ControlFlow<()> {
        let ordinal = self.ordinal(scene.time().start());
        let store = &mut self.store;
        if store.refused.is_some() || store.cancel.is_cancelled() {
            return ControlFlow::Break(());
        }
        if let SceneTime::Frame(frame) = scene.time().start()
            && frame < 0
        {
            // The store keys frames from 0; a Scene before the video has no frame to be drawn on.
            let line = self.line(ordinal);
            self.skip_record(
                ordinal,
                format!("line {line}: frame number {frame} is before the video"),
            );
            return ControlFlow::Continue(());
        }
        match store.accept(&mut self.link, scene, on_equal_key, ordinal) {
            Ok(()) => ControlFlow::Continue(()),
            Err(reason) => {
                store.refused = Some(reason);
                ControlFlow::Break(())
            }
        }
    }

    fn skip(&mut self, position: u64, reason: String) {
        let ordinal = match self.framing {
            Framing::Records => self.records.get().saturating_sub(1),
            Framing::Whole => {
                self.whole.skip();
                self.whole.count - 1
            }
        };
        self.skip_record(ordinal, format!("line {position}: {reason}"));
    }

    fn warn(&mut self, warning: Warning) {
        self.store.warnings.push(warning);
    }
}
