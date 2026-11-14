//! Placing a file's samples on one video's frames: which frame shows which sample under the lane's matching, so a run of
//! samples becomes the frames it is on screen.

use foundation::{CancelToken, Cancelled, Timeline};
use scene::Coverage;
use scene::matching::{MatchPolicy, SampleKeys, Selection, select};

/// How many frames between two checks of the cancel token.
const CANCEL_EVERY: u32 = 4096;

/// What every frame of a video shows of one overlay file, as the Overlay Lookup selects it. Eight bytes per frame.
///
/// Frames are `u32`: a video past `u32::MAX` frames is placed up to that frame.
pub struct Placement {
    frames: u32,
    /// The frames that show a sample, ascending, and the sample each shows. Selection never shows an older sample on
    /// a later frame, so `shown_samples` ascends too.
    shown_frames: Vec<u32>,
    shown_samples: Vec<u32>,
    /// The frames that show nothing: no sample, or a dense file's empty frame.
    blank_frames: Vec<u32>,
}

impl Placement {
    /// About how much memory the placement holds, in bytes.
    pub fn bytes(&self) -> usize {
        size_of_val(self.shown_frames.as_slice())
            + size_of_val(self.shown_samples.as_slice())
            + size_of_val(self.blank_frames.as_slice())
    }

    /// Selects the sample of every frame of `timeline`. Cancellation is checked as it goes.
    pub fn new(
        keys: &SampleKeys,
        coverage: Coverage,
        timeline: &Timeline,
        policy: &MatchPolicy,
        cancel: &CancelToken,
    ) -> Result<Self, Cancelled> {
        let frames = u32::try_from(timeline.len()).unwrap_or(u32::MAX);
        let mut placement = Self {
            frames,
            shown_frames: Vec::new(),
            shown_samples: Vec::new(),
            blank_frames: Vec::new(),
        };
        for frame in 0..frames {
            if frame % CANCEL_EVERY == 0 {
                cancel.check()?;
            }
            let Some(id) = timeline.frame(u64::from(frame)) else {
                break;
            };
            match select(keys, coverage, id, timeline, policy) {
                Selection::Sample { index, .. } => {
                    placement.shown_frames.push(frame);
                    placement.shown_samples.push(index as u32);
                }
                Selection::None | Selection::Empty => placement.blank_frames.push(frame),
            }
        }
        Ok(placement)
    }

    /// The frames showing the samples of `runs` (first and last sample of each), as spans of consecutive frames
    /// `[first, last]`. A run is the frames from the first that shows one of its samples to the last, less the blank
    /// frames inside; spans that touch are one.
    pub(crate) fn spans(&self, runs: &[[u32; 2]]) -> Vec<[u32; 2]> {
        let mut spans: Vec<[u32; 2]> = Vec::new();
        for &run in runs {
            let Some((first, last)) = self.frames_of(run) else {
                continue;
            };
            let from = self.blank_frames.partition_point(|&blank| blank <= first);
            let to = self.blank_frames.partition_point(|&blank| blank < last);
            let mut start = first;
            for &blank in &self.blank_frames[from..to] {
                if blank > start {
                    push_span(&mut spans, [start, blank - 1]);
                }
                start = blank + 1;
            }
            push_span(&mut spans, [start, last]);
        }
        spans
    }

    /// The first and last frame showing any sample of `runs`, or `None` when no frame shows one.
    pub(crate) fn bounds(&self, runs: &[[u32; 2]]) -> Option<(u32, u32)> {
        let first = runs.iter().find_map(|&run| self.frames_of(run))?.0;
        let last = runs.iter().rev().find_map(|&run| self.frames_of(run))?.1;
        Some((first, last))
    }

    /// The first and last frame that shows a sample from `run`, if any.
    fn frames_of(&self, [first, last]: [u32; 2]) -> Option<(u32, u32)> {
        let start = self.shown_samples.partition_point(|&sample| sample < first);
        let stop = self.shown_samples.partition_point(|&sample| sample <= last);
        (start < stop).then(|| (self.shown_frames[start], self.shown_frames[stop - 1]))
    }

    /// The frames of `spans` for a strip of at most `bins` marks: gaps shorter than `frames / bins` are closed, so
    /// there are at most `bins` spans. `bins == 0` gives none.
    pub(crate) fn presence(&self, spans: &[[u32; 2]], bins: u16) -> Vec<[u64; 2]> {
        if bins == 0 {
            return Vec::new();
        }
        let gap = u64::from(self.frames) / u64::from(bins);
        let mut marks: Vec<[u64; 2]> = Vec::new();
        for &[first, last] in spans {
            let (first, last) = (u64::from(first), u64::from(last));
            match marks.last_mut() {
                Some(mark) if first - mark[1] - 1 < gap => mark[1] = last,
                _ => marks.push([first, last]),
            }
        }
        marks
    }
}

/// Adds `span` after `spans`, joined to the last one when they touch.
fn push_span(spans: &mut Vec<[u32; 2]>, span: [u32; 2]) {
    match spans.last_mut() {
        Some(last) if last[1] + 1 == span[0] => last[1] = span[1],
        _ => spans.push(span),
    }
}
