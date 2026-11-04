//! A track fed by a live source: Scenes arrive in time order, only the newest ones are kept, and nothing is stored.

use std::{
    ops::ControlFlow,
    sync::{
        Arc, Mutex, OnceLock, PoisonError, RwLock, Weak,
        atomic::{AtomicUsize, Ordering as AtomicOrdering},
    },
    thread,
};

use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, MediaTime, UserError, Warning};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Form, Header, Input, OnEqualKey, Record, SceneSink,
    SkippedRecords, Values,
};
use overlay_sync::{KeyChunks, MatchBasis, SampleKeys};
use scene::{
    Coverage, Fields, FilterPolicy, Scene, SceneTime, serial::columns::encode_sample_item,
};

use crate::{
    History, HistoryUnavailable, TrackRead,
    group::Group,
    ingest::GROUP_BYTES,
    lru::ItemCache,
    track::{Decided, Growth, ITEM_CACHE_BYTES, Ingest, Meta, Numbering, TrackSnapshot},
    warnings::Warnings,
};

/// Where a live track gets its records: one [`Input`] per session of the source.
pub trait Feed: Send {
    /// The input of the next session, waiting for the source to offer one; `None` when no session will come, or
    /// when `cancel` fires. The previous input has been read to its end or given up on.
    fn next_input(&mut self, cancel: &CancelToken) -> Option<Box<dyn Input + Send>>;
}

/// An input that counts the records it gives, for the skipped warning.
struct Counted {
    inner: Box<dyn Input + Send>,
    records: Arc<AtomicUsize>,
}

impl Input for Counted {
    fn path(&self) -> Option<&std::path::Path> {
        self.inner.path()
    }

    fn next(&mut self, cancel: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        let next = self.inner.next(cancel)?;
        if next.is_some() {
            self.records.fetch_add(1, AtomicOrdering::Relaxed);
        }
        Ok(next)
    }
}

/// The Scenes of one numbering of a live track, in sample order. A snapshot reads its own numbering, so one the track
/// has since renumbered stays readable until it is dropped.
pub(crate) struct Samples {
    scenes: RwLock<Vec<Arc<Scene>>>,
    items: Mutex<ItemCache>,
}

impl Samples {
    fn new(scenes: Vec<Arc<Scene>>) -> Self {
        Self {
            scenes: RwLock::new(scenes),
            items: Mutex::new(ItemCache::new(ITEM_CACHE_BYTES)),
        }
    }

    fn scene(&self, index: usize) -> Result<Arc<Scene>, UserError> {
        let held = self.scenes.read().unwrap_or_else(PoisonError::into_inner);
        held.get(index).cloned().ok_or_else(|| {
            UserError::new(
                ErrorCode::Internal,
                format!("Live sample {index} is not held"),
            )
        })
    }

    fn items(&self) -> std::sync::MutexGuard<'_, ItemCache> {
        self.items.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

impl std::fmt::Debug for Samples {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Samples").finish_non_exhaustive()
    }
}

/// What a header said, once a session has given one; read through [`LiveTrack`] and set by the thread.
struct Heard {
    given: OnceLock<Meta>,
    /// What reads as the header until one is given.
    default: Meta,
}

impl Heard {
    fn meta(&self) -> &Meta {
        self.given.get().unwrap_or(&self.default)
    }
}

/// A track over a live feed, matched to the video by time.
///
/// A thread decodes each [`Input`] the feed gives, one after the other, and the sink keeps the newest Scene open until
/// a later one arrives or its session ends. The track holds at most `cap` published samples: when there are more it
/// drops the oldest half at once, which renumbers the rest (a new generation), so a viewer renumbers once per batch
/// and not once per sample. The ingest never ends; dropping the track stops the thread.
pub struct LiveTrack {
    options: Form,
    growth: Arc<Growth>,
    heard: Arc<Heard>,
    /// The sample the sink holds open, which [`LiveTrack::snapshot_with_open`] reads.
    open: Arc<Mutex<Option<Open>>>,
    cancel: CancelToken,
}

/// The newest sample, which a Scene with its key may still join.
struct Open {
    time: MediaTime,
    group: Group,
    /// Whether a reader took the group as a sample of a snapshot: joining more into it then renumbers.
    exposed: bool,
}

