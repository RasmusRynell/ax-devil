//! A viewer's lanes: what the entry shows, how a lane selects samples, where its load stands and the data it sends,
//! and their wire forms. A policy that cannot be applied, and a lane's problems, are told only to an audience with
//! host details; the lane keeps its last matching, adopts the new revision and the session goes on.

use super::{End, Flow, Outgoing, stream::Setup, text};
use api_types::{
    Audience,
    playback::{
        AlignmentVerdictWire, AlignmentWire, FilterOptionInfo, IngestWire, LaneDefaultsWire,
        LaneInfo, LanePolicyWire, LaneStatusWire, MAX_DATA_WINDOW_FRAMES, ServerMessage,
        StickyWire, WarningInfo, envelope,
    },
    project,
};
use axum::extract::ws::Message;
use catalogs::Library;
use foundation::{ErrorCode, HostPath, MediaTime, Timeline, UserError};
use overlays::{LaneStatus, OverlayEvent, ViewerOverlays};
use playback::{Rank, Want};
use scene::matching::{MatchPolicy, Sticky, Tolerance, Verdict};
use scene_store::Ingest;
use std::collections::{BTreeMap, BTreeSet};
use workspace::Entry;

/// How long a kept sample may stay, at most, in milliseconds.
const MAX_STICKY_TIMEOUT_MS: u32 = 100_000;

/// What a viewer's lanes have to send.
pub(super) enum LaneEvent {
    /// A lane policy the viewer sent that cannot be applied.
    Problem(u16, String),
    /// What the overlays reported.
    Overlay(OverlayEvent),
    /// The overlays closed on their own, which leaves the video playing without them.
    Ended,
    /// A lane whose overlays ended before it failed or closed.
    Abandoned(u16),
}

/// The lanes of one viewer's entry and the overlays that draw them.
pub(super) struct Lanes {
    overlays: ViewerOverlays,
    /// Whether the overlays still report.
    live: bool,
    count: usize,
    /// The lanes with an overlay that has not failed.
    working: BTreeSet<u16>,
    /// Lanes the overlays left without a terminal status when they ended, each still to be told it failed.
    abandoned: Vec<u16>,
    /// The newest unsent complaint about each lane's policy; a lane's next policy replaces it.
    problems: BTreeMap<u16, String>,
    /// The data window the overlays were last given.
    window: Option<Want>,
    audience: Audience,
    /// The video's file, which a lane whose overlays stopped is told about.
    location: HostPath,
}

impl Lanes {
    /// The viewer's overlays, which answer inspections.
    pub(super) fn overlays(&self) -> &ViewerOverlays {
        &self.overlays
    }

    /// Starts loading the overlays of `entry`, one lane each, or the one plain lane of an entry without overlays.
    /// Nothing is sent until the lanes [`follow`](Self::follow) a window and a lane subscribes.
    pub(super) fn open(setup: &Setup, timeline: Timeline, location: HostPath) -> Self {
        let Setup {
            entry,
            data_bytes,
            audience,
            overlays,
            admission,
            ..
        } = *setup;
        let sources: Vec<_> = if entry.overlays.is_empty() {
            vec![None]
        } else {
            entry.overlays.iter().cloned().map(Some).collect()
        };
        let working = (0..sources.len())
            .zip(&sources)
            .filter(|(_, source)| source.is_some())
            .filter_map(|(lane, _)| u16::try_from(lane).ok())
            .collect();
        let count = sources.len();
        Self {
            overlays: overlays.open_viewer(sources, timeline, data_bytes, admission),
            live: true,
            count,
            working,
            abandoned: Vec::new(),
            problems: BTreeMap::new(),
            window: None,
            audience,
            location,
        }
    }

    /// Gives the overlays the data window inside `applied`, the window the viewer's authority allowed its video, when
    /// it is new: the viewer's data frames, bounded by what the video window allows and by the largest data window.
    /// The overlays never select or send anything for a frame the viewer may not have.
    pub(super) fn follow(&mut self, applied: Option<Want>, data: (u32, u32)) {
        let Some(applied) = applied else {
            return;
        };
        let window = data_window(applied, data);
        if self.window != Some(window) {
            self.window = Some(window);
            self.overlays.want(window);
        }
    }

