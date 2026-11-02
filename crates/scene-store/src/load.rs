//! Opening one track: reuse the artifact when its identity holds, else start an ingest that decodes the file.

use std::{
    fs::File,
    io::{self, BufWriter, Read, Seek, SeekFrom},
    path::Path,
    sync::{Arc, mpsc},
    time::Duration,
};

use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, UserError};
use scene::SCENE_VERSION;
use scene_decoders::{DecodeOptions, DecoderSpec, FileSceneDecoder};

use crate::{
    TrackKey,
    format::{Identity, Index, Sections, Tail, write_tail},
    ingest::{First, Job, Tuning, spawn},
    io::user_error,
    output::temporary_beside,
    source::{LazySource, read_through},
    stat::Stat,
    summary::Summary,
    track::{Decided, Generation, Growth, Ingest, Meta, Records, Track, TrackSnapshot},
};

/// How often a waiting open looks at its cancel token.
const WAIT_SLICE: Duration = Duration::from_millis(20);

/// Everything one open needs besides the decoder.
pub(crate) struct Request<'a> {
    pub key: &'a TrackKey,
    pub options: &'a DecodeOptions,
    /// Where the artifact lives; `None` keeps it in an unnamed temporary file.
    pub artifact: Option<&'a Path>,
    pub cache_bytes: usize,
    pub tuning: Tuning,
}

/// Opens the track of `request.key`. A decode that must run is started on its own thread; this returns once the
/// track can be read, which for a decoder without a header is the end of the decode.
pub(crate) fn load(
    decoder: &Arc<dyn FileSceneDecoder>,
    request: &Request<'_>,
    cancel: &CancelToken,
) -> Result<Arc<Track>, Interrupt<UserError>> {
    let key = request.key;
    let spec = decoder.spec();
    let failed = |error: UserError| Interrupt::Failed(error);
    let source = File::open(&key.path)
        .map_err(|e| failed(user_error("Cannot open the overlay file", &e, &key.path)))?;
    let stat = Stat::of(&source)
        .map_err(|e| failed(user_error("Cannot read the overlay file", &e, &key.path)))?;
    let wanted = Identity {
        handler: key.handler.clone(),
        artifact_version: spec.artifact_version,
        scene_major: SCENE_VERSION.major,
        scene_minor: SCENE_VERSION.minor,
        options: key.options.clone(),
        code: decoder.code_hash(),
        stat,
        digest: None,
    };
    if let Some(path) = request.artifact
        && let Some(track) = reuse(path, request, &wanted, &source, spec, cancel)?
    {
        return Ok(track);
    }
    build(
        decoder,
        request,
        wanted,
        LazySource::new(key.path.clone(), source, stat),
        cancel,
    )
}