impl Open {
    fn new(time: MediaTime, scene: Scene) -> Self {
        Self {
            time,
            group: Group::new(scene),
            exposed: false,
        }
    }
}

impl LiveTrack {
    /// Starts decoding what `feed` gives with `decoder` and `values`, keeping at most `cap` samples. A `cap` below 2
    /// is refused: dropping half of the samples would drop the newest too.
    pub fn start(
        decoder: Arc<dyn Decoder>,
        values: Values,
        feed: Box<dyn Feed>,
        cap: usize,
    ) -> std::io::Result<Arc<Self>> {
        if cap < 2 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "a live track keeps at least 2 samples",
            ));
        }
        let cancel = CancelToken::new();
        let heard = Arc::new(Heard {
            given: OnceLock::new(),
            default: Meta::of(None),
        });
        let samples = Arc::new(Samples::new(Vec::new()));
        let mut sink = Sink {
            growth: Arc::new(Growth::new(
                snapshot(KeyChunks::new(), None, 0, &samples, Vec::new()),
                None,
            )),
            heard: heard.clone(),
            cancel: cancel.clone(),
            cap,
            samples,
            keys: KeyChunks::new(),
            open: Arc::default(),
            closed: None,
            generation: 0,
            late: 0,
            records: Arc::default(),
            skipped: SkippedRecords::new("records", "They are not drawn."),
            warnings: Warnings::default(),
        };
        let track = Arc::new(Self {
            options: decoder.spec().options.clone(),
            growth: sink.growth.clone(),
            heard,
            open: sink.open.clone(),
            cancel: cancel.clone(),
        });
        thread::Builder::new()
            .name(format!("scene-live-{}", decoder.spec().id))
            .spawn(move || run(&*decoder, &values, feed, &mut sink, &cancel))?;
        Ok(track)
    }

    /// The snapshot with the sample still open as its newest sample, for a frame that cannot wait for the next one
    /// to close it. Equal to [`TrackRead::snapshot`] when no sample is open.
    ///
    /// The open sample is numbered after the published ones and keeps its number when it is published. Taking it
    /// freezes what it holds: a Scene that later joins it renumbers the track, so no reader keeps the old content.
    pub fn snapshot_with_open(&self) -> Arc<TrackSnapshot> {
        let mut slot = self.open.lock().unwrap_or_else(PoisonError::into_inner);
        let base = self.growth.snapshot();
        let Some(open) = slot.as_mut() else {
            return base;
        };
        let (Some(times), Ok(samples)) = (&base.keys.times, base.numbering.memory()) else {
            return base;
        };
        let Ok(scene) = open.group.to_scene() else {
            return base;
        };
        let published = {
            let held = samples
                .scenes
                .read()
                .unwrap_or_else(PoisonError::into_inner);
            held.get(..times.len()).map(<[_]>::to_vec)
        };
        let Some(mut scenes) = published else {
            return base;
        };
        scenes.push(Arc::new(scene));
        open.exposed = true;
        Arc::new(TrackSnapshot {
            keys: SampleKeys {
                frames: None,
                times: Some(times.appended(&[open.time])),
            },
            numbering: Numbering::Memory(Arc::new(Samples::new(scenes))),
            ..(*base).clone()
        })
    }
}

impl Drop for LiveTrack {
    fn drop(&mut self) {
        self.cancel.cancel();
    }
}

impl TrackRead for LiveTrack {
    fn bases(&self) -> &[MatchBasis] {
        &[MatchBasis::Time]
    }

    fn default_basis(&self) -> MatchBasis {
        MatchBasis::Time
    }

    fn options(&self) -> &Form {
        &self.options
    }

    fn snapshot(&self) -> Arc<TrackSnapshot> {
        self.growth.snapshot()
    }

    fn on_change(&self, listener: Weak<dyn Fn() + Send + Sync>) {
        self.growth.listen(listener);
    }

    fn history(&self) -> Result<Arc<History>, HistoryUnavailable> {
        Err(HistoryUnavailable::Reading)
    }

    fn coverage(&self) -> Coverage {
        self.heard.meta().coverage
    }

    fn filter(&self) -> &FilterPolicy {
        &self.heard.meta().filter
    }

    fn metadata(&self) -> &Fields {
        &self.heard.meta().metadata
    }

    fn scene_in(&self, snapshot: &TrackSnapshot, index: usize) -> Result<Arc<Scene>, UserError> {
        snapshot.numbering.memory()?.scene(index)
    }

