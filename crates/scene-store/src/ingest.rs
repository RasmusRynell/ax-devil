//! The ingest: a thread decodes the file into the artifact while the track is already readable.
//!
//! Scenes arrive through the [`SceneSink`]. Equal start keys are joined into one group; a larger key closes the
//! group into a record. While keys keep ascending, closed records are published (when the decoder gave a header)
//! and everything below the open key is final. The first key below the open one freezes that: from then on every
//! Scene is a fragment record, and at the end the fragments are sorted, joined per key and written to a new
//! artifact that replaces the published one as the next generation.

use std::{
    cell::Cell,
    cmp::Ordering,
    fs::File,
    io::BufWriter,
    ops::ControlFlow,
    path::PathBuf,
    sync::{Arc, mpsc},
    thread,
    time::{Duration, Instant},
};

use foundation::{CancelToken, ErrorCode, Interrupt, UserError, Warning};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Framing, Header, OnEqualKey, SceneSink, SkippedRecords,
    Values,
};
use overlay_sync::MatchBasis;
use scene::{Scene, SceneTime, TimeKind};

use crate::{
    format::Identity,
    group::Group,
    history::{HISTORY_BYTES, HistoryBuilder},
    input::FileInput,
    keylog::KeyLog,
    link::{Link, Presented},
    output::Output,
    settle::{Fragments, merge_fragments},
    settled::Settled,
    sort::{Entry, SORT_BUDGET_BYTES, Sorter, sort_key},
    source::LazySource,
    track::{Decided, Ingest as State, Meta, Track},
    warnings::Warnings,
    writer::RecordWriter,
};

/// The most memory the open group may take before the decode fails.
pub(crate) const GROUP_BYTES: usize = 256 << 20;

/// How often the published samples are replaced while a decode runs.
const PUBLISH_EVERY: Duration = Duration::from_millis(250);

/// Limits a test may lower.
#[derive(Clone, Copy)]
pub(crate) struct Tuning {
    /// Entry memory of the sorter before it writes runs to disk.
    pub sort_budget: usize,
    /// The shortest time between two publications.
    pub publish_every: Duration,
    /// The memory the file history may take before the file has none.
    pub history_bytes: usize,
}

impl Default for Tuning {
    fn default() -> Self {
        Self {
            sort_budget: SORT_BUDGET_BYTES,
            publish_every: PUBLISH_EVERY,
            history_bytes: HISTORY_BYTES,
        }
    }
}

/// What the opener waits for: the first moment the track can be read, or the end of a decode that gave nothing to
/// read.
pub(crate) enum First {
    Track(Arc<Track>),
    Failed(UserError),
    Cancelled,
}

/// Everything the thread needs; it owns all of it.
pub(crate) struct Job {
    pub decoder: Arc<dyn Decoder>,
    pub options: Values,
    pub source: LazySource,
    pub identity: Identity,
    pub artifact: Option<PathBuf>,
    pub cache_bytes: usize,
    pub tuning: Tuning,
    /// Stops the decode; the track cancels it when it is dropped.
    pub cancel: CancelToken,
    pub first: mpsc::Sender<First>,
}

/// Starts the ingest thread.
pub(crate) fn spawn(job: Job) -> std::io::Result<thread::JoinHandle<()>> {
    thread::Builder::new()
        .name(format!("scene-ingest-{}", job.identity.handler))
        .spawn(move || run(job))
}