/// The track of a valid artifact, republishing its stat when only that differs; `None` when it cannot be used.
fn reuse(
    path: &Path,
    request: &Request<'_>,
    wanted: &Identity,
    source: &File,
    spec: &'static DecoderSpec,
    cancel: &CancelToken,
) -> Result<Option<Arc<Track>>, Interrupt<UserError>> {
    let Ok(mut file) = File::open(path) else {
        return Ok(None);
    };
    let discard = |why: String| {
        tracing::warn!(artifact = %path.display(), %why, "discarding an unusable scene artifact");
        Ok(None)
    };
    let tail = match Tail::read(&file) {
        Ok(tail) => tail,
        Err(why) => return discard(why),
    };
    let stored = match tail.identity() {
        Ok(stored) => stored,
        Err(why) => return discard(why),
    };
    let Some(digest) = stored.digest else {
        return Ok(None);
    };
    if !stored.same_decode(wanted) {
        return Ok(None);
    }
    if !wanted.stat.proves_unchanged(&stored.stat) {
        if read_through(source, &request.key.path, None, cancel)? != digest {
            return Ok(None);
        }
        let identity = Identity {
            stat: wanted.stat,
            ..stored
        };
        match republish(path, &file, &tail, &identity) {
            Ok(replacement) => file = replacement,
            Err(error) => {
                tracing::warn!(artifact = %path.display(), %error, "could not republish a scene artifact");
            }
        }
    }
    let sections = tail.sections();
    let parsed = postcard::from_bytes::<Index>(sections.index)
        .map_err(|e| e.to_string())
        .and_then(|index| index.into_layout(tail.records_end))
        .and_then(|layout| {
            let summary =
                postcard::from_bytes::<Summary>(sections.summary).map_err(|e| e.to_string())?;
            Ok((layout, summary.restore()?))
        });
    let (layout, restored) = match parsed {
        Ok(parsed) => parsed,
        Err(why) => return discard(why),
    };
    let snapshot = TrackSnapshot {
        keys: layout.keys,
        decided: Decided::All,
        ingest: Ingest::Complete,
        generation: 0,
        warnings: restored.warnings.into(),
        numbering: Arc::new(Generation::new(
            file,
            Records {
                boundaries: layout.boundaries,
                crcs: layout.crcs,
            },
            request.cache_bytes,
        )),
    };
    let growth = Arc::new(Growth::new(snapshot, Some(path.to_path_buf())));
    let meta = Meta {
        coverage: restored.coverage,
        filter: restored.filter,
        metadata: restored.metadata,
    };
    Ok(Some(Arc::new(Track::new(
        request.key.path.clone(),
        growth,
        meta,
        spec,
        CancelToken::new(),
    ))))
}

/// Replaces the artifact at `path` by a copy that carries `identity`, atomically.
pub(crate) fn republish(
    path: &Path,
    old: &File,
    tail: &Tail,
    identity: &Identity,
) -> io::Result<File> {
    let mut temp = temporary_beside(path)?;
    {
        let mut out = BufWriter::with_capacity(1 << 20, temp.as_file_mut());
        let mut records = old.try_clone()?;
        records.seek(SeekFrom::Start(0))?;
        io::copy(&mut records.take(tail.records_end), &mut out)?;
        let identity = identity.to_bytes()?;
        write_tail(
            &mut out,
            tail.records_end,
            Sections {
                identity: &identity,
                ..tail.sections()
            },
        )?;
        io::Write::flush(&mut out)?;
    }
    temp.persist(path).map_err(|e| e.error)
}

/// Starts the ingest and waits until the track can be read. Cancelling before that stops the ingest and waits for
/// it to remove its files; after that the track's own drop stops it.
fn build(
    decoder: &Arc<dyn FileSceneDecoder>,
    request: &Request<'_>,
    identity: Identity,
    source: LazySource,
    cancel: &CancelToken,
) -> Result<Arc<Track>, Interrupt<UserError>> {
    let (first, wait) = mpsc::channel();
    let ingest = CancelToken::new();
    let stopped = |reason: String| {
        Interrupt::Failed(
            UserError::new(ErrorCode::Internal, reason).at(HostPath::new(&request.key.path)),
        )
    };
    let handle = spawn(Job {
        decoder: decoder.clone(),
        options: request.options.clone(),
        source,
        identity,
        artifact: request.artifact.map(Path::to_path_buf),
        cache_bytes: request.cache_bytes,
        tuning: request.tuning,
        cancel: ingest.clone(),
        first,
    })
    .map_err(|e| stopped(format!("Cannot start reading the overlay: {e}")))?;
    let outcome = loop {
        if cancel.is_cancelled() {
            break Err(Interrupt::Cancelled);
        }
        match wait.recv_timeout(WAIT_SLICE) {
            Ok(First::Track(track)) => return Ok(track),
            Ok(First::Failed(error)) => break Err(Interrupt::Failed(error)),
            Ok(First::Cancelled) => break Err(Interrupt::Cancelled),
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                break Err(stopped("The overlay reader stopped unexpectedly".into()));
            }
        }
    };
    ingest.cancel();
    // The thread removes its files as it ends; the caller can rely on that.
    let _ = handle.join();
    outcome
}
