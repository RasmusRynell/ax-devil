//! The streaming half of a playback session: once the video is open, tell the viewer about the entry, then apply its
//! windows and lane policies and send the frames in the windows and the lanes' data for them: Sample Items and Frame
//! Maps, nearest the playhead first, interleaved with the frames by the same rank.

use super::{
    End, Flow, Incoming, Outbox, Outgoing,
    cached::CachedFeed,
    catalogs::CatalogFeed,
    export::{Exports, Request},
    file_scope::{self, FileScopes},
    gate::Gate,
    inspect::Inspections,
    lanes::{self, LaneEvent, Lanes},
    next_client, stopped, text,
};
use crate::{
    exports::ExportService,
    video_sources::{VideoSources, video_location},
};
use api_types::{
    Audience,
    playback::{
        ClientMessage, Direction as WireDirection, PLAYBACK_PROTOCOL, ServerMessage, VideoPtsWire,
        Want as WireWant, envelope,
    },
    project,
};
use axum::extract::ws::Message;
use catalogs::Library;
use foundation::{ErrorCode, FrameSize, HostPath, UserError};
use media::PtsTiming;
use overlay_sync::MatchPolicy;
use overlays::OverlayService;
use playback::{Delivery, Direction, Priority, Session, Want};
use std::{collections::BTreeMap, sync::Arc, time::Duration};
use tokio::{sync::watch, time::MissedTickBehavior};
use workspace::Entry;

/// Before the viewer's first `Want`, which follows its Timeline within a round trip, it gets this many frames around
/// the one it opened on.
const INITIAL_BEHIND: u32 = 4;
const INITIAL_AHEAD: u32 = 4;
/// How often a stream judges again what its viewer may have, so a follower's window moves with the presenter's clock.
const REJUDGE: Duration = Duration::from_millis(100);

/// What the stream knows of the viewer and of the entry it opened.
pub(super) struct Setup<'a> {
    /// The entry: its video, which the session plays, and its overlays, one lane each.
    pub(super) entry: &'a Entry,
    /// How the workspace says each lane matches its records to frames; `None` is the default.
    pub(super) matching: &'a BTreeMap<String, MatchPolicy>,
    /// The frame the viewer opened on.
    pub(super) start_frame: Option<u64>,
    /// The most bytes of Sample Items the viewer holds at once.
    pub(super) data_bytes: u64,
    /// What the viewer may learn from an error, a warning or a lane problem.
    pub(super) audience: Audience,
    /// The size of the decoded pictures.
    pub(super) size: FrameSize,
    /// The video's warnings, which name the host's files.
    pub(super) warnings: Vec<String>,
    /// How the video's frame times come from its timestamps; `None` for an image sequence.
    pub(super) pts: Option<PtsTiming>,
    /// The catalogs a lane may draw with, which the viewer asks for by id.
    pub(super) library: &'a Library,
    /// Opens the lanes' overlays.
    pub(super) overlays: &'a OverlayService,
    /// Runs the viewer's export, if the owner asks for one.
    pub(super) exports: &'a Arc<ExportService>,
    /// The open videos, which an export reads from.
    pub(super) videos: &'a Arc<VideoSources>,
}

/// What the session sends next: notices first, then whichever of a lane's data and the next frame ranks first.
enum Next {
    Catalog(Outgoing),
    Inspection(Outgoing),
    Export(Outgoing),
    FileScope(file_scope::Reply),
    /// The cache is due to be looked at.
    Cached,
    Lane(LaneEvent),
    /// `None` when the session or its source closed.
    Delivery(Option<Delivery>),
}

/// An open entry's session.
struct Playing<'a, I> {
    session: &'a mut Session,
    gate: Gate,
    lanes: Lanes,
    catalogs: CatalogFeed,
    inspections: Inspections,
    exports: Exports,
    file_scopes: FileScopes,
    cached: CachedFeed,
    incoming: &'a mut I,
    outbox: &'a Outbox,
    closing: &'a mut watch::Receiver<bool>,
    /// The video's file, which the errors that end the session name when the layer that failed did not.
    location: HostPath,
    audience: Audience,
    last: u64,
    failures: u32,
    /// The data frames after and before the playhead the viewer asked for last.
    data: (u32, u32),
}