fn run(job: Job) {
    let Job {
        decoder,
        options,
        source,
        identity,
        artifact,
        cache_bytes,
        tuning,
        cancel,
        first,
    } = job;
    let spec = Arc::new(decoder.spec().clone());
    let by_position = spec.bases.contains(&MatchBasis::Frame);
    let mut link = Link::new(
        source.path_buf(),
        cache_bytes,
        cancel.clone(),
        spec.clone(),
        first,
    );
    let store = match Store::start(artifact, tuning, by_position, cancel.clone()) {
        Ok(store) => store,
        Err(reason) => {
            link.fail(Interrupt::Failed(link.error(ErrorCode::Internal, reason)));
            return;
        }
    };
    let records = Cell::new(0);
    let mut input = FileInput::new(&source, spec.framing, &records);
    let mut feed = Feed {
        link,
        store,
        framing: spec.framing,
        records: &records,
        whole: Whole::default(),
        before_video: None,
        skipped: SkippedRecords::new("lines", "Those lines are not drawn."),
    };
    let result = decoder.decode(&mut input, &options, &mut feed, &cancel);
    let total = feed.lines();
    let Feed {
        mut link,
        mut store,
        skipped,
        ..
    } = feed;
    if let Some(warning) = skipped.warning(total) {
        store.warnings.push(warning);
    }
    if let Err(interrupt) = store.conclude(&mut link, result, &source, identity) {
        link.fail(interrupt);
    }
}

/// The sink the decoder feeds.
struct Feed<'a> {
    link: Link,
    store: Store,
    framing: Framing,
    /// How many records the input has given; the last is the one the decoder is working on.
    records: &'a Cell<u64>,
    /// The records of a decoder that reads the file itself, which no input counts.
    whole: Whole,
    /// The last record counted as skipped for lying before the video.
    before_video: Option<u64>,
    /// The records the decoder gave up on; the warning names them with the count of lines read.
    skipped: SkippedRecords,
}

/// The records of a decoder that reads the file itself (a plugin): a skip is one, and so are the consecutive
/// Scenes of one start key, which the host joins.
#[derive(Default)]
struct Whole {
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

    /// The records the skipped warning counts against.
    fn lines(&self) -> usize {
        let count = match self.framing {
            Framing::Records => self.records.get(),
            Framing::Whole => self.whole.count,
        };
        usize::try_from(count).unwrap_or(usize::MAX)
    }
}

/// What the store keeps while Scenes arrive.
struct Store {
    cancel: CancelToken,
    tuning: Tuning,
    output: Output,
    writer: RecordWriter<BufWriter<File>>,
    clock: Option<TimeKind>,
    header: Option<Header>,
    /// The group of the highest key so far and the position in the file of its record; `None` once the ingest is
    /// frozen or nothing has arrived.
    open: Option<(SceneTime, Group, u64)>,
    /// Set when a key below the open one arrived: from then on Scenes are fragments to be sorted.
    frozen: Option<Sorter>,
    /// How many Scenes replaced earlier ones of their key while ascending; the pass adds its own.
    replaced: usize,
    keys: KeyLog,
    /// Whether a timestamp-keyed file is also matched by frame, by the position of its samples.
    by_position: bool,
    /// The history of the records written in order; empty once the ingest froze.
    history: HistoryBuilder,
    warnings: Warnings,
    /// Why the store cannot take more.
    refused: Option<String>,
    last_publish: Option<Instant>,
    /// The records readers already have.
    published_records: usize,
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
            // The store keys frames from 0; a Scene before the video has no frame to be drawn on. A record that
            // gave several such Scenes is one skipped record.
            if self.before_video != Some(ordinal) {
                self.before_video = Some(ordinal);
                self.skipped.add(format!(
                    "line {}: frame number {frame} is before the video",
                    ordinal + 1
                ));
            }
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
        self.whole.skip();
        self.skipped.add(format!("line {position}: {reason}"));
    }

    fn warn(&mut self, warning: Warning) {
        self.store.warnings.push(warning);
    }
}

impl Store {
    fn start(
        artifact: Option<PathBuf>,
        tuning: Tuning,
        by_position: bool,
        cancel: CancelToken,
    ) -> Result<Self, String> {
        let cannot = |e: std::io::Error| format!("Cannot write the scene cache: {e}");
        let output = Output::create(artifact.as_deref()).map_err(cannot)?;
        let file = output.handle().map_err(cannot)?;
        let writer = RecordWriter::new(BufWriter::with_capacity(1 << 20, file)).map_err(cannot)?;
        Ok(Self {
            cancel,
            tuning,
            output,
            writer,
            clock: None,
            header: None,
            open: None,
            frozen: None,
            replaced: 0,
            keys: KeyLog::new(TimeKind::Frame, by_position),
            by_position,
            history: HistoryBuilder::new(tuning.history_bytes),
            warnings: Warnings::default(),
            refused: None,
            last_publish: None,
            published_records: 0,
        })
    }

