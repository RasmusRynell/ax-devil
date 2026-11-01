//! Opening one track: reuse the artifact when its identity holds, else decode the file and write a new one.

use std::{
    fs::File,
    io::{self, BufWriter, Read, Seek, SeekFrom, Write},
    path::Path,
    sync::Arc,
};

use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, UserError};
use scene::SCENE_VERSION;
use scene_decoders::{DecodeContext, DecodeOptions, FileSceneDecoder};
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;

use crate::{
    TrackKey,
    format::{Identity, Index, Tail, write_tail},
    io::{create_private_dir, user_error},
    stat::Stat,
    summary::Summary,
    track::Track,
    writer::RecordWriter,
};

/// Bytes read from a source between cancellation checks.
const CHUNK: usize = 4 << 20;

/// Everything one open needs besides the decoder.
pub(crate) struct Request<'a> {
    pub key: &'a TrackKey,
    pub options: &'a DecodeOptions,
    /// Where the artifact lives; `None` keeps it in an unnamed temporary file.
    pub artifact: Option<&'a Path>,
    pub cache_bytes: usize,
}

/// Opens the track of `request.key`.
pub(crate) fn load(
    decoder: &dyn FileSceneDecoder,
    request: &Request<'_>,
    cancel: &CancelToken,
) -> Result<Arc<Track>, Interrupt<UserError>> {
    let key = request.key;
    let spec = decoder.spec();
    let failed = |error: UserError| Interrupt::Failed(error);
    let mut source = File::open(&key.path)
        .map_err(|e| failed(user_error("Cannot open the overlay file", &e, &key.path)))?;
    let stat = Stat::of(&source)
        .map_err(|e| failed(user_error("Cannot read the overlay file", &e, &key.path)))?;
    let wanted = Identity {
        handler: key.handler.clone(),
        artifact_version: spec.artifact_version,
        scene_major: SCENE_VERSION.major,
        scene_minor: SCENE_VERSION.minor,
        options: key.options.clone(),
        width: key.frame_size.width,
        height: key.frame_size.height,
        stat,
        digest: None,
    };
    if let Some(path) = request.artifact
        && let Some(track) = reuse(path, request, &wanted, &mut source, cancel)?
    {
        return Ok(track);
    }
    build(decoder, request, wanted, &mut source, cancel)
}

/// The track of a valid artifact, republishing its stat when only that differs; `None` when it cannot be used.
fn reuse(
    path: &Path,
    request: &Request<'_>,
    wanted: &Identity,
    source: &mut File,
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
    let (index, summary) = tail.index_and_summary();
    let parsed = postcard::from_bytes::<Index>(index)
        .map_err(|e| e.to_string())
        .and_then(|index| index.into_layout(tail.records_end))
        .and_then(|layout| {
            let summary = postcard::from_bytes::<Summary>(summary).map_err(|e| e.to_string())?;
            Ok((layout, summary.restore()?))
        });
    match parsed {
        Ok((layout, restored)) => Ok(Some(Arc::new(Track::new(
            request.key.path.clone(),
            file,
            Some(path.to_path_buf()),
            layout,
            restored,
            request.cache_bytes,
        )))),
        Err(why) => discard(why),
    }
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
        let (index, summary) = tail.index_and_summary();
        write_tail(&mut out, tail.records_end, index, summary, identity)?;
        out.flush()?;
    }
    temp.persist(path).map_err(|e| e.error)
}

