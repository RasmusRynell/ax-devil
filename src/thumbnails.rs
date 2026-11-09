//! The first picture of a video as a small JPEG, kept on disk so that a hover card appears without decoding the
//! video again.
//!
//! The file name is the tag, `{identity}-{EDGE}`, where the identity changes when the video file does, so a
//! changed source is a new key and an old picture is never served for it. The newest [`MAX_FILES`] pictures by
//! modification time stay; a read renews the time. A cached file that is cut short or does not decode counts as
//! missing and is made again.
//!
//! Requests for the same tag share one read of the video (see [`flights`]). The read stops when the last of them
//! goes away, and when it fails the same tag is answered with that failure for a while instead of opening the file
//! again; a changed file has a new tag, so it is tried at once. At most two reads decode at a time; the others wait,
//! and one whose requests have all gone is dropped without decoding. The newest [`REMEMBERED`] pictures are also
//! kept in memory, so a card that is hovered again is not read from disk.

mod flights;

use bytes::Bytes;
use flights::Flights;
use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use media::{MAX_JPEG_BYTES, VideoInput, first_frame, first_frame_identity, whole_jpeg};
use std::{
    collections::VecDeque,
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::{
        Mutex, MutexGuard, PoisonError,
        atomic::{AtomicU64, Ordering},
    },
    time::SystemTime,
};
use tokio::task::spawn_blocking;

/// The folder of the pictures inside the cache folder.
pub(crate) const FOLDER: &str = "thumbnails";
/// The extension of a finished picture; anything else in the folder is not counted or pruned.
pub(crate) const EXTENSION: &str = "jpg";
/// The long side of a picture in pixels.
const EDGE: u32 = 560;
/// How many pictures the folder keeps.
const MAX_FILES: usize = 500;
/// How many pictures are kept in memory; the least recently asked for goes first.
const REMEMBERED: usize = 48;

/// What a request for a thumbnail is answered with.
pub(crate) enum Answer {
    /// The picture the client holds, named by the tag it sent, is still the current one.
    Unchanged,
    /// The current picture.
    Picture {
        /// Names this picture; it differs whenever the video file does.
        tag: String,
        /// The JPEG.
        jpeg: Bytes,
    },
}

/// The folder of cached pictures and the reads in progress.
pub(crate) struct Thumbnails {
    dir: PathBuf,
    flights: Flights,
    /// The newest pictures by tag, the most recently used last.
    remembered: Mutex<VecDeque<(String, Bytes)>>,
}

impl Thumbnails {
    /// Pictures kept in `dir`, which is created when the first one is written.
    pub(crate) fn new(dir: PathBuf) -> Self {
        Self {
            dir,
            flights: Flights::default(),
            remembered: Mutex::default(),
        }
    }

    /// The thumbnail of `input`: `Unchanged` when `known_tag` is the current one, else the picture from the cache,
    /// or decoded and cached on a miss. A request dropped before it is answered stops the decode when no other
    /// request waits for it. A cache that cannot be written costs only a warning.
    pub(crate) async fn answer(
        &self,
        input: VideoInput,
        known_tag: Option<String>,
    ) -> Result<Answer, UserError> {
        let dir = self.dir.clone();
        let (input, tag, unchanged) = spawn_blocking(move || {
            let identity = first_frame_identity(&input)?;
            let tag = format!("{identity:016x}-{EDGE}");
            let unchanged = known_tag.as_deref() == Some(tag.as_str());
            if unchanged {
                renew(&picture_path(&dir, &tag));
            }
            Ok::<_, UserError>((input, tag, unchanged))
        })
        .await
        .map_err(stopped)??;
        if unchanged {
            return Ok(Answer::Unchanged);
        }
        if let Some(jpeg) = self.recall(&tag) {
            return Ok(Answer::Picture { tag, jpeg });
        }
        let path = picture_path(&self.dir, &tag);
        let dir = self.dir.clone();
        let flight = self
            .flights
            .join(&tag, move |cancel| make(&dir, &path, &input, cancel));
        let jpeg = flight.outcome().await?;
        self.remember(&tag, &jpeg);
        Ok(Answer::Picture { tag, jpeg })
    }

