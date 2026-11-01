//! The session of an open Entry: tells the viewer about it, applies its windows and lane views, and sends what its
//! video and its overlays produce until it leaves.

use super::{End, Flow, Outbox, envelope, lanes, next_client, overlay_envelope, stopped, text};
use api_types::playback::{
    ClientMessage, Direction as WireDirection, LaneInfo, LaneViewWire, PLAYBACK_PROTOCOL,
    ServerMessage, Want as WireWant,
};
use axum::extract::ws::{Message, WebSocket};
use foundation::{FrameSize, UserError};
use futures_util::stream::SplitStream;
use overlays::{LaneStatus, OverlayEvent, OverlayService, ViewerOverlays};
use playback::{Delivery, Direction, Session, Want};
use playlist_resolvers::OverlayRef;
use std::collections::{BTreeMap, BTreeSet};
use tokio::sync::watch;

/// Before the viewer's first `Want`, which follows its Timeline within a round trip, it gets this many frames around
/// the one it opened on.
const INITIAL_BEHIND: u32 = 4;
const INITIAL_AHEAD: u32 = 4;

/// The viewer's socket and the backend's stop signal.
pub(super) struct Link<'a> {
    pub(super) incoming: &'a mut SplitStream<WebSocket>,
    pub(super) outbox: &'a Outbox,
    pub(super) closing: watch::Receiver<bool>,
}

/// What an opened Entry tells its viewer, and what its lanes are made of.
pub(super) struct Setup {
    /// The video's file, which errors that end the session name when the layer that failed did not.
    pub(super) location: String,
    pub(super) lanes: Vec<LaneInfo>,
    /// The overlay of each lane; `None` for the plain lane of an entry without overlays.
    pub(super) sources: Vec<Option<OverlayRef>>,
    pub(super) size: FrameSize,
    pub(super) warnings: Vec<String>,
    pub(super) start_frame: Option<u64>,
    pub(super) service: OverlayService,
}

/// What the session sends next.
enum Next {
    /// A lane view the viewer sent that cannot be applied.
    Problem(u16, String),
    Overlay(OverlayEvent),
    /// The overlays closed on their own, which leaves the video playing without them.
    OverlaysEnded,
    /// A lane whose overlays ended before it failed or closed.
    Abandoned(u16),
    /// `None` when the session or its source closed.
    Delivery(Option<Delivery>),
}

/// Where the session's outgoing messages come from.
struct Sources<'a> {
    session: &'a mut Session,
    overlays: ViewerOverlays,
    overlays_live: bool,
    /// The lanes with an overlay that has not failed.
    working: BTreeSet<u16>,
    /// Lanes the overlays left without a terminal status when they ended, each still to be told it failed.
    abandoned: Vec<u16>,
    /// The newest unsent complaint about each lane's view; a lane's next view replaces it.
    problems: BTreeMap<u16, String>,
}

impl Sources<'_> {
    /// The next thing to send: complaints about views first, then overlays, which are nearest the playhead and few,
    /// then frames.
    async fn next(&mut self) -> Next {
        if let Some((lane, reason)) = self.problems.pop_first() {
            return Next::Problem(lane, reason);
        }
        if let Some(lane) = self.abandoned.pop() {
            return Next::Abandoned(lane);
        }
        let overlays_live = self.overlays_live;
        tokio::select! {
            biased;
            event = self.overlays.next_event(), if overlays_live => match event {
                Some(event) => Next::Overlay(event),
                None => Next::OverlaysEnded,
            },
            delivery = self.session.next_delivery() => Next::Delivery(delivery),
        }
    }
}

/// An open Entry's session.
struct Playing<'a> {
    link: Link<'a>,
    sources: Sources<'a>,
    lane_count: usize,
    location: String,
    last: u64,
    failures: u32,
}

