//! Opening a derived lane: its input lanes are opened, written as lane files and listed in a bundle, and the bundle is
//! what the lane's decoder reads.
//!
//! Lane files and bundles are named by the digest of their content, so an input that did not change finds its file and
//! a changed one gives another bundle, which the track key and the artifact then treat as another file.

use std::{
    collections::BTreeMap,
    fs,
    io::{self, BufWriter, Write},
    path::{Path, PathBuf},
    sync::Arc,
    thread,
    time::Duration,
};

use foundation::{CancelToken, ErrorCode, Interrupt, UserError, hex};
use handler_api::{Bundle, BundleLane, BundleVideo, OverlayRef, VideoRef};
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;

use crate::{
    Admission, Ingest, TrackRead, TrackRegistry,
    io::{create_private_dir, user_error},
    lanefile::write_lane,
    stat::Stat,
};

/// The folder under the cache folder that holds lane files.
pub const LANE_FOLDER: &str = "lanes";
/// The extension of a lane file.
pub const LANE_EXTENSION: &str = "jsonl";
/// The folder under the cache folder that holds bundles.
pub const DERIVED_FOLDER: &str = "derived";
/// The extension of a bundle.
pub const DERIVED_EXTENSION: &str = "json";

/// How often a wait for an input's ingest looks at its cancel token.
const WAIT_SLICE: Duration = Duration::from_millis(20);

impl TrackRegistry {
    /// Opens `lanes` with `admission`, waits for each to be read completely, and returns the path of the bundle that
    /// names their lane files and `video`.
    pub(crate) fn derive(
        &self,
        lanes: &[(String, OverlayRef)],
        video: Option<&VideoRef>,
        admission: Admission,
        cancel: &CancelToken,
    ) -> Result<PathBuf, Interrupt<UserError>> {
        let root = self.derived_root().map_err(Interrupt::Failed)?;
        let bundle = build_bundle(
            lanes,
            video,
            &root.join(LANE_FOLDER),
            |input| Ok(self.open_as(input, admission, cancel)? as Arc<dyn TrackRead>),
            cancel,
        )?;
        let bytes = bundle.to_bytes();
        let folder = root.join(DERIVED_FOLDER);
        let mut temp = temporary_in(&folder).map_err(Interrupt::Failed)?;
        temp.write_all(&bytes)
            .map_err(|error| user_error("Cannot write the derived lane's inputs", &error, &folder))
            .map_err(Interrupt::Failed)?;
        publish(temp, &folder, &Sha256::digest(&bytes), DERIVED_EXTENSION)
            .map_err(Interrupt::Failed)
    }

    /// The folder lane files and bundles go under: the cache folder, or one private to this registry.
    fn derived_root(&self) -> Result<PathBuf, UserError> {
        if let Some(cache) = &self.cache_dir {
            return Ok(cache.clone());
        }
        if let Some(scratch) = self.scratch.get() {
            return Ok(scratch.path().to_path_buf());
        }
        let dir = tempfile::tempdir().map_err(|error| {
            UserError::new(
                ErrorCode::Internal,
                format!("Cannot make a folder for derived lanes: {error}"),
            )
        })?;
        Ok(self.scratch.get_or_init(|| dir).path().to_path_buf())
    }
}

/// Opens each of `lanes` with `open`, waits for it to be read completely, writes it as a lane file in `folder`, and
/// returns the bundle that names the lane files and `video`. An input that stopped early fails the build.
pub fn build_bundle(
    lanes: &[(String, OverlayRef)],
    video: Option<&VideoRef>,
    folder: &Path,
    open: impl Fn(&OverlayRef) -> Result<Arc<dyn TrackRead>, Interrupt<UserError>>,
    cancel: &CancelToken,
) -> Result<Bundle, Interrupt<UserError>> {
    let mut bundle = Bundle {
        lanes: BTreeMap::new(),
        video: None,
    };
    for (key, input) in lanes {
        let track = open(input)?;
        wait_complete(&*track, cancel)?;
        let (path, digest) = store_lane(folder, &*track, cancel)?;
        bundle.lanes.insert(
            key.clone(),
            BundleLane {
                path,
                sha256: hex(&digest),
            },
        );
    }
    if let Some(video) = video {
        let named = match video {
            VideoRef::File(path) => path,
            VideoRef::ImageSequence(sequence) => &sequence.pattern,
        };
        bundle.video = Some(
            bundle_video(video)
                .map_err(|error| user_error("Cannot read the video", &error, named))
                .map_err(Interrupt::Failed)?,
        );
    }
    Ok(bundle)
}

