//! The keys of the samples written so far: the published ones in shared chunks, the rest waiting for a publication.

use foundation::MediaTime;
use scene::matching::{FrameKey, KeyChunks, SampleKeys};
use scene::{SceneTime, TimeKind};

use crate::format::Keys;

/// Keys of one kind: those readers can see and those still pending.
struct Log<T> {
    published: KeyChunks<T>,
    pending: Vec<T>,
}

impl<T: Copy> Log<T> {
    fn new() -> Self {
        Self {
            published: KeyChunks::new(),
            pending: Vec::new(),
        }
    }

    fn publish(&mut self) -> KeyChunks<T> {
        self.published = self.published.appended(&self.pending);
        self.pending.clear();
        self.published.clone()
    }

    fn all(&self) -> impl Iterator<Item = T> + '_ {
        self.published.iter().chain(self.pending.iter().copied())
    }
}

/// The keys of every sample, by frame number and by timestamp. A frame-keyed file has the first. A timestamp-keyed
/// one has the second and, when its decoder can be matched by frame, the first as well: each sample is found by the
/// position in the file of the record it came from, however the samples are ordered by time.
pub(crate) struct KeyLog {
    frames: Option<Log<FrameKey>>,
    times: Option<Log<MediaTime>>,
    /// How many samples have been added; the index of the next.
    samples: u32,
}

impl KeyLog {
    /// An empty log for a file keyed on `kind`'s clock. `by_position` gives a timestamp-keyed file frame numbers.
    pub(crate) fn new(kind: TimeKind, by_position: bool) -> Self {
        let (frames, times) = match kind {
            TimeKind::Frame => (Some(Log::new()), None),
            TimeKind::Timestamp => (by_position.then(Log::new), Some(Log::new())),
        };
        Self {
            frames,
            times,
            samples: 0,
        }
    }

    /// Adds the next sample, which must be on the file's clock and above the last in time. `ordinal` is the position
    /// in the file of the record it came from; a frame-keyed file does not use it.
    pub(crate) fn push(&mut self, time: SceneTime, ordinal: u64) {
        let sample = self.samples;
        self.samples += 1;
        match time {
            SceneTime::Frame(frame) => {
                if let Some(frames) = &mut self.frames {
                    // A Scene before the video is never stored, so the frame is not negative here.
                    let frame = u64::try_from(frame).unwrap_or_default();
                    frames.pending.push(FrameKey { frame, sample });
                }
            }
            SceneTime::Timestamp(time) => {
                if let Some(frames) = &mut self.frames {
                    frames.pending.push(FrameKey {
                        frame: ordinal,
                        sample,
                    });
                }
                if let Some(times) = &mut self.times {
                    times.pending.push(time);
                }
            }
        }
    }

    /// Moves the pending keys into the published lists and returns them. Frame numbers follow file order, so within
    /// one publication they are sorted; across publications they only ascend while the samples arrive in time order,
    /// which is why a file that does not publishes once, with all of them.
    pub(crate) fn publish(&mut self) -> SampleKeys {
        if let Some(frames) = &mut self.frames {
            frames.pending.sort_unstable_by_key(|key| key.frame);
        }
        SampleKeys {
            frames: self.frames.as_mut().map(Log::publish),
            times: self.times.as_mut().map(Log::publish),
        }
    }

    /// Every key by sample, published and pending, for writing the index: the frame number of sample `i` is at
    /// index `i`.
    pub(crate) fn all(&self) -> Keys {
        Keys {
            frames: self.frames.as_ref().map(|frames| {
                let mut by_sample = vec![0; self.samples as usize];
                for key in frames.all() {
                    by_sample[key.sample as usize] = key.frame;
                }
                by_sample
            }),
            times: self
                .times
                .as_ref()
                .map(|times| times.all().map(|time| time.0).collect()),
        }
    }
}
