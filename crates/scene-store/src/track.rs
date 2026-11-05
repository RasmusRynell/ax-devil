//! A decoded overlay file: its sample keys in memory, its Scenes in the artifact, both growing while it is ingested.

use std::{
    fs::File,
    path::PathBuf,
    sync::{
        Arc, Mutex, OnceLock, PoisonError, RwLock, RwLockReadGuard, Weak,
        atomic::{AtomicBool, Ordering},
    },
};

use foundation::{CancelToken, ErrorCode, FrameId, HostPath, MediaTime, UserError, Warning};
use handler_api::{Form, Header, Spec};
use overlay_sync::{MatchBasis, SampleKeys};
use scene::{
    Coverage, Fields, FilterPolicy, Scene, SceneTime, TimeKind, serial::columns::encode_sample_item,
};

use crate::{
    format::{HEADER_LEN, read_record},
    history::{History, HistoryUnavailable, Miss, Slot},
    io::names_file,
    live::Samples,
    lru::{ItemCache, SceneCache, footprint},
    read::TrackRead,
    source::SourceWatch,
};

/// What the encoded Sample Items of one track may hold in memory.
pub(crate) const ITEM_CACHE_BYTES: usize = 16 << 20;

/// How far the ingest of a file has got.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Ingest {
    /// The decoder is still running; more samples may come.
    Reading,
    /// Every sample is in, and the artifact is stored if it can be.
    Complete,
    /// The decoder stopped early; the samples it gave are kept, the artifact is not stored, and the next open
    /// decodes again.
    Incomplete(UserError),
}

/// Which frames the published samples decide.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Decided {
    /// Every sample keyed below these is published and final; later samples may still come. A key is `None` when the
    /// file cannot be matched on that basis, so no sample will ever belong to a frame by it.
    Below {
        /// The frame number the open sample will have: its record's position in the file.
        frame: Option<u64>,
        /// The open sample's timestamp.
        time: Option<MediaTime>,
    },
    /// The ingest has ended: every frame is decided.
    All,
}

impl Decided {
    /// What an ingest decides while the sample keyed `open`, from the record at `ordinal` in the file, is not yet
    /// published. `ordinal` is `None` when the file is matched by timestamps only.
    pub(crate) fn below(open: SceneTime, ordinal: Option<u64>) -> Self {
        match open {
            SceneTime::Frame(frame) => Self::Below {
                frame: Some(frame),
                time: None,
            },
            SceneTime::Timestamp(time) => Self::Below {
                frame: ordinal,
                time: Some(time),
            },
        }
    }
}

/// What a [`TrackRead`] holds at one moment. Take one with [`TrackRead::snapshot`]; it never changes.
///
/// It holds the numbering of its samples, so [`TrackRead::scene_in`] and [`TrackRead::item_in`] read sample `i` of the
/// snapshot even after the track has numbered its samples anew.
#[derive(Clone, Debug)]
pub struct TrackSnapshot {
    /// The published samples' keys, ascending and unique; sample `i` is read with [`TrackRead::scene_in`].
    pub keys: SampleKeys,
    /// Which frames the published samples decide.
    pub decided: Decided,
    /// How far the ingest has got.
    pub ingest: Ingest,
    /// Counts the times published sample indices changed meaning (a sample arrived out of order and the samples
    /// were sorted). Snapshots of different generations number their samples differently.
    pub generation: u32,
    /// Problems the decoder and the store reported so far.
    pub warnings: Arc<[Warning]>,
    /// Where the records of these samples are.
    pub(crate) numbering: Numbering,
}

impl TrackSnapshot {
    /// Whether the samples published here decide what `frame` shows when matched by `basis`, so that no later sample
    /// can change it. A frame shows only samples keyed at or before it on that basis, so it is decided once the open
    /// key is past it.
    pub fn decides(&self, frame: FrameId, basis: MatchBasis) -> bool {
        match (self.decided, basis) {
            (Decided::All, _) => true,
            (Decided::Below { frame: open, .. }, MatchBasis::Frame) => {
                open.is_none_or(|open| frame.sequence_id < open)
            }
            (Decided::Below { time: open, .. }, MatchBasis::Time) => {
                open.is_none_or(|open| frame.timestamp < open)
            }
        }
    }

    /// The clock the file's own keys are on.
    pub fn clock(&self) -> TimeKind {
        if self.keys.times.is_some() {
            TimeKind::Timestamp
        } else {
            TimeKind::Frame
        }
    }
}