/// Tells the viewer about the Entry, then applies its messages and sends what it has room for until it leaves.
pub(super) async fn run(session: &mut Session, setup: Setup, link: Link<'_>) -> Flow<()> {
    // What the viewer is told comes from the source it joined, which can be older than this open.
    let spec = session.spec();
    let timeline = spec.timeline.clone();
    let last = timeline.last();
    // Decoding starts at the first frame the viewer shows while the messages below are on their way.
    let initial = Want {
        playhead: setup.start_frame.unwrap_or(0).min(last),
        ahead: INITIAL_AHEAD,
        behind: INITIAL_BEHIND,
        direction: Direction::Forward,
    };
    session.want(initial);
    let lane_count = setup.lanes.len();
    let opened = text(&ServerMessage::Opened {
        protocol: PLAYBACK_PROTOCOL,
        frame_count: timeline.len(),
        duration_us: timeline.duration().as_us(),
        frame_bytes: spec.frame_bytes as u64,
        width: setup.size.width,
        height: setup.size.height,
        warnings: setup.warnings,
        lane_defaults: lanes::view_wire(&setup.service.lane_defaults()),
        lanes: setup.lanes,
    });
    let working = (0..lane_count)
        .zip(&setup.sources)
        .filter(|(_, source)| source.is_some())
        .filter_map(|(lane, _)| u16::try_from(lane).ok())
        .collect();
    let overlays = setup
        .service
        .open_viewer(setup.sources, timeline.clone(), setup.size);
    overlays.want(initial);
    let mut playing = Playing {
        link,
        sources: Sources {
            session,
            overlays,
            overlays_live: true,
            working,
            abandoned: Vec::new(),
            problems: BTreeMap::new(),
        },
        lane_count,
        location: setup.location,
        last,
        failures: 0,
    };
    playing.send(opened).await?;
    playing
        .send(Message::Binary(envelope::timeline(&timeline)))
        .await?;
    playing.pump().await
}

impl Playing<'_> {
    /// Handles what the viewer sent once its video is open: windows and lane views apply, anything else ends the
    /// session.
    fn apply(&mut self, message: Option<Result<ClientMessage, UserError>>) -> Flow<()> {
        match message {
            Some(Ok(ClientMessage::Want(wire))) => {
                let want = want(wire);
                self.sources.session.want(want);
                self.sources.overlays.want(want);
                Ok(())
            }
            Some(Ok(ClientMessage::LaneView {
                lane,
                revision,
                view,
            })) => self.lane_view(lane, revision, view),
            Some(Ok(ClientMessage::Open { .. })) => Err(End::Failed(UserError::new(
                "The video is already open",
                "playback",
            ))),
            Some(Err(error)) => Err(End::Failed(error)),
            Some(Ok(ClientMessage::Close)) | None => Err(End::Closed),
        }
    }

    /// Gives a lane the view the viewer asked for. A view that cannot be applied is reported to the viewer as a
    /// `LaneProblem` and the lane keeps its last view; the session goes on.
    fn lane_view(&mut self, lane: u16, revision: u32, view: LaneViewWire) -> Flow<()> {
        if usize::from(lane) >= self.lane_count {
            return Err(End::Failed(UserError::new(
                format!("The video has no lane {lane}"),
                "playback",
            )));
        }
        match lanes::view_from_wire(view) {
            Ok(view) => {
                self.sources.problems.remove(&lane);
                self.sources.overlays.set_view(lane, revision, view);
            }
            Err(reason) => {
                self.sources.problems.insert(lane, reason);
            }
        }
        Ok(())
    }

    /// Sends `message` once the outbox has room, while still hearing from the viewer and the backend: a viewer that
    /// stopped reading cannot keep an ended session waiting.
    async fn send(&mut self, message: Message) -> Flow<()> {
        loop {
            tokio::select! {
                reserved = self.link.outbox.reserve() => {
                    reserved.map_err(|_| End::Closed)?.send(message);
                    return Ok(());
                }
                client = next_client(self.link.incoming) => self.apply(client)?,
                () = stopped(&mut self.link.closing) => return Err(End::Closed),
            }
        }
    }

    /// Applies the viewer's messages and sends what its video and overlays produce until the session ends.
    async fn pump(&mut self) -> Flow<()> {
        let outbox = self.link.outbox;
        let mut room = None;
        loop {
            tokio::select! {
                message = next_client(self.link.incoming) => self.apply(message)?,
                // Room in the outbox is taken before a message is, so the message sent is the best one at that moment.
                reserved = outbox.reserve(), if room.is_none() => {
                    room = Some(reserved.map_err(|_| End::Closed)?);
                }
                next = self.sources.next(), if room.is_some() => {
                    if let Some(message) = self.encode(next)?
                        && let Some(room) = room.take()
                    {
                        room.send(message);
                    }
                }
                () = stopped(&mut self.link.closing) => return Err(End::Closed),
            }
        }
    }

    /// The message for what the session produced, if it is one.
    fn encode(&mut self, next: Next) -> Flow<Option<Message>> {
        Ok(Some(match next {
            Next::Problem(lane, reason) => text(&ServerMessage::LaneProblem { lane, reason }),
            Next::Overlay(OverlayEvent::Status { lane, status }) => {
                if matches!(status, LaneStatus::Failed(_)) {
                    self.sources.working.remove(&lane);
                }
                text(&ServerMessage::LaneStatus {
                    lane,
                    status: lanes::status_wire(status),
                })
            }
            Next::Overlay(OverlayEvent::Problem { lane, reason }) => {
                text(&ServerMessage::LaneProblem { lane, reason })
            }
            Next::Overlay(OverlayEvent::Overlay(overlay)) => {
                Message::Binary(overlay_envelope::overlay(&overlay))
            }
            Next::OverlaysEnded => {
                tracing::warn!(
                    "The overlays of {} stopped; the video goes on",
                    self.location
                );
                self.sources.overlays_live = false;
                // The viewer waits for every lane that is not failed, so none may be left waiting.
                self.sources.abandoned = std::mem::take(&mut self.sources.working)
                    .into_iter()
                    .collect();
                return Ok(None);
            }
            Next::Abandoned(lane) => text(&ServerMessage::LaneStatus {
                lane,
                status: lanes::status_wire(LaneStatus::Failed(UserError::new(
                    "The overlays stopped unexpectedly",
                    self.location.clone(),
                ))),
            }),
            Next::Delivery(delivery) => {
                let delivery = delivery.ok_or(End::Closed)?;
                encode_delivery(delivery, self.last, &self.location, &mut self.failures)?
            }
        }))
    }
}