    fn remembered(&self) -> MutexGuard<'_, VecDeque<(String, Bytes)>> {
        self.remembered
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }

    /// The picture of `tag` held in memory, which becomes the most recently used.
    fn recall(&self, tag: &str) -> Option<Bytes> {
        let mut remembered = self.remembered();
        let at = remembered.iter().position(|(held, _)| held == tag)?;
        let entry = remembered.remove(at)?;
        let jpeg = entry.1.clone();
        remembered.push_back(entry);
        Some(jpeg)
    }

    /// Keeps `jpeg` as the most recently used picture, forgetting the least recently used beyond [`REMEMBERED`].
    fn remember(&self, tag: &str, jpeg: &Bytes) {
        let mut remembered = self.remembered();
        remembered.retain(|(held, _)| held != tag);
        remembered.push_back((tag.to_owned(), jpeg.clone()));
        while remembered.len() > REMEMBERED {
            remembered.pop_front();
        }
    }
}

fn picture_path(dir: &Path, tag: &str) -> PathBuf {
    dir.join(format!("{tag}.{EXTENSION}"))
}

/// The picture at `path` when it is whole, else decoded from `input` and cached there.
fn make(
    dir: &Path,
    path: &Path,
    input: &VideoInput,
    cancel: &CancelToken,
) -> Result<Bytes, Interrupt<UserError>> {
    if let Some(jpeg) = read_cached(path)
        && whole_jpeg(&jpeg, EDGE)
    {
        renew(path);
        return Ok(Bytes::from(jpeg));
    }
    let jpeg = first_frame(input, EDGE, cancel)?.bytes;
    match store(path, &jpeg) {
        Ok(()) => prune(dir, MAX_FILES),
        Err(error) => tracing::warn!("The thumbnail {} was not cached: {error}", path.display()),
    }
    Ok(Bytes::from(jpeg))
}

/// The bytes of the file at `path`, at most one more than [`MAX_JPEG_BYTES`], so that a huge file in the folder is
/// never read whole; `whole_jpeg` rejects what is over the limit.
fn read_cached(path: &Path) -> Option<Vec<u8>> {
    let mut jpeg = Vec::new();
    File::open(path)
        .ok()?
        .take(MAX_JPEG_BYTES + 1)
        .read_to_end(&mut jpeg)
        .ok()?;
    Some(jpeg)
}

fn stopped(error: impl std::fmt::Display) -> UserError {
    tracing::error!("Making a thumbnail failed: {error}");
    UserError::new(ErrorCode::Internal, "The thumbnail stopped unexpectedly")
}

/// Marks the picture as just used, so pruning keeps it. Best effort: a picture that is not there has nothing to
/// renew.
fn renew(path: &Path) {
    let _ = OpenOptions::new()
        .write(true)
        .open(path)
        .and_then(|file| file.set_modified(SystemTime::now()));
}

/// Writes `jpeg` to `path` so that readers see the whole picture or none: to a private temporary file beside it,
/// which replaces `path` once complete.
fn store(path: &Path, jpeg: &[u8]) -> io::Result<()> {
    let Some(dir) = path.parent() else {
        return Err(io::Error::other("the thumbnail path has no folder"));
    };
    create_private_dir(dir)?;
    static WRITES: AtomicU64 = AtomicU64::new(0);
    let unique = WRITES.fetch_add(1, Ordering::Relaxed);
    let temporary = path.with_extension(format!("{}-{unique}.tmp", std::process::id()));
    let result = (|| {
        let mut file = private_file(&temporary)?;
        file.write_all(jpeg)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)
    })();
    if result.is_err() {
        // Best effort: a leftover temporary file is not a picture and is never read.
        let _ = fs::remove_file(&temporary);
    }
    result
}

/// Removes all but the newest `keep` pictures of `dir` by modification time. Best effort.
fn prune(dir: &Path, keep: usize) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    let mut pictures: Vec<(SystemTime, PathBuf)> = entries
        .filter_map(Result::ok)
        .filter(|entry| entry.path().extension().is_some_and(|ext| ext == EXTENSION))
        .filter_map(|entry| Some((entry.metadata().ok()?.modified().ok()?, entry.path())))
        .collect();
    // Newest first; the name settles equal times, so which of them goes is the same on every run.
    pictures.sort_by(|a, b| b.cmp(a));
    for (_, path) in pictures.into_iter().skip(keep) {
        if let Err(error) = fs::remove_file(&path) {
            tracing::warn!("The thumbnail {} was not removed: {error}", path.display());
        }
    }
}

#[cfg(unix)]
fn create_private_dir(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::DirBuilderExt;
    fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(path)
}

#[cfg(not(unix))]
fn create_private_dir(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)
}

#[cfg(unix)]
fn private_file(path: &Path) -> io::Result<File> {
    use std::os::unix::fs::OpenOptionsExt;
    OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)
}