/// What the file says about itself, fixed at the first publication.
pub(crate) struct Meta {
    pub coverage: Coverage,
    pub filter: FilterPolicy,
    pub metadata: Fields,
}

impl Meta {
    /// What a header says; a decoder that gave none has an empty policy, sparse coverage and no metadata.
    pub(crate) fn of(header: Option<&Header>) -> Self {
        match header {
            Some(header) => Self {
                coverage: header.coverage,
                filter: header.filter.clone(),
                metadata: header.metadata.clone(),
            },
            None => Self {
                coverage: Coverage::Sparse,
                filter: FilterPolicy {
                    name: "",
                    options: Vec::new(),
                },
                metadata: Fields::new(),
            },
        }
    }
}

/// Where the records of one generation are: record `r` occupies `boundaries[r]..boundaries[r + 1]`, and record `r`
/// is sample `r`.
pub(crate) struct Records {
    pub boundaries: Vec<u64>,
    pub crcs: Vec<u32>,
}

impl Records {
    /// The records whose ends are `records.0` and checksums `records.1`, starting after the file header.
    pub(crate) fn after_header(records: (&[u64], &[u32])) -> Self {
        let mut boundaries = Vec::with_capacity(records.0.len() + 1);
        boundaries.push(HEADER_LEN);
        boundaries.extend_from_slice(records.0);
        Self {
            boundaries,
            crcs: records.1.to_vec(),
        }
    }
}

/// Where the samples of a snapshot are kept: in a stored artifact, or in memory for a live track.
#[derive(Clone, Debug)]
pub(crate) enum Numbering {
    Artifact(Arc<Generation>),
    Memory(Arc<Samples>),
}

/// The error for a snapshot read through a track it is not of.
fn foreign() -> UserError {
    UserError::new(
        ErrorCode::Internal,
        "A sample was read through a track it does not belong to",
    )
}

impl Numbering {
    /// The artifact of a snapshot of a [`Track`].
    fn artifact(&self) -> Result<&Arc<Generation>, UserError> {
        match self {
            Self::Artifact(generation) => Ok(generation),
            Self::Memory(_) => Err(foreign()),
        }
    }

    /// The samples of a snapshot of a [`LiveTrack`](crate::LiveTrack).
    pub(crate) fn memory(&self) -> Result<&Arc<Samples>, UserError> {
        match self {
            Self::Memory(samples) => Ok(samples),
            Self::Artifact(_) => Err(foreign()),
        }
    }
}

/// The artifact as one numbering of samples: its handle, the published records and the caches keyed by sample index.
pub(crate) struct Generation {
    file: Arc<File>,
    records: RwLock<Records>,
    cache: Mutex<SceneCache>,
    items: Mutex<ItemCache>,
}

impl Generation {
    pub(crate) fn new(file: File, records: Records, cache_bytes: usize) -> Self {
        Self {
            file: Arc::new(file),
            records: RwLock::new(records),
            cache: Mutex::new(SceneCache::new(cache_bytes)),
            items: Mutex::new(ItemCache::new(ITEM_CACHE_BYTES)),
        }
    }

    /// Makes the records with these ends and checksums readable after the ones already there.
    pub(crate) fn extend(&self, boundaries: &[u64], crcs: &[u32]) {
        let mut held = self.records.write().unwrap_or_else(PoisonError::into_inner);
        held.boundaries.extend_from_slice(boundaries);
        held.crcs.extend_from_slice(crcs);
    }

    fn records(&self) -> RwLockReadGuard<'_, Records> {
        self.records.read().unwrap_or_else(PoisonError::into_inner)
    }

    fn cache(&self) -> std::sync::MutexGuard<'_, SceneCache> {
        self.cache.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn items(&self) -> std::sync::MutexGuard<'_, ItemCache> {
        self.items.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

impl std::fmt::Debug for Generation {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Generation").finish_non_exhaustive()
    }
}

/// The part of a track the ingest changes, shared by the track and the thread that feeds it.
pub(crate) struct Growth {
    snapshot: RwLock<Arc<TrackSnapshot>>,
    listeners: Mutex<Vec<Weak<dyn Fn() + Send + Sync>>>,
    history: Mutex<Slot>,
    /// Where the artifact is stored, once it is.
    stored: OnceLock<PathBuf>,
}

impl Growth {
    pub(crate) fn new(snapshot: TrackSnapshot, stored: Option<PathBuf>) -> Self {
        Self {
            history: Mutex::new(Slot::initial(&snapshot.ingest)),
            snapshot: RwLock::new(Arc::new(snapshot)),
            listeners: Mutex::new(Vec::new()),
            stored: stored.map(OnceLock::from).unwrap_or_default(),
        }
    }

