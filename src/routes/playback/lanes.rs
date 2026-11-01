//! A viewer's lanes: what the entry shows, how a lane is viewed, where its load stands and the overlays it sends,
//! and their wire forms. A lane view that cannot be applied, and a lane's problems, are told only to an audience with
//! host details; the lane keeps its last view and the session goes on.

use super::{End, Flow, Outgoing, overlay_envelope, text};
use api_types::{
    Audience,
    playback::{
        ClockWire, FilterOptionInfo, LaneInfo, LaneStatusWire, LaneViewWire, ServerMessage,
        StickyWire, TargetWire, WarningInfo,
    },
    project,
};
use axum::extract::ws::Message;
use catalogs::CatalogId;
use foundation::{ErrorCode, FrameSize, HostPath, MediaTime, Timeline, UserError};
use overlay_sync::{MatchPolicy, Sticky, Tolerance};
use overlays::{LaneStatus, LaneView, OverlayEvent, OverlayService, TargetSize, ViewerOverlays};
use playback::Want;
use scene::{FilterChoice, TimeKind};
use std::collections::{BTreeMap, BTreeSet};
use workspace::Entry;

/// How long a kept sample may stay, at most, in milliseconds.
const MAX_STICKY_TIMEOUT_MS: u32 = 100_000;

/// What a viewer's lanes have to send.
pub(super) enum LaneEvent {
    /// A lane view the viewer sent that cannot be applied.
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
    /// The newest unsent complaint about each lane's view; a lane's next view replaces it.
    problems: BTreeMap<u16, String>,
    /// The window the overlays were last given.
    window: Option<Want>,
    audience: Audience,
    /// The video's file, which a lane whose overlays stopped is told about.
    location: HostPath,
}