/// The bundle entry for `video`: a file by its size and modification time, an image sequence by its pattern, count and
/// a digest of every image's size and modification time. Costs one `stat` per image and reads no image. A missing
/// image is part of the identity, not an error here: the decoder that needs it reports it.
fn bundle_video(video: &VideoRef) -> io::Result<BundleVideo> {
    let measure = |path: &Path| fs::metadata(path).map(|metadata| Stat::from_metadata(&metadata));
    match video {
        VideoRef::File(path) => {
            let file = fs::canonicalize(path)?;
            let stat = measure(&file)?;
            Ok(BundleVideo {
                path: file,
                size: stat.size,
                mtime_ns: stat.mtime_ns.into(),
                images_sha256: None,
            })
        }
        VideoRef::ImageSequence(sequence) => {
            let numbering = Numbering::parse(&sequence.pattern)?;
            let mut digest = Sha256::new();
            for index in 0..sequence.frame_count {
                let number = u64::from(sequence.start_number) + index;
                match measure(&numbering.path(number)) {
                    Ok(stat) => {
                        digest.update([1]);
                        digest.update(stat.size.to_le_bytes());
                        digest.update(i128::from(stat.mtime_ns).to_le_bytes());
                    }
                    Err(_) => digest.update([0]),
                }
            }
            Ok(BundleVideo {
                path: sequence.pattern.clone(),
                size: sequence.frame_count,
                mtime_ns: 0,
                images_sha256: Some(hex(&digest.finalize())),
            })
        }
    }
}

/// A file name pattern with one `%d` or `%0Nd` in it.
struct Numbering {
    before: String,
    width: usize,
    after: String,
}

impl Numbering {
    fn parse(pattern: &Path) -> io::Result<Self> {
        let invalid = |reason: &str| io::Error::new(io::ErrorKind::InvalidInput, reason.to_owned());
        let text = pattern
            .to_str()
            .ok_or_else(|| invalid("the image pattern is not valid text"))?;
        let at = text
            .find('%')
            .ok_or_else(|| invalid("the image pattern has no number in it"))?;
        let rest = &text[at + 1..];
        let digits = rest.chars().take_while(char::is_ascii_digit).count();
        let (padding, after) = rest.split_at(digits);
        let after = after
            .strip_prefix('d')
            .filter(|after| !after.contains('%'))
            .ok_or_else(|| invalid("the image pattern must hold exactly one %d or %06d"))?;
        let width = if padding.is_empty() {
            0
        } else {
            padding
                .parse()
                .map_err(|_| invalid("the image pattern's padding is too wide"))?
        };
        Ok(Self {
            before: text[..at].to_owned(),
            width,
            after: after.to_owned(),
        })
    }

    fn path(&self, number: u64) -> PathBuf {
        let Self {
            before,
            width,
            after,
        } = self;
        PathBuf::from(format!("{before}{number:0width$}{after}"))
    }
}

/// Waits until `track` has read everything it will; an input that stopped early fails the open.
fn wait_complete(track: &dyn TrackRead, cancel: &CancelToken) -> Result<(), Interrupt<UserError>> {
    loop {
        match &track.snapshot().ingest {
            Ingest::Complete => return Ok(()),
            Ingest::Incomplete(error) => return Err(Interrupt::Failed(error.clone())),
            Ingest::Reading => {}
        }
        cancel.check()?;
        thread::sleep(WAIT_SLICE);
    }
}

/// Writes `track` as a lane file named by its content and returns its path and digest.
fn store_lane(
    folder: &Path,
    track: &dyn TrackRead,
    cancel: &CancelToken,
) -> Result<(PathBuf, [u8; 32]), Interrupt<UserError>> {
    let mut temp = temporary_in(folder).map_err(Interrupt::Failed)?;
    let mut out = BufWriter::new(temp.as_file_mut());
    let digest = write_lane(track, None, u64::MAX, &mut out, cancel)?;
    out.flush()
        .map_err(|error| user_error("Cannot write the lane", &error, folder))
        .map_err(Interrupt::Failed)?;
    drop(out);
    let path = publish(temp, folder, &digest, LANE_EXTENSION).map_err(Interrupt::Failed)?;
    Ok((path, digest))
}

/// A temporary file in `folder`, private to the user.
fn temporary_in(folder: &Path) -> Result<NamedTempFile, UserError> {
    create_private_dir(folder)
        .and_then(|()| {
            tempfile::Builder::new()
                .prefix(".derived-")
                .suffix(".tmp")
                .tempfile_in(folder)
        })
        .map_err(|error| user_error("Cannot write in the cache folder", &error, folder))
}

/// Moves `temp` to the name of its `digest` in `folder`, unless that file is already there, and returns the path.
fn publish(
    temp: NamedTempFile,
    folder: &Path,
    digest: &[u8],
    extension: &str,
) -> Result<PathBuf, UserError> {
    let path = folder.join(format!("{}.{extension}", hex(digest)));
    if !path.exists() {
        temp.persist(&path)
            .map_err(|error| user_error("Cannot write in the cache folder", &error.error, &path))?;
    }
    fs::canonicalize(&path)
        .map_err(|error| user_error("Cannot find the stored lane", &error, &path))
}