#[cfg(not(unix))]
fn private_file(path: &Path) -> io::Result<File> {
    OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn fixture(name: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fixtures/video")
            .join(name)
    }

    fn tag_of(answer: Answer) -> String {
        match answer {
            Answer::Picture { tag, jpeg } => {
                assert!(whole_jpeg(&jpeg, EDGE), "a whole JPEG");
                tag
            }
            Answer::Unchanged => panic!("a client without a tag gets the picture"),
        }
    }

    #[tokio::test]
    async fn rewriting_the_video_gives_a_new_tag_and_the_old_one_is_no_longer_current() {
        let folder = tempfile::tempdir().unwrap();
        let video = folder.path().join("video.mp4");
        fs::copy(fixture("cfr_h264_gop10_bf2.mp4"), &video).unwrap();
        let thumbnails = Thumbnails::new(folder.path().join(FOLDER));
        let input = VideoInput::File(video.clone());

        let tag = tag_of(thumbnails.answer(input.clone(), None).await.unwrap());
        let held = Some(tag.clone());
        assert!(matches!(
            thumbnails.answer(input.clone(), held.clone()).await,
            Ok(Answer::Unchanged)
        ));
        // Served from the cache the second time, under the same tag.
        assert_eq!(
            tag_of(thumbnails.answer(input.clone(), None).await.unwrap()),
            tag
        );

        fs::copy(fixture("hevc_gop30.mp4"), &video).unwrap();
        let rewritten = tag_of(thumbnails.answer(input, held).await.unwrap());
        assert_ne!(rewritten, tag);
    }

    #[tokio::test]
    async fn a_cached_picture_that_is_cut_short_or_damaged_inside_is_made_again() {
        let input = VideoInput::File(fixture("cfr_h264_gop10_bf2.mp4"));
        for damage in ["cut short", "damaged inside"] {
            let folder = tempfile::tempdir().unwrap();
            let thumbnails = Thumbnails::new(folder.path().join(FOLDER));
            let tag = tag_of(thumbnails.answer(input.clone(), None).await.unwrap());
            let path = picture_path(&folder.path().join(FOLDER), &tag);
            let whole = fs::read(&path).unwrap();
            let mut broken = whole.clone();
            if damage == "cut short" {
                broken.truncate(whole.len() / 2);
            } else {
                // Both markers and the headers stay; most of the picture data is garbage.
                let scan = whole.windows(2).position(|w| w == [0xFF, 0xDA]).unwrap();
                let start = scan + 2 + usize::from(whole[scan + 3]) + 16;
                broken[start..whole.len() - 4].fill(0x5A);
            }
            fs::write(&path, &broken).unwrap();
            // A new instance has nothing in memory, so the damaged disk copy is what it finds.
            let thumbnails = Thumbnails::new(folder.path().join(FOLDER));

            let Answer::Picture { jpeg, .. } =
                thumbnails.answer(input.clone(), None).await.unwrap()
            else {
                panic!("a client without a tag gets the picture");
            };

            assert_eq!(jpeg, whole, "{damage}: the picture is made again");
            assert_eq!(
                fs::read(&path).unwrap(),
                whole,
                "{damage}: and cached again"
            );
        }
    }
    #[test]
    fn memory_keeps_the_most_recently_used_pictures() {
        let thumbnails = Thumbnails::new(PathBuf::new());
        let jpeg = Bytes::from_static(b"jpeg");
        for n in 0..REMEMBERED {
            thumbnails.remember(&n.to_string(), &jpeg);
        }
        assert!(thumbnails.recall("0").is_some(), "asked for again");
        thumbnails.remember("new", &jpeg);
        assert!(
            thumbnails.recall("1").is_none(),
            "the least recently used went"
        );
        assert!(thumbnails.recall("0").is_some());
        assert!(thumbnails.recall("new").is_some());
        assert_eq!(thumbnails.remembered().len(), REMEMBERED);
    }

    #[test]
    fn pruning_keeps_the_newest_pictures_and_leaves_other_files() {
        let folder = tempfile::tempdir().unwrap();
        let start = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000_000);
        for (name, age) in [("a.jpg", 1), ("b.jpg", 4), ("c.jpg", 3), ("d.jpg", 2)] {
            let file = File::create(folder.path().join(name)).unwrap();
            file.set_modified(start + Duration::from_secs(age)).unwrap();
        }
        fs::write(folder.path().join("half.tmp"), "x").unwrap();

        prune(folder.path(), 2);

        let mut left: Vec<_> = fs::read_dir(folder.path())
            .unwrap()
            .map(|entry| entry.unwrap().file_name().into_string().unwrap())
            .collect();
        left.sort();
        assert_eq!(left, ["b.jpg", "c.jpg", "half.tmp"]);
    }
}