impl Lanes {
    /// Starts loading the overlays of `entry`, one lane each, or the one plain lane of an entry without overlays.
    /// Nothing is prepared until the lanes [`follow`](Self::follow) a window.
    pub(super) fn open(
        service: &OverlayService,
        entry: &Entry,
        timeline: Timeline,
        size: FrameSize,
        audience: Audience,
        location: HostPath,
    ) -> Self {
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
            overlays: service.open_viewer(sources, timeline, size),
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

    /// Gives the overlays `applied`, the window the viewer's authority allowed its video, when it is new. The
    /// overlays never prepare a frame the viewer may not have.
    pub(super) fn follow(&mut self, applied: Option<Want>) {
        if let Some(want) = applied
            && self.window != applied
        {
            self.window = applied;
            self.overlays.want(want);
        }
    }

    /// Gives a lane the view the viewer asked for. One that cannot be applied leaves the lane's last view and is
    /// reported as a `LaneProblem`; a lane the entry does not have ends the session.
    pub(super) fn view(&mut self, lane: u16, revision: u32, view: LaneViewWire) -> Flow<()> {
        if usize::from(lane) >= self.count {
            return Err(End::Failed(UserError::new(
                ErrorCode::ProtocolViolation,
                format!("The video has no lane {lane}"),
            )));
        }
        match view_from_wire(view) {
            Ok(view) => {
                self.problems.remove(&lane);
                self.overlays.set_view(lane, revision, view);
            }
            Err(reason) => {
                self.problems.insert(lane, reason);
            }
        }
        Ok(())
    }

    /// The next thing the lanes have to send: complaints about views first, then what the overlays report, which is
    /// nearest the playhead. Waits while there is nothing.
    pub(super) async fn next(&mut self) -> LaneEvent {
        if let Some((lane, reason)) = self.problems.pop_first() {
            return LaneEvent::Problem(lane, reason);
        }
        if let Some(lane) = self.abandoned.pop() {
            return LaneEvent::Abandoned(lane);
        }
        if !self.live {
            return std::future::pending().await;
        }
        match self.overlays.next_event().await {
            Some(event) => LaneEvent::Overlay(event),
            None => LaneEvent::Ended,
        }
    }

    /// The message for `event`, if the viewer is to be told. An overlay names its frame, so the gate drops it like
    /// the frame once the viewer's authority no longer reaches it.
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
            LaneEvent::Overlay(OverlayEvent::Overlay(overlay)) => Some(Outgoing {
                message: Message::Binary(overlay_envelope::overlay(&overlay)),
                frame: Some(overlay.frame.sequence_id),
            }),
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
}

/// The lanes an entry shows: one per overlay, or one plain lane.
pub(super) fn infos(entry: &Entry) -> Vec<LaneInfo> {
    if entry.overlays.is_empty() {
        return vec![LaneInfo {
            label: entry.label.clone(),
            has_overlay: false,
        }];
    }
    entry
        .overlays
        .iter()
        .map(|overlay| LaneInfo {
            label: overlay.label.clone(),
            has_overlay: true,
        })
        .collect()
}

fn millis(time: MediaTime) -> u32 {
    u32::try_from(time.as_us() / 1000).unwrap_or(u32::MAX)
}

/// The wire form of a lane's view.
pub(super) fn view_wire(view: &LaneView) -> LaneViewWire {
    LaneViewWire {
        catalog: view.catalog.to_string(),
        filter: view.filter.overrides.clone(),
        tolerance_ms: match view.policy.tolerance {
            Tolerance::Exact => None,
            Tolerance::PreviousWithin(time) => Some(millis(time)),
        },
        sticky: StickyWire {
            enabled: view.policy.sticky.enabled,
            timeout_ms: view.policy.sticky.timeout.map(millis),
            opacity: view.policy.sticky.opacity,
        },
        target: TargetWire {
            width: view.target.width,
            height: view.target.height,
        },
        visible: view.visible,
    }
}

/// The view a viewer asked for, or why it cannot be applied, in words for the user.
fn view_from_wire(wire: LaneViewWire) -> Result<LaneView, String> {
    let catalog = CatalogId::parse(&wire.catalog)
        .ok_or_else(|| format!("There is no catalog \"{}\"", wire.catalog))?;
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
    if !(0.0..=1.0).contains(&wire.sticky.opacity) {
        return Err("The opacity of a kept overlay is between 0 and 1".to_owned());
    }
    Ok(LaneView {
        catalog,
        filter: FilterChoice {
            overrides: wire.filter,
        },
        policy: MatchPolicy {
            tolerance: match wire.tolerance_ms {
                None => Tolerance::Exact,
                Some(ms) => Tolerance::PreviousWithin(MediaTime::from_us(i64::from(ms) * 1000)),
            },
            sticky: Sticky {
                enabled: wire.sticky.enabled,
                timeout,
                opacity: wire.sticky.opacity,
            },
        },
        // A lane that is not laid out yet reports nothing; lengths in pixels still need a size to measure against,
        // and the viewer sends a new view when the real one is known.
        target: TargetSize {
            width: wire.target.width.max(1),
            height: wire.target.height.max(1),
        },
        visible: wire.visible,
    })
}

/// The wire form of a lane's load status for `audience`: the decoder's warnings name the host's files and lines, so
/// only an audience with host details hears them.
fn status_wire(status: LaneStatus, audience: Audience) -> LaneStatusWire {
    match status {
        LaneStatus::Loading => LaneStatusWire::Loading,
        LaneStatus::Ready(ready) => LaneStatusWire::Ready {
            clock: match ready.clock {
                TimeKind::Frame => ClockWire::Frame,
                TimeKind::Timestamp => ClockWire::Timestamp,
            },
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
                .map(|warning| WarningInfo {
                    problem: warning.problem,
                    reasons: warning.reasons,
                    consequence: warning.consequence,
                    more_reasons: warning.more_reasons,
                })
                .collect(),
            samples: ready.samples as u64,
        },
        LaneStatus::Failed(error) => LaneStatusWire::Failed(project(&error, audience)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wire() -> LaneViewWire {
        LaneViewWire {
            catalog: "built-in:tracking".into(),
            filter: [("show_car".to_owned(), false)].into(),
            tolerance_ms: Some(40),
            sticky: StickyWire {
                enabled: true,
                timeout_ms: Some(2050),
                opacity: 0.5,
            },
            target: TargetWire {
                width: 640,
                height: 360,
            },
            visible: false,
        }
    }

    #[test]
    fn a_view_survives_the_trip_and_what_cannot_be_applied_says_why() {
        let view = view_from_wire(wire()).unwrap();
        assert_eq!(view_wire(&view), wire());

        let refused = |change: fn(&mut LaneViewWire)| {
            let mut bad = wire();
            change(&mut bad);
            view_from_wire(bad).unwrap_err()
        };
        assert!(refused(|v| v.catalog = "built-in:nope".into()).contains("built-in:nope"));
        assert!(refused(|v| v.sticky.timeout_ms = Some(100_001)).contains("100 seconds"));
        assert!(refused(|v| v.sticky.opacity = 1.5).contains("opacity"));
        assert!(refused(|v| v.sticky.opacity = f32::NAN).contains("opacity"));

        let mut unmeasured = wire();
        unmeasured.target = TargetWire {
            width: 0,
            height: 0,
        };
        let view = view_from_wire(unmeasured).unwrap();
        assert_eq!((view.target.width, view.target.height), (1, 1));
        let mut unlimited = wire();
        (unlimited.tolerance_ms, unlimited.sticky.timeout_ms) = (None, None);
        assert_eq!(
            view_wire(&view_from_wire(unlimited.clone()).unwrap()),
            unlimited
        );
    }
}
