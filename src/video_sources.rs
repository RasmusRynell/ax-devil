//! Videos as playback sources: the decoder behind the playback crate's trait, and opening a viewer on a video file
//! or an image sequence.
//!
//! The playback crate knows frames and windows but not FFmpeg, and the media crate knows FFmpeg but not windows;
//! this module is where they meet.

use foundation::{
    CancelToken, ErrorCode, FrameFailure, FrameRate, FrameSize, HostPath, Interrupt, UserError,
    VideoFrame,
};
use media::{Cadence, Decoder, FrameIndex, ImageSequenceInput, PtsTiming, VideoInput};
use playback::{DecoderFactory, Registry, Session, SourceKey, SourceSpec, SpanDecoder};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, Weak};
use workspace::VideoRef;

/// A media decoder as a playback span decoder: the playback crate cannot depend on the media crate.
struct MediaDecoder(Decoder);

impl SpanDecoder for MediaDecoder {
    fn position(&self) -> Option<u64> {
        self.0.position()
    }

    fn seek_to_keyframe(&mut self, keyframe: u64) -> Result<(), UserError> {
        self.0.seek_to_keyframe(keyframe)
    }

    fn next_frame(&mut self) -> Result<Option<VideoFrame>, FrameFailure> {
        self.0.next_frame()
    }
}

fn decoder_factory(
    input: VideoInput,
    index: Arc<FrameIndex>,
    details: Arc<Details>,
) -> DecoderFactory {
    Arc::new(move |cancel: &CancelToken, threads: usize| {
        // The source keeps this factory while it is open, which keeps its details findable (see `VideoSources`).
        let _details = &details;
        let decoder = Decoder::open(&input, Arc::clone(&index), threads, cancel.clone()).map_err(
            |interrupt| match interrupt {
                Interrupt::Failed(error) => error,
                // The source cancels the token when it closes, and nobody is left to be told.
                Interrupt::Cancelled => {
                    UserError::new(ErrorCode::VideoClosed, "The video was closed")
                        .at(HostPath::new(input.location()))
                }
            },
        )?;
        Ok(Box::new(MediaDecoder(decoder)) as Box<dyn SpanDecoder>)
    })
}

/// What a viewer is told about a video besides its timeline, which the session carries, and what an export reads it
/// with.
struct Details {
    size: FrameSize,
    warnings: Vec<String>,
    input: VideoInput,
    index: Arc<FrameIndex>,
}

/// A viewer of an open video.
pub struct OpenedVideo {
    /// The viewer's place on the source.
    pub session: Session,
    /// The size of the decoded pictures.
    pub size: FrameSize,
    /// Things worth telling the user about the video, such as a missing last image. They name the host's files, so
    /// only an audience with host details is told.
    pub warnings: Vec<String>,
    /// How the frame times come from the container's timestamps; `None` for an image sequence.
    pub pts: Option<PtsTiming>,
    /// How evenly the frames are spaced; `None` for a one-frame video.
    pub cadence: Option<Cadence>,
    /// The rate the stream declares; `None` when it declares none.
    pub frame_rate: Option<FrameRate>,
}

/// The path errors about `video` name: its file, or the pattern of its images.
pub fn video_location(video: &VideoRef) -> HostPath {
    match video {
        VideoRef::File(path) => HostPath::new(path),
        VideoRef::ImageSequence(sequence) => HostPath::new(&sequence.pattern),
    }
}