    fn item_in(&self, snapshot: &TrackSnapshot, index: usize) -> Result<Arc<[u8]>, UserError> {
        let samples = snapshot.numbering.memory()?;
        if let Some(item) = samples.items().get(index) {
            return Ok(item);
        }
        let scene = samples.scene(index)?;
        let given = self.heard.given.get().is_some();
        let encoded = encode_sample_item(&scene, self.filter()).map_err(|error| {
            UserError::new(
                ErrorCode::Internal,
                format!("Live sample {index} cannot be sent ({error})"),
            )
            .at(HostPath::new("live"))
        })?;
        let item: Arc<[u8]> = encoded.into();
        let bytes = item.len();
        // A header or summary that came while this encoded under the default filter replaces the cache (see
        // `Sink::ended`), so under the cache's lock an item made without it is not kept.
        let mut items = samples.items();
        if given || self.heard.given.get().is_none() {
            items.insert(index, item.clone(), bytes);
        }
        Ok(item)
    }
}

/// The snapshot of a live track: `keys` published, `frontier` the key of the newest sample, if any: frames below it
/// are decided.
fn snapshot(
    keys: KeyChunks<MediaTime>,
    frontier: Option<MediaTime>,
    generation: u32,
    samples: &Arc<Samples>,
    warnings: Vec<Warning>,
) -> TrackSnapshot {
    TrackSnapshot {
        keys: SampleKeys {
            frames: None,
            times: Some(keys),
        },
        // Until a sample is open nothing is decided: any frame may still get the first.
        decided: Decided::Below {
            frame: None,
            time: Some(frontier.unwrap_or(MediaTime(i64::MIN))),
        },
        ingest: Ingest::Reading,
        generation,
        warnings: warnings.into(),
        numbering: Numbering::Memory(samples.clone()),
    }
}

/// Reads the inputs the feed gives, one session each; the call of `decode` is the session.
fn run(
    decoder: &dyn Decoder,
    values: &Values,
    mut feed: Box<dyn Feed>,
    sink: &mut Sink,
    cancel: &CancelToken,
) {
    while let Some(input) = feed.next_input(cancel) {
        let mut input = Counted {
            inner: input,
            records: sink.records.clone(),
        };
        match decoder.decode(&mut input, values, sink, cancel) {
            Ok(summary) => sink.ended(summary),
            Err(Interrupt::Cancelled) => return,
            Err(Interrupt::Failed(error)) => sink.warnings.push(Warning {
                problem: "A live session ended with an error".into(),
                reasons: vec![error.to_string()],
                consequence: "The next session starts from the source again.".into(),
                more_reasons: false,
            }),
        }
        if cancel.is_cancelled() {
            return;
        }
        sink.end_session();
    }
}

/// Where the decoder puts its Scenes: the newest stays open, a later one publishes it.
struct Sink {
    growth: Arc<Growth>,
    heard: Arc<Heard>,
    cancel: CancelToken,
    cap: usize,
    /// The numbering of the published samples.
    samples: Arc<Samples>,
    /// The keys of the published samples.
    keys: KeyChunks<MediaTime>,
    /// The newest sample. Held while a transition changes it together with the snapshot, so a reader sees both agree.
    open: Arc<Mutex<Option<Open>>>,
    /// The key of the newest published sample, which a later Scene must be above.
    closed: Option<MediaTime>,
    generation: u32,
    /// How many Scenes arrived with a key below the open one and were dropped.
    late: usize,
    /// How many records the inputs gave, for the skipped warning.
    records: Arc<AtomicUsize>,
    skipped: SkippedRecords,
    warnings: Warnings,
}

impl SceneSink for Sink {
    fn header(&mut self, header: Header) {
        // The first header stands: later sessions of one source give the same one.
        let _ = self.heard.given.set(Meta::of(Some(&header)));
    }

    fn sample(&mut self, scene: Scene, on_equal_key: OnEqualKey) -> ControlFlow<()> {
        if self.cancel.is_cancelled() {
            return ControlFlow::Break(());
        }
        let SceneTime::Timestamp(time) = scene.time().start() else {
            self.skipped.add("a live sample needs a timestamp");
            return ControlFlow::Continue(());
        };
        let slot = Arc::clone(&self.open);
        let mut open = slot.lock().unwrap_or_else(PoisonError::into_inner);
        let published = self.admit(&mut open, time, scene, on_equal_key);
        drop(open);
        if published {
            self.growth.notify();
        }
        ControlFlow::Continue(())
    }

