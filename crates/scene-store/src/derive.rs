//! Opening a derived lane: its input lanes are opened, written as lane files and listed in a bundle, and the bundle is
//! what the lane's decoder reads.
//!
//! Lane files and bundles are named by the digest of their content, so an input that did not change finds its file and
//! a changed one gives another bundle, which the track key and the artifact then treat as another file.

use std::{
    collections::BTreeMap,
    fs,
    io::{BufWriter, Write},
    path::{Path, PathBuf},
    thread,
    time::{Duration, UNIX_EPOCH},
};

use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use handler_api::{Bundle, BundleLane, BundleVideo, OverlayRef, VideoRef};
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;

use crate::{
    Admission, Ingest, TrackRead, TrackRegistry,
    io::{create_private_dir, user_error},
    lanefile::{hex, write_lane},
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
        let mut bundle = Bundle {
            lanes: BTreeMap::new(),
            video: None,
        };
        for (key, input) in lanes {
            let track = self.open_as(input, admission, cancel)?;
            wait_complete(&*track, cancel)?;
            let (path, digest) = store_lane(&root.join(LANE_FOLDER), &*track, cancel)?;
            bundle.lanes.insert(
                key.clone(),
                BundleLane {
                    path,
                    sha256: hex(&digest),
                },
            );
        }
        bundle.video = video
            .map(bundle_video)
            .transpose()
            .map_err(Interrupt::Failed)?;
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

/// The bundle's entry for a video: its file's size and modification time. An image sequence is named by its pattern
/// and image count.
fn bundle_video(video: &VideoRef) -> Result<BundleVideo, UserError> {
    match video {
        VideoRef::File(path) => {
            let file = fs::canonicalize(path)
                .map_err(|error| user_error("Cannot find the video", &error, path))?;
            let stat = fs::metadata(&file)
                .map_err(|error| user_error("Cannot read the video", &error, &file))?;
            let mtime_ns =
                stat.modified()
                    .map_or(0, |modified| match modified.duration_since(UNIX_EPOCH) {
                        Ok(after) => i128::try_from(after.as_nanos()).unwrap_or(i128::MAX),
                        Err(before) => {
                            -i128::try_from(before.duration().as_nanos()).unwrap_or(i128::MAX)
                        }
                    });
            Ok(BundleVideo {
                path: file,
                size: stat.len(),
                mtime_ns,
            })
        }
        VideoRef::ImageSequence(sequence) => Ok(BundleVideo {
            path: sequence.pattern.clone(),
            size: sequence.frame_count,
            mtime_ns: 0,
        }),
    }
}