/// The identity of `video` for the registry: the canonical path of a file, or of a sequence's folder with the rest
/// of what makes the frames: the file name pattern, the first number, the frame rate and the count.
fn source_key(video: &VideoRef) -> Result<SourceKey, UserError> {
    let canonical = |path: &Path| {
        std::fs::canonicalize(path).map_err(|error| {
            let code = match error.kind() {
                std::io::ErrorKind::NotFound => ErrorCode::FileNotFound,
                _ => ErrorCode::FileUnreadable,
            };
            UserError::new(code, format!("Cannot open the video: {error}")).at(HostPath::new(path))
        })
    };
    let identity = match video {
        VideoRef::File(path) => canonical(path)?.display().to_string(),
        VideoRef::ImageSequence(sequence) => {
            let folder = sequence.pattern.parent().unwrap_or(Path::new("."));
            let name = sequence.pattern.file_name().unwrap_or_default();
            format!(
                "{} (from {}, {}/{} fps, {} frames)",
                canonical(folder)?.join(name).display(),
                sequence.start_number,
                sequence.frame_rate.num(),
                sequence.frame_rate.den(),
                sequence.frame_count,
            )
        }
    };
    Ok(SourceKey {
        identity,
        location: video_location(video),
    })
}

/// What the media crate opens `video` as.
pub(crate) fn input_of(video: &VideoRef) -> VideoInput {
    match video {
        VideoRef::File(path) => VideoInput::File(path.clone()),
        VideoRef::ImageSequence(sequence) => VideoInput::ImageSequence(ImageSequenceInput {
            pattern: sequence.pattern.clone(),
            start_number: sequence.start_number,
            frame_rate: sequence.frame_rate,
            frame_count: sequence.frame_count,
        }),
    }
}

/// Opens viewers on videos over one playback registry.
pub struct VideoSources {
    registry: Registry,
    /// The cache folder; `media` keeps the frame indexes in a folder of their own under it.
    cache_dir: PathBuf,
    /// The details of each open source, held weakly by what the source itself keeps (its decoder factory), so an
    /// entry is found exactly as long as its source is open and never outlives it.
    details: Mutex<HashMap<SourceKey, Weak<Details>>>,
}

impl VideoSources {
    /// Sources in `registry`, with their frame indexes cached under `cache_dir`.
    pub fn new(registry: Registry, cache_dir: &Path) -> Self {
        Self {
            registry,
            cache_dir: cache_dir.to_path_buf(),
            details: Mutex::new(HashMap::new()),
        }
    }

    /// The sources, for the cache allowance and the shutdown wait.
    pub fn registry(&self) -> &Registry {
        &self.registry
    }

    fn live_details(&self, key: &SourceKey) -> Option<Arc<Details>> {
        lock(&self.details).get(key)?.upgrade()
    }

    /// The input and frame index of `video` as the viewers of its open source hold them, for an export to read the
    /// frames on a decoder of its own; `None` when nobody has it open.
    pub fn open_input(&self, video: &VideoRef) -> Option<(VideoInput, Arc<FrameIndex>)> {
        let details = self.live_details(&source_key(video).ok()?)?;
        Some((details.input.clone(), Arc::clone(&details.index)))
    }