    pub(crate) fn set_stored(&self, path: PathBuf) {
        let _ = self.stored.set(path);
    }

    /// Says where the history is; set before the snapshot that ends the ingest is published.
    pub(crate) fn set_history(&self, slot: Slot) {
        *self.history.lock().unwrap_or_else(PoisonError::into_inner) = slot;
    }

    pub(crate) fn snapshot(&self) -> Arc<TrackSnapshot> {
        self.snapshot
            .read()
            .unwrap_or_else(PoisonError::into_inner)
            .clone()
    }

    /// Calls `listener` after every publication.
    pub(crate) fn listen(&self, listener: Weak<dyn Fn() + Send + Sync>) {
        self.listeners
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push(listener);
    }

    /// Publishes `snapshot` and tells the listeners. A snapshot of a new numbering starts serving it at once.
    pub(crate) fn publish(&self, snapshot: TrackSnapshot) {
        self.store(snapshot);
        self.notify();
    }

    /// Serves `snapshot` from now on without telling the listeners; follow with [`Growth::notify`].
    pub(crate) fn store(&self, snapshot: TrackSnapshot) {
        *self
            .snapshot
            .write()
            .unwrap_or_else(PoisonError::into_inner) = Arc::new(snapshot);
    }

    /// Calls every listener. Called outside every lock, so a listener may read the track.
    pub(crate) fn notify(&self) {
        let live: Vec<Arc<dyn Fn() + Send + Sync>> = {
            let mut listeners = self
                .listeners
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            listeners.retain(|listener| listener.strong_count() > 0);
            listeners.iter().filter_map(Weak::upgrade).collect()
        };
        for listener in live {
            listener();
        }
    }
}

/// One decoded overlay file: its sample keys in memory and its Scenes behind an artifact.
///
/// While the file is still being ingested the track serves what is published; [`TrackRead::snapshot`] says how much.
/// Memory is the keys, a few bytes per record, and byte-bounded caches of recently used Scenes and Sample Items;
/// everything else stays on disk. Dropping the last handle stops an ingest still running.
pub struct Track {
    source: PathBuf,
    growth: Arc<Growth>,
    meta: Meta,
    /// The decoder that reads the file.
    spec: Arc<Spec>,
    /// Cancels the ingest thread when the track goes away.
    ingest: CancelToken,
    /// Set when a record failed its check; the registry then stops sharing this track.
    damaged: AtomicBool,
    /// Notices an in-place edit of the file; set by the registry that opened the track.
    watch: OnceLock<SourceWatch>,
}

impl Track {
    pub(crate) fn new(
        source: PathBuf,
        growth: Arc<Growth>,
        meta: Meta,
        spec: Arc<Spec>,
        ingest: CancelToken,
    ) -> Self {
        Self {
            source,
            growth,
            meta,
            spec,
            ingest,
            damaged: AtomicBool::new(false),
            watch: OnceLock::new(),
        }
    }

    /// Starts noticing in-place edits of the file; the first watch is kept.
    pub(crate) fn watch(&self, source: SourceWatch) {
        let _ = self.watch.set(source);
    }
}

impl TrackRead for Track {
    fn bases(&self) -> &[MatchBasis] {
        &self.spec.bases
    }

    fn default_basis(&self) -> MatchBasis {
        self.spec.default_basis
    }

    fn options(&self) -> &Form {
        &self.spec.options
    }

    fn snapshot(&self) -> Arc<TrackSnapshot> {
        self.growth.snapshot()
    }

    fn on_change(&self, listener: Weak<dyn Fn() + Send + Sync>) {
        self.growth.listen(listener);
    }

    fn history(&self) -> Result<Arc<History>, HistoryUnavailable> {
        let generation = self
            .growth
            .snapshot()
            .numbering
            .artifact()
            .cloned()
            .map_err(HistoryUnavailable::Failed)?;
        let found = self
            .growth
            .history
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .get(&generation.file);
        found.map_err(|miss| match miss {
            Miss::Unavailable(why) => why,
            Miss::Damaged(why) => {
                self.invalidate(&generation.file);
                match why {
                    HistoryUnavailable::Failed(error) => {
                        HistoryUnavailable::Failed(error.at(HostPath::new(&self.source)))
                    }
                    other => other,
                }
            }
        })
    }

    fn coverage(&self) -> Coverage {
        self.meta.coverage
    }

    fn filter(&self) -> &FilterPolicy {
        &self.meta.filter
    }

    fn metadata(&self) -> &Fields {
        &self.meta.metadata
    }