    /// Takes back data the writer dropped, which the overlays send again while the viewer's window still needs it.
    pub(super) fn undelivered(&self, event: &OverlayEvent) {
        self.overlays.undelivered(event);
    }

    /// Gives a lane the policy the viewer asked for. One that cannot be applied is reported as a `LaneProblem`; the
    /// lane keeps its matching but adopts `revision` and `subscribed`, so the maps it sends carry the revision the
    /// viewer waits for. A plain lane ignores it; a lane the entry does not have ends the session.
    pub(super) fn policy(
        &mut self,
        lane: u16,
        revision: u32,
        policy: LanePolicyWire,
        subscribed: bool,
    ) -> Flow<()> {
        if usize::from(lane) >= self.count {
            return Err(End::Failed(UserError::new(
                ErrorCode::ProtocolViolation,
                format!("The video has no lane {lane}"),
            )));
        }
        if self.overlays.lane_sources()[usize::from(lane)].is_none() {
            return Ok(());
        }
        let matching = match policy_from_wire(policy) {
            Ok(matching) => {
                self.problems.remove(&lane);
                Some(matching)
            }
            Err(reason) => {
                self.problems.insert(lane, reason);
                None
            }
        };
        self.overlays
            .set_policy(lane, revision, matching, subscribed);
        Ok(())
    }

    /// The next thing the lanes have to send: complaints first, then what the overlays report, which is
    /// nearest the playhead and goes only while its rank is at most `frame_rank`, the rank of the video frame
    /// waiting. Waits while there is nothing.
    pub(super) async fn next(&mut self, frame_rank: Option<Rank>) -> LaneEvent {
        if let Some((lane, reason)) = self.problems.pop_first() {
            return LaneEvent::Problem(lane, reason);
        }
        if let Some(lane) = self.abandoned.pop() {
            return LaneEvent::Abandoned(lane);
        }
        if !self.live {
            return std::future::pending().await;
        }
        match self.overlays.next_event(frame_rank).await {
            Some(event) => LaneEvent::Overlay(event),
            None => LaneEvent::Ended,
        }
    }

    /// The message for `event`, if the viewer is to be told. A sample names the frame it ranks by and a map run the
    /// frames it covers, so the writer drops it like a frame once the viewer's authority no longer reaches all of
    /// them.
    pub(super) fn encode(&mut self, event: LaneEvent) -> Option<Outgoing> {
        let host_details = self.audience.host_details;
        let problem = |lane, reason| {
            host_details.then(|| text(&ServerMessage::LaneProblem { lane, reason }).into())
        };
        match event {
            LaneEvent::Problem(lane, reason)
            | LaneEvent::Overlay(OverlayEvent::Problem { lane, reason }) => problem(lane, reason),
            LaneEvent::Overlay(OverlayEvent::Status { lane, status }) => {
                if matches!(status, LaneStatus::Failed(_)) {
                    self.working.remove(&lane);
                }
                Some(self.status(lane, status))
            }
            LaneEvent::Overlay(OverlayEvent::Sample(sample)) => Some(Outgoing {
                inspection: None,
                message: Message::Binary(envelope::sample(&sample)),
                frames: Some(sample.frame..=sample.frame),
                data: Some(OverlayEvent::Sample(sample)),
            }),
            LaneEvent::Overlay(OverlayEvent::Map(run)) => Some(Outgoing {
                inspection: None,
                message: Message::Binary(envelope::frame_map(&run)),
                frames: Some(run.frames()),
                data: Some(OverlayEvent::Map(run)),
            }),
            // Control messages are never gated: they name nothing the authority did not allow when it was chosen.
            LaneEvent::Overlay(OverlayEvent::Window { first, last }) => {
                Some(text(&ServerMessage::DataWindow { first, last }).into())
            }
            LaneEvent::Overlay(OverlayEvent::Evict {
                source,
                data_revision,
                samples,
            }) => Some(
                text(&ServerMessage::Evict {
                    source,
                    data_revision,
                    samples,
                })
                .into(),
            ),
            LaneEvent::Ended => {
                tracing::warn!(
                    "The overlays of {} stopped; the video goes on",
                    self.location.as_path().display()
                );
                self.live = false;
                // The viewer waits for every lane that is not failed, so none may be left waiting.
                self.abandoned = std::mem::take(&mut self.working).into_iter().collect();
                None
            }
            LaneEvent::Abandoned(lane) => {
                let error =
                    UserError::new(ErrorCode::Internal, "The overlays stopped unexpectedly")
                        .at(self.location.clone());
                Some(self.status(lane, LaneStatus::Failed(error)))
            }
        }
    }