    fn accept(
        &mut self,
        link: &mut Link,
        scene: Scene,
        on_equal_key: OnEqualKey,
        ordinal: u64,
    ) -> Result<(), String> {
        let start = scene.time().start();
        match self.clock {
            None => {
                self.clock = Some(start.kind());
                self.keys = KeyLog::new(start.kind(), self.by_position);
            }
            Some(kind) if kind != start.kind() => {
                return Err(
                    "the file mixes frame numbers and timestamps; a file must use one".into(),
                );
            }
            Some(_) => {}
        }
        let replace = on_equal_key == OnEqualKey::Replace;
        if let Some(sorter) = &mut self.frozen {
            return add_fragment(sorter, &mut self.writer, start, &scene, replace, ordinal);
        }
        let Some((open, group, first)) = &mut self.open else {
            self.open = Some((start, Group::new(scene), ordinal));
            return Ok(());
        };
        match start.cmp(open) {
            Ordering::Equal => {
                if replace {
                    self.replaced += 1;
                    *group = Group::new(scene);
                    *first = ordinal;
                } else {
                    group.merge(&scene).map_err(|e| e.to_string())?;
                }
                if group.bytes() > GROUP_BYTES {
                    return Err("a sample is too large to be joined".into());
                }
                Ok(())
            }
            Ordering::Greater => {
                self.close_group()?;
                self.open = Some((start, Group::new(scene), ordinal));
                self.publish_progress(link)
            }
            Ordering::Less => {
                self.freeze()?;
                match &mut self.frozen {
                    Some(sorter) => {
                        add_fragment(sorter, &mut self.writer, start, &scene, replace, ordinal)
                    }
                    None => Ok(()),
                }
            }
        }
    }

    /// Writes the open group as the next record.
    fn close_group(&mut self) -> Result<(), String> {
        if let Some((time, group, ordinal)) = self.open.take() {
            let scene = group.into_scene().map_err(|e| e.to_string())?;
            self.writer.add(&scene)?;
            self.history.add(&scene);
            self.keys.push(time, ordinal);
        }
        Ok(())
    }

    /// The open group becomes the first fragment and the watermark stops moving.
    fn freeze(&mut self) -> Result<(), String> {
        let mut sorter = Sorter::new(self.tuning.sort_budget, self.output.folder());
        if let Some((time, group, ordinal)) = self.open.take() {
            let scene = group.into_scene().map_err(|e| e.to_string())?;
            add_fragment(&mut sorter, &mut self.writer, time, &scene, false, ordinal)?;
        }
        self.frozen = Some(sorter);
        // The pass over the sorted fragments builds the history of the final numbering.
        self.history = HistoryBuilder::new(self.tuning.history_bytes);
        Ok(())
    }

    /// Publishes the closed records when the decoder gave a header and the last publication is old enough.
    fn publish_progress(&mut self, link: &mut Link) -> Result<(), String> {
        let (Some(header), Some((open, _, ordinal))) = (&self.header, &self.open) else {
            return Ok(());
        };
        if self
            .last_publish
            .is_some_and(|at| at.elapsed() < self.tuning.publish_every)
        {
            return Ok(());
        }
        self.writer.flush()?;
        let file = self.output.handle().map_err(|e| e.to_string())?;
        let keys = self.keys.publish();
        link.present(
            Presented {
                file,
                keys,
                records: self.writer.tail_from(self.published_records),
                warnings: self.warnings.list(),
                ingest: State::Reading,
                decided: Decided::below(*open, self.by_position.then_some(*ordinal)),
                stored: None,
                fresh: false,
                history: None,
            },
            || Meta::of(Some(header)),
        );
        self.published_records = self.writer.records();
        self.last_publish = Some(Instant::now());
        Ok(())
    }

    /// Whether a failed decode leaves rows worth showing: the decoder is a plugin or gave a header, so readers
    /// were promised them, and at least one sample arrived. A built-in that fails fails the open.
    fn keeps_rows(&self, incremental: bool) -> bool {
        (incremental || self.header.is_some()) && (self.open.is_some() || self.writer.records() > 0)
    }