/// Decodes the source into a new artifact.
fn build(
    decoder: &dyn FileSceneDecoder,
    request: &Request<'_>,
    mut identity: Identity,
    source: &mut File,
    cancel: &CancelToken,
) -> Result<Arc<Track>, Interrupt<UserError>> {
    let key = request.key;
    let located = |code: ErrorCode, reason: String| {
        Interrupt::Failed(UserError::new(code, reason).at(HostPath::new(&key.path)))
    };
    let mut input = Vec::with_capacity(usize::try_from(identity.stat.size).unwrap_or(0));
    let digest = read_through(source, &key.path, Some(&mut input), cancel)?;
    let output = Output::create(request.artifact).map_err(|e| {
        located(
            ErrorCode::Internal,
            format!("Cannot write the scene cache: {e}"),
        )
    })?;
    let file = output.writer().map_err(|e| {
        located(
            ErrorCode::Internal,
            format!("Cannot write the scene cache: {e}"),
        )
    })?;
    let mut writer = RecordWriter::new(BufWriter::with_capacity(1 << 20, file)).map_err(|e| {
        located(
            ErrorCode::Internal,
            format!("Cannot write the scene cache: {e}"),
        )
    })?;

    let context = DecodeContext {
        options: request.options,
        frame_size: key.frame_size,
    };
    let mut refused: Option<String> = None;
    let decoded = {
        let mut emit = |scene| {
            if refused.is_none() && !cancel.is_cancelled() {
                refused = writer.add(&scene).err();
            }
        };
        decoder.decode_file(&input, &context, &mut emit, cancel)
    };
    let decoded = match decoded {
        Ok(summary) => summary,
        Err(Interrupt::Cancelled) => return Err(Interrupt::Cancelled),
        Err(Interrupt::Failed(error)) => return Err(located(ErrorCode::OverlayInvalid, error.0)),
    };
    cancel.check()?;
    if let Some(reason) = refused {
        return Err(located(ErrorCode::Internal, reason));
    }
    drop(input);

    // A source that changed while it was read has a digest that no longer describes it: keep the artifact for this
    // session but never accept it again.
    let unchanged = Stat::of(source).is_ok_and(|now| now == identity.stat);
    identity.digest = unchanged.then_some(digest);
    let summary = Summary::of(&decoded).map_err(|e| located(ErrorCode::Internal, e))?;
    let finished = writer
        .finish(summary, &identity)
        .map_err(|e| located(ErrorCode::Internal, e))?;
    drop(finished.out);
    let file = output.publish();
    let restored = finished
        .summary
        .restore()
        .map_err(|e| located(ErrorCode::Internal, e))?;
    Ok(Arc::new(Track::new(
        key.path.clone(),
        file,
        request.artifact.map(Path::to_path_buf),
        finished.layout,
        restored,
        request.cache_bytes,
    )))
}

/// The artifact file while it is being written.
enum Output {
    /// A temporary sibling of the final path; dropping it removes it.
    Named {
        temp: NamedTempFile,
        target: std::path::PathBuf,
    },
    Anonymous(File),
}

impl Output {
    /// A named temporary file beside `target`, or an unnamed one when there is no target or no way to write there.
    fn create(target: Option<&Path>) -> io::Result<Self> {
        if let Some(target) = target {
            match temporary_beside(target) {
                Ok(temp) => {
                    return Ok(Self::Named {
                        temp,
                        target: target.to_path_buf(),
                    });
                }
                Err(error) => {
                    tracing::warn!(cache = %target.display(), %error, "scene cache folder is not writable; keeping the artifact in a temporary file");
                }
            }
        }
        tempfile::tempfile().map(Self::Anonymous)
    }

    fn writer(&self) -> io::Result<File> {
        match self {
            Self::Named { temp, .. } => temp.as_file().try_clone(),
            Self::Anonymous(file) => file.try_clone(),
        }
    }

    /// Moves a named file into place and returns the handle to read it through.
    fn publish(self) -> File {
        match self {
            Self::Anonymous(file) => file,
            Self::Named { temp, target } => match temp.persist(&target) {
                Ok(file) => file,
                Err(error) => {
                    tracing::warn!(artifact = %target.display(), error = %error.error, "could not store the scene artifact; keeping it for this session only");
                    error.file.into_file()
                }
            },
        }
    }
}

/// A temporary file in the folder of `target`, private to the user.
fn temporary_beside(target: &Path) -> io::Result<NamedTempFile> {
    let Some(folder) = target.parent() else {
        return Err(io::Error::other("the artifact path has no folder"));
    };
    create_private_dir(folder)?;
    tempfile::Builder::new()
        .prefix(".axscene-")
        .suffix(".tmp")
        .tempfile_in(folder)
}

/// Reads the whole source from its start, hashing it and optionally keeping the bytes; checks for cancellation
/// between chunks.
fn read_through(
    source: &File,
    path: &Path,
    mut keep: Option<&mut Vec<u8>>,
    cancel: &CancelToken,
) -> Result<[u8; 32], Interrupt<UserError>> {
    let fail = |error: io::Error| {
        Interrupt::Failed(user_error("Cannot read the overlay file", &error, path))
    };
    let mut reader = source;
    reader.seek(SeekFrom::Start(0)).map_err(fail)?;
    let mut hasher = Sha256::new();
    let mut chunk = vec![0_u8; CHUNK];
    loop {
        cancel.check()?;
        let read = reader.read(&mut chunk).map_err(fail)?;
        if read == 0 {
            return Ok(hasher.finalize().into());
        }
        hasher.update(&chunk[..read]);
        if let Some(bytes) = keep.as_deref_mut() {
            bytes.extend_from_slice(&chunk[..read]);
        }
    }
}