    fn skip(&mut self, _position: u64, reason: String) {
        self.skipped.add(reason);
    }

    fn warn(&mut self, warning: Warning) {
        self.warnings.push(warning);
    }
}

impl Sink {
    /// Joins `scene`, keyed `time`, to the open sample, or opens a new one. True when a snapshot was stored.
    fn admit(
        &mut self,
        open: &mut Option<Open>,
        time: MediaTime,
        scene: Scene,
        on_equal_key: OnEqualKey,
    ) -> bool {
        let Some(current) = open.as_mut() else {
            if self.closed.is_some_and(|closed| time <= closed) {
                self.late += 1;
                return false;
            }
            *open = Some(Open::new(time, scene));
            self.store(open);
            return true;
        };
        if time > current.time {
            self.close_open(open);
            *open = Some(Open::new(time, scene));
            self.store(open);
            return true;
        }
        if time < current.time {
            self.late += 1;
            return false;
        }
        if on_equal_key == OnEqualKey::Replace {
            current.group = Group::new(scene);
        } else if current.group.bytes() > GROUP_BYTES {
            self.skipped.add("a sample is too large to be joined");
        } else if let Err(error) = current.group.merge(&scene) {
            self.skipped.add(error.to_string());
        }
        if !std::mem::take(&mut current.exposed) {
            return false;
        }
        // A reader holds the sample as it was: the track is numbered anew, so readers drop it.
        self.generation += 1;
        self.store(open);
        true
    }

    /// Ends a session: the open sample is published as the newest one.
    fn end_session(&mut self) {
        let slot = Arc::clone(&self.open);
        let mut open = slot.lock().unwrap_or_else(PoisonError::into_inner);
        self.close_open(&mut open);
        self.store(&open);
        drop(open);
        self.growth.notify();
    }

    /// Publishes the open sample's Scene as the newest sample; drops the oldest half in one step when there are more
    /// than `cap`, so the samples are renumbered once per batch.
    fn close_open(&mut self, open: &mut Option<Open>) {
        let Some(Open { time, group, .. }) = open.take() else {
            return;
        };
        let scene = match group.into_scene() {
            Ok(scene) => Arc::new(scene),
            Err(error) => {
                self.skipped.add(error.to_string());
                return;
            }
        };
        let mut held = self
            .samples
            .scenes
            .write()
            .unwrap_or_else(PoisonError::into_inner);
        held.push(scene);
        self.closed = Some(time);
        self.keys = self.keys.appended(&[time]);
        if held.len() > self.cap {
            let dropped = held.len().div_ceil(2);
            let kept = held[dropped..].to_vec();
            drop(held);
            self.keys = self.keys.iter().skip(dropped).collect();
            self.samples = Arc::new(Samples::new(kept));
            self.generation += 1;
        }
    }

    /// Shows readers what the sink holds now, without telling the listeners.
    fn store(&mut self, open: &Option<Open>) {
        let mut warnings = self.warnings.list();
        if self.late > 0 {
            warnings.push(Warning {
                problem: format!(
                    "Dropped {} live samples that arrived after a later one",
                    self.late
                ),
                reasons: Vec::new(),
                consequence: "They are not drawn.".into(),
                more_reasons: false,
            });
        }
        let total = self.records.load(AtomicOrdering::Relaxed);
        warnings.extend(self.skipped.clone().warning(total));
        let frontier = open.as_ref().map(|open| open.time).or(self.closed);
        self.growth.store(snapshot(
            self.keys.clone(),
            frontier,
            self.generation,
            &self.samples,
            warnings,
        ));
    }

    /// Takes what a finished session reports. A decoder that gave no header has its filter, coverage and metadata
    /// from the first summary, which also replaces what the samples were encoded under.
    fn ended(&mut self, summary: DecodeSummary) {
        let meta = Meta {
            coverage: summary.coverage,
            filter: summary.filter,
            metadata: summary.metadata,
        };
        if self.heard.given.set(meta).is_ok() {
            *self.samples.items() = ItemCache::new(ITEM_CACHE_BYTES);
        }
        for warning in summary.warnings {
            self.warnings.push(warning);
        }
    }
}