/// Tells the viewer about the entry, then applies its messages and sends what it has room for until it leaves.
pub(super) async fn stream(
    session: &mut Session,
    setup: Setup<'_>,
    incoming: &mut impl Incoming,
    outbox: &Outbox,
    closing: &mut watch::Receiver<bool>,
    gate: Gate,
) -> Flow<()> {
    // What the viewer is told comes from the source it joined, which can be older than this open.
    let spec = session.spec();
    let timeline = spec.timeline.clone();
    let frame_bytes = spec.frame_bytes as u64;
    let last = timeline.last();
    let location = video_location(&setup.entry.video);
    gate.open(timeline.clone());
    let lanes = Lanes::open(&setup, timeline.clone(), location.clone());
    let catalogs = CatalogFeed::new(setup.library);
    let opened = text(&ServerMessage::Opened {
        protocol: PLAYBACK_PROTOCOL,
        frame_count: timeline.len(),
        duration_us: timeline.duration().as_us(),
        frame_bytes,
        width: setup.size.width,
        height: setup.size.height,
        warnings: if setup.audience.host_details {
            setup.warnings
        } else {
            Vec::new()
        },
        lanes: lanes.infos(setup.entry, setup.matching),
        lane_defaults: lanes::lane_defaults(setup.library),
        catalogs: catalogs.listing(),
        video_pts: setup.pts.map(|pts| VideoPtsWire {
            time_base: [pts.time_base.0, pts.time_base.1],
            first_pts: pts.first_pts,
        }),
    });
    // The overlays stop with `Playing`, which goes when the session ends, a revoked one included.
    let mut playing = Playing {
        session,
        gate,
        lanes,
        catalogs,
        inspections: Inspections::default(),
        exports: Exports::new(setup.entry, setup.audience, setup.exports, setup.videos),
        file_scopes: FileScopes::new(setup.audience),
        cached: CachedFeed::new(),
        incoming,
        outbox,
        closing,
        location,
        audience: setup.audience,
        last,
        failures: 0,
        data: (INITIAL_AHEAD, INITIAL_BEHIND),
    };
    // Decoding starts at the first frame the viewer shows while the messages below are on their way.
    playing.ask(Want {
        playhead: setup.start_frame.unwrap_or(0).min(last),
        ahead: INITIAL_AHEAD,
        behind: INITIAL_BEHIND,
        direction: Direction::Forward,
        priority: Priority::default(),
    })?;
    playing.send(opened).await?;
    playing
        .send(Message::Binary(envelope::timeline(&timeline)))
        .await?;
    playing.pump().await
}