    fn status(&self, lane: u16, status: LaneStatus) -> Outgoing {
        text(&ServerMessage::LaneStatus {
            lane,
            status: status_wire(status, self.audience),
        })
        .into()
    }

    /// The lanes the entry shows: one per overlay, or one plain lane.
    pub(super) fn infos(
        &self,
        entry: &Entry,
        matching: &BTreeMap<String, MatchPolicy>,
    ) -> Vec<LaneInfo> {
        entry
            .lane_labels()
            .into_iter()
            .zip(entry.lane_slots())
            .zip(self.overlays.lane_sources())
            .map(|((label, slot), source)| LaneInfo {
                label: label.to_owned(),
                slot: slot.to_owned(),
                source: *source,
                policy: matching.get(slot).map(policy_wire),
            })
            .collect()
    }
}

/// What every lane starts with: the library's default catalog and the default matching.
pub(super) fn lane_defaults(catalogs: &Library) -> LaneDefaultsWire {
    LaneDefaultsWire {
        catalog: catalogs.default_id().to_string(),
        policy: policy_wire(&MatchPolicy::default()),
    }
}

/// The data window of a viewer: `data` frames ahead and behind the playhead of `applied`, no more than it keeps frames
/// and no more than [`MAX_DATA_WINDOW_FRAMES`] in all.
fn data_window(applied: Want, data: (u32, u32)) -> Want {
    let ahead = data.0.min(applied.ahead).min(MAX_DATA_WINDOW_FRAMES - 1);
    let behind = data
        .1
        .min(applied.behind)
        .min(MAX_DATA_WINDOW_FRAMES - 1 - ahead);
    Want {
        ahead,
        behind,
        ..applied
    }
}

fn millis(time: MediaTime) -> u32 {
    u32::try_from(time.as_us() / 1000).unwrap_or(u32::MAX)
}

fn tolerance_ms(tolerance: Tolerance) -> Option<u32> {
    match tolerance {
        Tolerance::Exact => None,
        Tolerance::PreviousWithin(time) => Some(millis(time)),
    }
}

/// The wire form of a lane's matching.
pub(super) fn policy_wire(policy: &MatchPolicy) -> LanePolicyWire {
    LanePolicyWire {
        basis: policy.basis,
        tolerance_ms: tolerance_ms(policy.tolerance),
        sticky: StickyWire {
            enabled: policy.sticky.enabled,
            timeout_ms: policy.sticky.timeout.map(millis),
        },
    }
}

/// The matching a viewer asked for, or why it cannot be applied, in words for the user.
pub(crate) fn policy_from_wire(wire: LanePolicyWire) -> Result<MatchPolicy, String> {
    let timeout = match wire.sticky.timeout_ms {
        Some(ms) if ms > MAX_STICKY_TIMEOUT_MS => {
            return Err(format!(
                "A kept overlay can stay at most {} seconds",
                MAX_STICKY_TIMEOUT_MS / 1000
            ));
        }
        Some(ms) => Some(MediaTime::from_us(i64::from(ms) * 1000)),
        None => None,
    };
    Ok(MatchPolicy {
        basis: wire.basis,
        tolerance: match wire.tolerance_ms {
            None => Tolerance::Exact,
            Some(ms) => Tolerance::PreviousWithin(MediaTime::from_us(i64::from(ms) * 1000)),
        },
        sticky: Sticky {
            enabled: wire.sticky.enabled,
            timeout,
        },
    })
}