    /// Reads the artifact through the handle that wrote or validated it and keeps recent Scenes in a bounded cache.
    fn scene_in(&self, snapshot: &TrackSnapshot, index: usize) -> Result<Arc<Scene>, UserError> {
        self.read_scene(snapshot.numbering.artifact()?, index)
    }

    /// Recent items are kept in a bounded cache; an item larger than the whole cache is encoded again on each call.
    fn item_in(&self, snapshot: &TrackSnapshot, index: usize) -> Result<Arc<[u8]>, UserError> {
        let generation = snapshot.numbering.artifact()?;
        if let Some(item) = generation.items().get(index) {
            return Ok(item);
        }
        // Encoded outside the lock; two threads missing the same item both encode it and one copy is kept.
        let scene = self.read_scene(generation, index)?;
        let encoded = encode_sample_item(&scene, &self.meta.filter).map_err(|error| {
            UserError::new(
                ErrorCode::FileUnreadable,
                format!("Sample {index} of this overlay cannot be sent ({error})"),
            )
            .at(HostPath::new(&self.source))
        })?;
        let item: Arc<[u8]> = encoded.into();
        let bytes = item.len();
        generation.items().insert(index, item.clone(), bytes);
        Ok(item)
    }
}

impl Track {
    fn read_scene(&self, generation: &Generation, index: usize) -> Result<Arc<Scene>, UserError> {
        if let Some(scene) = generation.cache().get(index) {
            return Ok(scene);
        }
        if self.is_rewritten() {
            return Err(UserError::new(
                ErrorCode::FileChanged,
                format!(
                    "{} changed while open; no longer serving overlays from it",
                    self.source
                        .file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                ),
            )
            .at(HostPath::new(&self.source)));
        }
        // Read and decode outside the lock; two threads missing the same Scene both read it and one copy is kept.
        let scene = Arc::new(self.read(generation, index)?);
        let bytes = footprint(&scene);
        generation.cache().insert(index, scene.clone(), bytes);
        Ok(scene)
    }

    /// Whether a new open may share this track: not once its artifact is damaged, its file was written in place or
    /// its ingest ended Incomplete. Readers that hold it keep it; the new open decodes again.
    pub(crate) fn is_reusable(&self) -> bool {
        !self.is_damaged()
            && !self.is_rewritten()
            && !matches!(self.snapshot().ingest, Ingest::Incomplete(_))
    }

    /// Whether the file was written in place since the track was opened, so Scenes not yet read are not served.
    fn is_rewritten(&self) -> bool {
        self.watch.get().is_some_and(SourceWatch::rewritten)
    }

    /// Whether a record of the artifact failed its check, so the artifact is gone and a new open decodes again.
    fn is_damaged(&self) -> bool {
        self.damaged.load(Ordering::Acquire)
    }

    /// How many bytes of Scenes the cache holds.
    #[cfg(test)]
    pub(crate) fn cached_bytes(&self) -> usize {
        self.snapshot()
            .numbering
            .artifact()
            .map_or(0, |generation| generation.cache().used())
    }

    /// Marks the artifact damaged and unlinks it, so the next open decodes the file again. Readers that hold the
    /// file keep reading it; a path that names another file now (a newer artifact) is left alone.
    fn invalidate(&self, file: &File) {
        if self.damaged.swap(true, Ordering::AcqRel) {
            return;
        }
        let Some(path) = self.growth.stored.get() else {
            return;
        };
        if names_file(path, file)
            && let Err(error) = std::fs::remove_file(path)
        {
            tracing::warn!(artifact = %path.display(), %error, "could not remove a damaged scene artifact");
        }
    }

    fn read(&self, generation: &Generation, index: usize) -> Result<Scene, UserError> {
        let fail = |reason: String| {
            UserError::new(
                ErrorCode::FileUnreadable,
                format!(
                    "The scene cache of this overlay is damaged at sample {index} ({reason}); open the \
                     overlay again"
                ),
            )
            .at(HostPath::new(&self.source))
        };
        let (start, end, crc) = {
            let records = generation.records();
            match (
                records.crcs.get(index),
                records.boundaries.get(index..=index + 1),
            ) {
                (Some(&crc), Some(&[start, end])) => (start, end, crc),
                _ => return Err(fail("no such sample".to_string())),
            }
        };
        read_record(&generation.file, start, end, crc).map_err(|reason| {
            self.invalidate(&generation.file);
            fail(reason)
        })
    }
}

impl Drop for Track {
    fn drop(&mut self) {
        self.ingest.cancel();
    }
}