    /// The end of the decode: settle what arrived and show it as complete, or as what is left of a failure.
    fn conclude(
        mut self,
        link: &mut Link,
        result: Result<DecodeSummary, Interrupt<DecodeError>>,
        source: &LazySource,
        identity: Identity,
    ) -> Result<(), Interrupt<UserError>> {
        // A refusal breaks the sink, and a decoder may answer that with `Cancelled` too, so only the caller's token
        // tells a cancellation from a refusal.
        let ended = match result {
            _ if self.cancel.is_cancelled() => return Err(Interrupt::Cancelled),
            _ if self.refused.is_some() => {
                let reason = self.refused.take().unwrap_or_default();
                Err(link.error(ErrorCode::Internal, reason))
            }
            Err(Interrupt::Cancelled) => return Err(Interrupt::Cancelled),
            Err(Interrupt::Failed(error)) => Err(link.error(ErrorCode::OverlayInvalid, error.0)),
            Ok(summary) => Ok(summary),
        };
        if let Err(error) = &ended
            && !self.keeps_rows(identity.code.is_some())
        {
            return Err(Interrupt::Failed(error.clone()));
        }
        let settled = self.settle(link)?;
        match ended {
            Ok(summary) => settled.complete(link, summary, source, identity),
            Err(error) => settled.incomplete(link, error),
        }
    }

    /// Closes the open group and, when the ingest froze, sorts the fragments into a new artifact.
    fn settle(mut self, link: &Link) -> Result<Settled, Interrupt<UserError>> {
        let fail = |reason: String| Interrupt::Failed(link.error(ErrorCode::Internal, reason));
        let Some(sorter) = self.frozen.take() else {
            self.close_group().map_err(fail)?;
            return Ok(Settled {
                known: self.published_records,
                fresh: false,
                replaced: self.replaced,
                writer: self.writer,
                keys: self.keys,
                history: self.history,
                output: self.output,
                header: self.header,
                warnings: self.warnings.into_list(),
                cancel: self.cancel,
            });
        };
        let io = |e: std::io::Error| fail(format!("Cannot write the scene cache: {e}"));
        self.writer.flush().map_err(fail)?;
        let reader = self.output.handle().map_err(io)?;
        let output = Output::create(self.output.target()).map_err(io)?;
        let mut writer = RecordWriter::new(BufWriter::with_capacity(
            1 << 20,
            output.handle().map_err(io)?,
        ))
        .map_err(io)?;
        let kind = self.clock.unwrap_or(TimeKind::Frame);
        let mut keys = KeyLog::new(kind, self.by_position);
        let fragments = Fragments {
            first: &self.writer,
            reader: &reader,
            kind,
            ordered: self.keys.all(),
            fragments: sorter.finish().map_err(io)?,
        };
        let replaced = match merge_fragments(
            fragments,
            &mut writer,
            &mut keys,
            &mut self.history,
            &self.cancel,
        ) {
            Ok(replaced) => replaced,
            Err(Interrupt::Cancelled) => return Err(Interrupt::Cancelled),
            Err(Interrupt::Failed(reason)) => return Err(fail(reason)),
        };
        Ok(Settled {
            known: 0,
            fresh: true,
            replaced: self.replaced + replaced,
            writer,
            keys,
            history: self.history,
            output,
            header: self.header,
            warnings: self.warnings.into_list(),
            cancel: self.cancel,
        })
    }
}

/// Writes a Scene as a fragment record and remembers its key for the sort.
fn add_fragment(
    sorter: &mut Sorter,
    writer: &mut RecordWriter<BufWriter<File>>,
    time: SceneTime,
    scene: &Scene,
    replace: bool,
    ordinal: u64,
) -> Result<(), String> {
    let record = writer.add(scene)?;
    sorter
        .push(Entry {
            key: sort_key(time),
            record,
            replace,
            ordinal,
        })
        .map_err(|e| format!("cannot write the scene cache: {e}"))
}