/// The wire form of a lane's load status for `audience`: the decoder's warnings name the host's files and lines, so
/// only an audience with host details hears them.
pub(super) fn status_wire(status: LaneStatus, audience: Audience) -> LaneStatusWire {
    match status {
        LaneStatus::Loading => LaneStatusWire::Loading,
        LaneStatus::Ready(ready) => LaneStatusWire::Ready {
            bases: ready.bases.clone(),
            default_basis: ready.default_basis,
            filter: ready
                .filter
                .options
                .iter()
                .map(|option| FilterOptionInfo {
                    id: option.id.to_string(),
                    label: option.label.to_string(),
                    default_enabled: option.default_enabled,
                })
                .collect(),
            warnings: ready
                .warnings
                .into_iter()
                .filter(|_| audience.host_details)
                .map(WarningInfo::from)
                .collect(),
            alignment: ready.alignment.map(|alignment| AlignmentWire {
                basis: alignment.basis,
                tolerance_ms: tolerance_ms(alignment.tolerance),
                samples: alignment.samples as u64,
                matched: alignment.matched as u64,
                exact: alignment.exact as u64,
                end_gap_us: alignment.end_gap.map(MediaTime::as_us),
                ends_far: alignment.ends_far(),
                verdict: match alignment.verdict() {
                    Verdict::Weak => AlignmentVerdictWire::Weak,
                    Verdict::EndsFar => AlignmentVerdictWire::EndsFar,
                    Verdict::Partial => AlignmentVerdictWire::Partial,
                    Verdict::Good => AlignmentVerdictWire::Good,
                },
            }),
            samples: ready.samples as u64,
            data_revision: ready.data_revision,
            mean_sample_bytes: ready.mean_sample_bytes,
            ingest: match ready.ingest {
                Ingest::Reading => IngestWire::Reading,
                Ingest::Complete => IngestWire::Complete,
                Ingest::Incomplete(error) => IngestWire::Incomplete(project(&error, audience)),
            },
        },
        LaneStatus::Failed(error) => LaneStatusWire::Failed(project(&error, audience)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use scene::MatchBasis;

    fn wire() -> LanePolicyWire {
        LanePolicyWire {
            basis: Some(MatchBasis::Time),
            tolerance_ms: Some(40),
            sticky: StickyWire {
                enabled: true,
                timeout_ms: Some(2050),
            },
        }
    }

    #[test]
    fn a_policy_survives_the_trip_and_what_cannot_be_applied_says_why() {
        let policy = policy_from_wire(wire()).unwrap();
        assert_eq!(policy_wire(&policy), wire());

        let mut unlimited = wire();
        unlimited.tolerance_ms = None;
        unlimited.sticky.timeout_ms = None;
        assert_eq!(
            policy_wire(&policy_from_wire(unlimited).unwrap()),
            unlimited
        );

        let mut too_long = wire();
        too_long.sticky.timeout_ms = Some(MAX_STICKY_TIMEOUT_MS + 1);
        assert!(
            policy_from_wire(too_long)
                .unwrap_err()
                .contains("100 seconds")
        );
    }

    #[test]
    fn the_data_window_is_bounded_by_the_video_window_and_the_largest_data_window() {
        let applied = |ahead, behind| Want {
            playhead: 5,
            ahead,
            behind,
            direction: playback::Direction::Forward,
            priority: playback::Priority::default(),
        };
        let window = |applied: Want, data| {
            let window = data_window(applied, data);
            (window.ahead, window.behind)
        };

        assert_eq!(window(applied(10, 10), (4, 4)), (4, 4));
        assert_eq!(window(applied(3, 2), (4, 4)), (3, 2));
        assert_eq!(
            window(applied(u32::MAX, u32::MAX), (u32::MAX, u32::MAX)),
            (MAX_DATA_WINDOW_FRAMES - 1, 0)
        );
    }
}