impl<I: Incoming> Playing<'_, I> {
    /// Gives the session `want` as the authority allows it, and the overlays the data window inside it.
    fn ask(&mut self, want: Want) -> Flow<()> {
        self.gate.ask(self.session, want)?;
        self.lanes.follow(self.gate.applied(), self.data);
        Ok(())
    }

    /// Judges the viewer's last want again, after the authority changed or as the presenter's clock moves, then
    /// gives the overlays back the data the writer dropped, which they offer again inside the window now.
    fn refresh(&mut self) -> Flow<()> {
        self.gate.refresh(self.session)?;
        self.lanes.follow(self.gate.applied(), self.data);
        for event in self.gate.take_rejected() {
            self.lanes.undelivered(&event);
        }
        Ok(())
    }

    /// Handles what the viewer sent once its video is open: windows apply as its authority allows them, lane policies
    /// apply to their lanes, catalog requests are answered, anything else ends the session.
    fn apply(&mut self, message: Option<Result<ClientMessage, UserError>>) -> Flow<()> {
        match message {
            Some(Ok(ClientMessage::Want(wire))) => {
                self.data = (wire.data_ahead, wire.data_behind);
                self.ask(want(wire))
            }
            Some(Ok(ClientMessage::LanePolicy {
                lane,
                revision,
                policy,
                subscribed,
            })) => self.lanes.policy(lane, revision, policy, subscribed),
            Some(Ok(ClientMessage::Catalog { id })) => {
                self.catalogs.ask(id);
                Ok(())
            }
            Some(Ok(ClientMessage::Inspect {
                source,
                data_revision,
                sample,
                entity,
            })) => {
                self.inspections
                    .ask(self.lanes.overlays(), source, data_revision, sample, entity);
                Ok(())
            }
            Some(Ok(ClientMessage::Open { .. })) => Err(End::Failed(UserError::new(
                ErrorCode::ProtocolViolation,
                "The video is already open",
            ))),
            Some(Ok(ClientMessage::Export {
                job,
                lanes,
                first,
                last,
                format,
                path,
            })) => {
                let request = Request {
                    job,
                    lanes,
                    first,
                    last,
                    format,
                    path,
                };
                self.exports
                    .start(request, self.lanes.overlays(), &self.catalogs, self.last);
                Ok(())
            }
            Some(Ok(ClientMessage::CancelExport { job })) => {
                self.exports.cancel(job);
                Ok(())
            }
            Some(Err(error)) => Err(End::Failed(error)),
            Some(Ok(ClientMessage::FileQuery {
                lane,
                request,
                query,
                classes,
                sort,
                offset,
                limit,
                bins,
                locate,
            })) => self.file_scopes.ask(
                self.lanes.overlays(),
                self.gate.is_own(),
                file_scope::Ask {
                    lane,
                    request,
                    query,
                    classes,
                    sort,
                    offset,
                    limit,
                    bins,
                    locate,
                },
            ),
            Some(Ok(ClientMessage::Appearances {
                lane,
                request,
                entity,
            })) => self.file_scopes.ask_appearances(
                self.lanes.overlays(),
                self.gate.is_own(),
                file_scope::AppearancesAsk {
                    lane,
                    request,
                    entity,
                },
            ),
            Some(Ok(ClientMessage::FileRelease { lane })) => {
                self.file_scopes.release(self.lanes.overlays(), lane)
            }
            Some(Ok(ClientMessage::Close)) | None => Err(End::Closed),
        }
    }

    /// Sends `message` once the outbox has room, while still hearing from the viewer and the backend: a viewer that
    /// stopped reading cannot keep an ended session waiting.
    async fn send(&mut self, message: Message) -> Flow<()> {
        loop {
            tokio::select! {
                reserved = self.outbox.reserve() => {
                    reserved.map_err(|_| End::Closed)?.send(message.into());
                    return Ok(());
                }
                client = next_client(self.incoming) => self.apply(client)?,
                () = self.gate.changed() => self.refresh()?,
                () = stopped(self.closing) => return Err(End::Closed),
            }
        }
    }

    /// Applies the viewer's messages and sends what its video and overlays produce until the session ends.
    async fn pump(&mut self) -> Flow<()> {
        let outbox = self.outbox;
        let mut room = None;
        // A presenter's clock moves without a message, so what the viewer may have is judged again as it does.
        let mut clock = tokio::time::interval(REJUDGE);
        clock.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            // Before anything is delivered, a change of authority narrows what the session is asked for.
            if self.gate.has_changed() {
                self.refresh()?;
            }
            tokio::select! {
                message = next_client(self.incoming) => self.apply(message)?,
                // Room in the outbox is taken before a message is, so the message sent is the best one at that moment.
                reserved = outbox.reserve(), if room.is_none() => {
                    room = Some(reserved.map_err(|_| End::Closed)?);
                }
                next = next(
                    self.session,
                    &mut self.catalogs,
                    &mut self.inspections,
                    &mut self.exports,
                    &mut self.file_scopes,
                    &mut self.cached,
                    &mut self.lanes,
                ), if room.is_some() => {
                    if let Some(message) = self.encode(next)?
                        && let Some(room) = room.take()
                    {
                        room.send(message);
                    }
                }
                () = stopped(self.closing) => return Err(End::Closed),
                () = self.gate.changed() => self.refresh()?,
                _ = clock.tick() => self.refresh()?,
            }
        }
    }

    /// The message for what the session produced, if it is one.
    fn encode(&mut self, next: Next) -> Flow<Option<Outgoing>> {
        match next {
            Next::Catalog(message) | Next::Inspection(message) | Next::Export(message) => {
                Ok(Some(message))
            }
            Next::FileScope(reply) => Ok(Some(reply.outgoing(self.gate.is_own()))),
            Next::Cached => Ok(self.cached.message(self.session.cached())),
            Next::Lane(event) => Ok(self.lanes.encode(event)),
            Next::Delivery(delivery) => {
                let delivery = delivery.ok_or(End::Closed)?;
                encode(
                    delivery,
                    self.last,
                    &self.location,
                    self.audience,
                    &mut self.failures,
                )
                .map(Some)
            }
        }
    }
}

