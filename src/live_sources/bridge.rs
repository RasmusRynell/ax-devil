//! From the async overlay task to the live track's own thread: the track pulls records from a blocking [`Feed`], so
//! each session of the overlay is a bounded queue the task pushes to and the track's decoder reads.

use std::{
    sync::{
        Arc, Mutex, PoisonError,
        atomic::{AtomicUsize, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, Sender},
    },
    time::Duration,
};

use bytes::Bytes;
use camera::DataHubStamp;
use foundation::{CancelToken, Interrupt, MediaTime};
use handler_api::{DecodeError, Input, Record};
use scene_store::Feed;

/// How often a blocked reader looks at its cancellation.
const POLL: Duration = Duration::from_millis(50);

/// One document of the overlay as the decoder reads it.
pub struct Doc {
    /// The record's bytes: an ONVIF document, or an MQTT or DataHub payload.
    pub bytes: Bytes,
    /// When the camera captured it, if it said.
    pub capture: Option<MediaTime>,
    /// The MQTT or DataHub topic it came from, for diagnostics; `None` for ONVIF.
    pub topic: Option<String>,
    /// The DataHub sample's envelope, for diagnostics; `None` for ONVIF and MQTT.
    pub stamp: Option<DataHubStamp>,
}

/// Why a document was not queued.
pub enum Refused {
    /// The decoder is behind: the documents waiting, across every session, are at the limit. The document is handed
    /// back.
    Full(Doc),
    /// The decoder stopped reading this session.
    Closed,
}

/// What the two ends of a session count together.
#[derive(Default)]
struct Held {
    /// Documents queued and not yet taken.
    pending: usize,
    /// Whether a document was ever queued.
    pushed: bool,
    /// The decoder dropped its end: nothing more is queued.
    closed: bool,
}

type Slot = Arc<Mutex<Held>>;

fn held(slot: &Slot) -> std::sync::MutexGuard<'_, Held> {
    slot.lock().unwrap_or_else(PoisonError::into_inner)
}

/// The write end of one session: what the overlay task pushes to.
pub struct Session {
    records: Sender<Doc>,
    slot: Slot,
    backlog: Arc<AtomicUsize>,
    capacity: usize,
}

impl Session {
    /// Queues `doc` for the decoder, unless the documents waiting in this and the earlier sessions are at the limit.
    pub fn push(&self, doc: Doc) -> Result<(), Refused> {
        let mut held = held(&self.slot);
        if held.closed {
            return Err(Refused::Closed);
        }
        // Only this end adds to the backlog, so the check and the addition cannot interleave with another add.
        if self.backlog.load(Ordering::Acquire) >= self.capacity {
            return Err(Refused::Full(doc));
        }
        self.records.send(doc).map_err(|_| Refused::Closed)?;
        held.pending += 1;
        held.pushed = true;
        self.backlog.fetch_add(1, Ordering::AcqRel);
        Ok(())
    }

    /// Whether no document was ever queued here, so the decoder has nothing of it to reset from.
    pub fn is_unused(&self) -> bool {
        !held(&self.slot).pushed
    }
}

/// Where the overlay task starts the sessions the track decodes, one after the other.
#[derive(Clone)]
pub struct Sessions {
    offers: Sender<Reads>,
    backlog: Arc<AtomicUsize>,
}

impl Sessions {
    /// A new session. `capacity` limits the documents waiting across all sessions, so reconnecting while the decoder
    /// is behind cannot pile up queues. The decoder of the previous one ends when it has read what is queued there,
    /// so the decoder is reset at the boundary and the open sample is published.
    pub fn start(&self, capacity: usize) -> Session {
        let (records, queue) = mpsc::channel();
        let slot = Slot::default();
        // The track has stopped when this fails, and nothing more is read.
        let _ = self.offers.send(Reads {
            queue,
            slot: Arc::clone(&slot),
            backlog: Arc::clone(&self.backlog),
            position: 0,
            current: None,
        });
        Session {
            records,
            slot,
            backlog: Arc::clone(&self.backlog),
            capacity,
        }
    }

    /// How many documents the decoder has not yet taken, in every session.
    pub fn backlog(&self) -> usize {
        self.backlog.load(Ordering::Acquire)
    }
}

/// The read end of one session: the decoder's [`Input`].
struct Reads {
    queue: Receiver<Doc>,
    slot: Slot,
    backlog: Arc<AtomicUsize>,
    position: u64,
    current: Option<Doc>,
}

impl Drop for Reads {
    fn drop(&mut self) {
        // What was never read leaves the backlog, and nothing more is queued.
        let mut held = held(&self.slot);
        held.closed = true;
        self.backlog.fetch_sub(held.pending, Ordering::AcqRel);
        held.pending = 0;
    }
}

impl Input for Reads {
    fn path(&self) -> Option<&std::path::Path> {
        None
    }

    fn next(&mut self, cancel: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        let doc = loop {
            if cancel.is_cancelled() {
                return Err(Interrupt::Cancelled);
            }
            match self.queue.recv_timeout(POLL) {
                Ok(doc) => break doc,
                Err(RecvTimeoutError::Timeout) => {}
                // The task ended this session.
                Err(RecvTimeoutError::Disconnected) => return Ok(None),
            }
        };
        held(&self.slot).pending -= 1;
        self.backlog.fetch_sub(1, Ordering::AcqRel);
        self.position += 1;
        let doc = self.current.insert(doc);
        Ok(Some(Record {
            bytes: &doc.bytes,
            position: self.position,
            capture_time: doc.capture,
        }))
    }
}

/// What the live track reads: the sessions [`Sessions::start`] makes.
pub struct SessionFeed {
    offers: Receiver<Reads>,
}

impl Feed for SessionFeed {
    fn next_input(&mut self, cancel: &CancelToken) -> Option<Box<dyn Input + Send>> {
        loop {
            if cancel.is_cancelled() {
                return None;
            }
            match self.offers.recv_timeout(POLL) {
                Ok(reads) => return Some(Box::new(reads)),
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => return None,
            }
        }
    }
}

/// A source of sessions and the feed a live track reads them from.
pub fn sessions() -> (Sessions, SessionFeed) {
    let (offers, offered) = mpsc::channel();
    (
        Sessions {
            offers,
            backlog: Arc::default(),
        },
        SessionFeed { offers: offered },
    )
}
