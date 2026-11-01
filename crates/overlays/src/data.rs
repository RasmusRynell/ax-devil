//! The windows of a viewer: which frames' maps and which samples its data window holds, and when they change.
//!
//! The data window is the playhead with `ahead` and `behind` frames of the viewer's `want`, clamped to the video.
//! Every change of the want, of a lane's policy or of a load recomputes the lane maps of the window (one
//! [`overlay_sync::select`] per new frame) and the sample window of each source ([`crate::sample_window`]), then
//! forgets what left them, so it is sent again when it returns.

use std::{collections::BTreeMap, ops::RangeInclusive};

use foundation::Timeline;
use overlay_sync::{MatchKind, Selection, select};
use playback::Want;

use crate::{
    sample_window,
    state::{Load, State},
    window::{FIRST_NON_SAMPLE, NONE_CELL},
};

/// A lane's map of every frame of the data window: the sample of each frame and how it matched.
#[derive(Clone, Debug, Default)]
pub(crate) struct LaneWindow {
    first: u64,
    pub(crate) samples: Vec<u32>,
    pub(crate) kinds: Vec<u8>,
}

impl LaneWindow {
    pub(crate) fn is_empty(&self) -> bool {
        self.samples.is_empty()
    }

    /// The cell of `frame`, if the window holds it.
    fn cell(&self, frame: u64) -> Option<(u32, u8)> {
        let at = usize::try_from(frame.checked_sub(self.first)?).ok()?;
        Some((*self.samples.get(at)?, self.kinds[at]))
    }

    /// The map of `range`, keeping the cells this window already has and asking `compute` for the others.
    fn moved_to(
        &self,
        range: &RangeInclusive<u64>,
        mut compute: impl FnMut(u64) -> (u32, u8),
    ) -> Self {
        let mut moved = Self {
            first: *range.start(),
            samples: Vec::new(),
            kinds: Vec::new(),
        };
        for frame in range.clone() {
            let (sample, kind) = self.cell(frame).unwrap_or_else(|| compute(frame));
            moved.samples.push(sample);
            moved.kinds.push(kind);
        }
        moved
    }

    /// The cells of the frames `run` as parallel columns.
    pub(crate) fn slice(&self, run: &RangeInclusive<u64>) -> (Vec<u32>, Vec<u8>) {
        let from = usize::try_from(run.start() - self.first).unwrap_or(0);
        let to = usize::try_from(run.end() - self.first).unwrap_or(0);
        (
            self.samples[from..=to].to_vec(),
            self.kinds[from..=to].to_vec(),
        )
    }
}

/// The `MATCH_*` byte of the playback wire.
pub(crate) fn kind_byte(kind: MatchKind) -> u8 {
    match kind {
        MatchKind::Exact => 0,
        MatchKind::Tolerated => 1,
        MatchKind::Retained => 2,
    }
}

impl State {
    /// The want with its playhead inside the video, and the frames it keeps.
    pub(crate) fn window(&self) -> Option<(Want, RangeInclusive<u64>)> {
        let want = Want {
            playhead: self.want?.playhead.min(self.last_frame),
            ..self.want?
        };
        let low = want.playhead.saturating_sub(u64::from(want.behind));
        let high = want
            .playhead
            .saturating_add(u64::from(want.ahead))
            .min(self.last_frame);
        Some((want, low..=high))
    }

    /// Applies the data window: maps and samples that left it are forgotten, so they are sent again when they
    /// return.
    pub(crate) fn set_want(&mut self, want: Want, timeline: &Timeline) {
        self.want = Some(want);
        let Some((_, window)) = self.window() else {
            return;
        };
        for lane in &mut self.lanes {
            lane.maps_sent.retain_within(&window);
        }
        self.refresh(timeline);
    }

    /// Replaces a lane's policy under a new revision: its maps are selected and sent again, and so are the samples
    /// its old map named, because the viewer keeps only the samples the maps it holds name and holds none of the
    /// new revision yet. A plain lane, or one past the last lane, is ignored.
    pub(crate) fn set_policy(
        &mut self,
        lane: usize,
        revision: u32,
        matching: Option<overlay_sync::MatchPolicy>,
        subscribed: bool,
        timeline: &Timeline,
    ) {
        let Some(slot) = self.lanes.get_mut(lane).filter(|l| l.source.is_some()) else {
            return;
        };
        slot.revision = revision;
        slot.policy.subscribed = subscribed;
        if let Some(matching) = matching {
            slot.policy.matching = matching;
        }
        slot.maps_sent.clear();
        if let Some(source) = slot.source {
            // The viewer forgets this lane's map and with it the samples only that map named: they are sent again.
            let source = &mut self.sources[source];
            for &cell in slot
                .window
                .samples
                .iter()
                .filter(|&&cell| cell < FIRST_NON_SAMPLE)
            {
                source.unclaim(u64::from(cell));
            }
        }
        slot.window = LaneWindow::default();
        self.refresh(timeline);
    }

    /// Recomputes the lane maps and sample windows for the current want and forgets the samples that left theirs.
    pub(crate) fn refresh(&mut self, timeline: &Timeline) {
        let Some((want, range)) = self.window() else {
            return;
        };
        for lane in &mut self.lanes {
            let track = lane
                .source
                .and_then(|source| match &self.sources[source].load {
                    Load::Ready(track) if lane.policy.subscribed => Some(track),
                    _ => None,
                });
            lane.window = match track {
                Some(track) => lane.window.moved_to(&range, |frame| {
                    let Some(id) = timeline.frame(frame) else {
                        return (NONE_CELL, 0);
                    };
                    match select(
                        track.keys(),
                        track.coverage(),
                        id,
                        timeline,
                        &lane.policy.matching,
                    ) {
                        Selection::None => (NONE_CELL, 0),
                        Selection::Empty => (FIRST_NON_SAMPLE, 0),
                        Selection::Sample { index, kind } => {
                            (u32::try_from(index).unwrap_or(NONE_CELL), kind_byte(kind))
                        }
                    }
                }),
                None => LaneWindow::default(),
            };
        }
        for (index, source) in self.sources.iter_mut().enumerate() {
            let maps: Vec<&[u32]> = self
                .lanes
                .iter()
                .filter(|lane| lane.source == Some(index) && !lane.window.is_empty())
                .map(|lane| lane.window.samples.as_slice())
                .collect();
            source.window = sample_window(&maps, *range.start(), want.playhead, want.direction)
                .into_iter()
                .collect::<BTreeMap<_, _>>();
            source
                .claimed
                .retain(|sample| source.window.contains_key(sample));
            source
                .ready
                .retain(|(sample, _)| source.window.contains_key(sample));
        }
    }
}