/// The next thing to send: the catalogs', inspections', exports' and file queries' messages and the lanes' notices, then
/// the cache when it is due, then their data or the next frame, whichever ranks first, the data first on a tie. The
/// cache comes before the frames so that a continuous flow of them cannot starve it. The lanes hold an item back while
/// a frame ranks before it; the rank is read again whenever this is called, which is after every message sent and
/// every message of the viewer.
async fn next(
    session: &mut Session,
    catalogs: &mut CatalogFeed,
    inspections: &mut Inspections,
    exports: &mut Exports,
    file_scopes: &mut FileScopes,
    cached: &mut CachedFeed,
    lanes: &mut Lanes,
) -> Next {
    let rank = session.next_rank();
    tokio::select! {
        biased;
        message = catalogs.next() => Next::Catalog(message),
        message = inspections.next() => Next::Inspection(message),
        message = exports.next() => Next::Export(message),
        reply = file_scopes.next() => Next::FileScope(reply),
        () = cached.tick() => Next::Cached,
        event = lanes.next(rank) => Next::Lane(event),
        delivery = session.next_delivery() => Next::Delivery(delivery),
    }
}

/// The message for something the session delivers. A source that cannot decode at all ends the session.
fn encode(
    delivery: Delivery,
    last: u64,
    location: &HostPath,
    audience: Audience,
    failures: &mut u32,
) -> Flow<Outgoing> {
    match delivery {
        Delivery::Frame(frame) => {
            let sequence_id = frame.id().sequence_id;
            Ok(Outgoing::frame(
                Message::Binary(envelope::video_frame(&frame, sequence_id == last)),
                sequence_id,
            ))
        }
        Delivery::Failure(failure) => {
            // A video with many bad frames would flood the log: the first is a warning, the rest are for diagnosis.
            *failures += 1;
            let error = &failure.error;
            if *failures == 1 {
                tracing::warn!(
                    "Frame {} failed: {:?}: {}",
                    failure.sequence_id,
                    error.location,
                    error.reason
                );
            } else {
                tracing::debug!(
                    "Frame {} failed: {:?}: {}",
                    failure.sequence_id,
                    error.location,
                    error.reason
                );
            }
            Ok(Outgoing::frame(
                text(&ServerMessage::FrameError {
                    sequence_id: failure.sequence_id,
                    error: project(&failure.error, audience),
                }),
                failure.sequence_id,
            ))
        }
        Delivery::Failed(mut error) => {
            if error.location.is_none() {
                error.location = Some(location.clone().into());
            }
            Err(End::Failed(error))
        }
    }
}

pub(super) fn want(wire: WireWant) -> Want {
    Want {
        playhead: wire.playhead,
        ahead: wire.ahead,
        behind: wire.behind,
        direction: match wire.direction {
            WireDirection::Forward => Direction::Forward,
            WireDirection::Backward => Direction::Backward,
        },
        priority: Priority::default(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::Location;

    #[test]
    fn a_source_failure_ends_the_session_naming_the_file_unless_the_failure_already_names_one() {
        let ended = |error| match encode(
            Delivery::Failed(error),
            9,
            &HostPath::new("/clips/a.mp4"),
            Audience::HOST,
            &mut 0,
        ) {
            Err(End::Failed(error)) => error,
            _ => panic!("a failed source must end the session"),
        };

        let at = |path| Some(Location::Host(HostPath::new(path)));
        assert_eq!(
            ended(UserError::new(ErrorCode::DecodeFailed, "no decoder")).location,
            at("/clips/a.mp4")
        );
        assert_eq!(
            ended(
                UserError::new(ErrorCode::DecodeFailed, "no decoder")
                    .at(HostPath::new("/clips/b.mp4"))
            )
            .location,
            at("/clips/b.mp4")
        );
    }
}