fn want(wire: WireWant) -> Want {
    Want {
        playhead: wire.playhead,
        ahead: wire.ahead,
        behind: wire.behind,
        direction: match wire.direction {
            WireDirection::Forward => Direction::Forward,
            WireDirection::Backward => Direction::Backward,
        },
    }
}

/// The message for something the video session delivers. A source that cannot decode at all ends the session.
fn encode_delivery(
    delivery: Delivery,
    last: u64,
    location: &str,
    failures: &mut u32,
) -> Flow<Message> {
    match delivery {
        Delivery::Frame(frame) => Ok(Message::Binary(envelope::video_frame(
            &frame,
            frame.id().sequence_id == last,
        ))),
        Delivery::Failure(failure) => {
            // A video with many bad frames would flood the log: the first is a warning, the rest are for diagnosis.
            *failures += 1;
            let error = &failure.error;
            if *failures == 1 {
                tracing::warn!(
                    "Frame {} failed: {}: {}",
                    failure.sequence_id,
                    error.location,
                    error.reason
                );
            } else {
                tracing::debug!(
                    "Frame {} failed: {}: {}",
                    failure.sequence_id,
                    error.location,
                    error.reason
                );
            }
            Ok(text(&ServerMessage::FrameError {
                sequence_id: failure.sequence_id,
                reason: failure.error.reason,
            }))
        }
        Delivery::Failed(mut error) => {
            if error.location.is_empty() {
                error.location = location.to_owned();
            }
            Err(End::Failed(error))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_source_failure_ends_the_session_naming_the_file_unless_the_failure_already_names_one() {
        let ended =
            |error| match encode_delivery(Delivery::Failed(error), 9, "/clips/a.mp4", &mut 0) {
                Err(End::Failed(error)) => error,
                _ => panic!("a failed source must end the session"),
            };

        assert_eq!(
            ended(UserError::new("no decoder", "")).location,
            "/clips/a.mp4"
        );
        assert_eq!(
            ended(UserError::new("no decoder", "/clips/b.mp4")).location,
            "/clips/b.mp4"
        );
    }
}