    /// A viewer of `video`: one on the source that is already open, or else a new source once the frame index is
    /// read, which for a file is built (reporting `progress`, 0 to 1) when it is not cached; an image sequence has
    /// nothing to build. What the viewer is told about the video comes from the source it joined. Blocks: call it
    /// on a blocking thread. Cancelling `cancel` ends an index build early.
    pub fn open(
        &self,
        video: &VideoRef,
        cancel: &CancelToken,
        progress: &mut dyn FnMut(f32),
    ) -> Result<OpenedVideo, Interrupt<UserError>> {
        let key = source_key(video).map_err(Interrupt::Failed)?;
        if let Some(session) = self.registry.attach(&key)
            && let Some(details) = self.live_details(&key)
        {
            return Ok(OpenedVideo {
                session,
                size: details.size,
                warnings: details.warnings.clone(),
                pts: details.index.pts_timing(),
                cadence: details.index.cadence().cloned(),
                frame_rate: details.index.info().frame_rate,
            });
        }
        let input = input_of(video);
        let index = Arc::new(FrameIndex::load_or_build(
            &input,
            Some(&self.cache_dir),
            cancel,
            progress,
        )?);
        let info = index.info();
        let frame_rate = info.frame_rate;
        let size = FrameSize {
            width: info.width,
            height: info.height,
        };
        let mut warnings = index.warnings().to_vec();
        if let VideoRef::ImageSequence(sequence) = video
            && (sequence.width, sequence.height) != (0, 0)
            && (sequence.width, sequence.height) != (size.width, size.height)
        {
            warnings.push(format!(
                "The images are {}x{} but seqinfo.ini says {}x{}; boxes follow the images",
                size.width, size.height, sequence.width, sequence.height
            ));
        }
        let details = Arc::new(Details {
            size,
            warnings,
            input: input.clone(),
            index: Arc::clone(&index),
        });
        let spec = SourceSpec {
            timeline: index.timeline().clone(),
            keyframes: index.keyframes().clone(),
            frame_bytes: info.frame_bytes(),
        };
        let factory = decoder_factory(input, index, Arc::clone(&details));
        let session = self
            .registry
            .open(&key, spec, factory)
            .map_err(Interrupt::Failed)?;
        let mut open = lock(&self.details);
        open.retain(|_, weak| weak.strong_count() > 0);
        let joined_other = open.get(&key).is_some_and(|weak| weak.strong_count() > 0);
        if !joined_other {
            open.insert(key, Arc::downgrade(&details));
        }
        drop(open);
        Ok(OpenedVideo {
            session,
            size: details.size,
            warnings: details.warnings.clone(),
            pts: details.index.pts_timing(),
            cadence: details.index.cadence().cloned(),
            frame_rate,
        })
    }
}

/// Locks `mutex`, ignoring poisoning: the maps behind it stay valid when a holder panics.
fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::FrameRate;
    use playback::{CachePool, DECODE_WORKERS};
    use workspace::ImageSequence;

    fn sources(cache: &Path) -> VideoSources {
        VideoSources::new(Registry::new(CachePool::new(64 << 20)), cache)
    }

    fn fixture_sequence(width: u32, height: u32) -> VideoRef {
        VideoRef::ImageSequence(ImageSequence {
            pattern: Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/video/seq/%06d.jpg"),
            start_number: 1,
            frame_rate: FrameRate::parse("30000/1001").expect("a rate"),
            width,
            height,
            frame_count: 12,
        })
    }

    fn open(sources: &VideoSources, video: &VideoRef) -> OpenedVideo {
        sources
            .open(video, &CancelToken::new(), &mut |_| {})
            .unwrap_or_else(|e| panic!("did not open: {e:?}"))
    }

    #[test]
    fn viewers_of_one_image_sequence_share_a_source_and_are_told_the_same_about_it() {
        let cache = tempfile::tempdir().expect("a cache folder");
        let sources = sources(cache.path());
        // Declared 640x480, decoded 64x48: the viewer is told, and the boxes follow the images.
        let video = fixture_sequence(640, 480);

        let first = open(&sources, &video);
        let second = open(&sources, &video);

        assert_eq!(sources.registry().running_workers(), DECODE_WORKERS);
        for opened in [&first, &second] {
            assert_eq!((opened.size.width, opened.size.height), (64, 48));
            assert_eq!(opened.session.spec().timeline.len(), 12);
            let [warning] = opened.warnings.as_slice() else {
                panic!("one warning, got {:?}", opened.warnings);
            };
            assert!(
                warning.contains("64x48") && warning.contains("640x480"),
                "{warning}"
            );
        }
        assert!(
            !cache.path().join("frame-index").exists(),
            "a sequence has no cache file"
        );
    }

    #[test]
    fn a_sequence_declared_at_its_real_size_has_no_warning() {
        let cache = tempfile::tempdir().expect("a cache folder");
        let opened = open(&sources(cache.path()), &fixture_sequence(64, 48));
        assert!(opened.warnings.is_empty(), "{:?}", opened.warnings);
    }
}
