//! A live lease on the playback socket. The viewer is sent `LiveOpened`, its lane's `LaneStatus` and the feeds'
//! `LiveStatus`, then, as the pipeline releases them, Sample Items, the frame, its one-cell Frame Map, `Evict`s and
//! `LiveEvents`, with `LiveStatus` on every change and `LiveGap` where the overlay lost samples. A slow viewer is
//! shown the newest frame, never a queue.
//!
//! Only a holder with its own authority may pause, resume or retry; a follower only watches, and what it sends is
//! ignored. A holder that begins to follow is resumed. A live video has no Timeline, no window and no export, so what a recording's viewer would ask for beyond
//! that is ignored too, except a File scope query, which is answered as unavailable.

use super::{
    End, Flow, Incoming, Outbox, Outgoing,
    catalogs::CatalogFeed,
    gate::Gate,
    inspect::Inspections,
    lanes::{lane_defaults, policy_from_wire, policy_wire, status_wire},
    next_client, stopped, text,
    wire::Wire,
};
use crate::AppState;
use api_types::{
    Audience,
    live::{LiveEventWire, LiveEventsWire, LiveGapWire, LiveOpenedWire, LiveSampleEventsWire},
    playback::{
        ClientMessage, EventAskWire, EventQueryWire, FileAnswer, FileScopeUnavailable, LaneInfo,
        PLAYBACK_PROTOCOL, ServerMessage, envelope,
    },
};
use axum::extract::ws::Message;
use catalogs::Library;
use foundation::{ErrorCode, UserError};
use live::{LiveViewer, Shown, Update};
use overlay_sync::MatchPolicy;
use overlays::{LaneReady, LaneStatus, LiveEvents};
use sessions::{SessionId, session::StreamLease};
use std::collections::VecDeque;
use tokio::sync::watch;
use workspace::{LIVE_SLOT, LiveOverlay, LiveVideoContent};

/// How many answers may wait for a viewer that is not reading. A viewer that goes on asking past this has its session
/// ended, so one socket cannot make the backend hold an unbounded queue.
const MAX_NOTICES: usize = 64;

/// Opens the lease's live video for the viewer and serves it until the viewer leaves, the backend stops or the
/// authority is revoked. Dropping the viewer detaches it from the feed.
pub(super) async fn run(
    state: &AppState,
    lease: &StreamLease,
    content: &LiveVideoContent,
    audience: Audience,
    incoming: &mut impl Incoming,
    outbox: &Outbox,
    mut gate: Gate,
) -> Flow<()> {
    let mut closing = state.stopping.clone();
    let matching = lease.matching().get(LIVE_SLOT).copied();
    let handlers = state.plugins.handlers();
    let viewer = tokio::select! {
        opened = state.live.open(content, &handlers, matching.unwrap_or_default()) => opened.map_err(End::Failed)?,
        () = stopped(&mut closing) => return Err(End::Closed),
        () = gate.revoked() => return Err(End::Revoked),
    };
    let label = match content.overlay {
        LiveOverlay::None => "Live",
        ref overlay => overlay.mode_name(),
    };
    stream(
        viewer,
        Setup {
            label,
            matching,
            library: &state.catalogs,
            audience,
            session: lease.session(),
            wire: Wire::of(&state.policy),
        },
        incoming,
        outbox,
        &mut closing,
        gate,
    )
    .await
}

/// What the stream knows besides the viewer.
pub(super) struct Setup<'a> {
    /// The lane's name.
    pub(super) label: &'a str,
    /// The matching the workspace keeps for the lane; without it the lane starts as a recording's would.
    pub(super) matching: Option<MatchPolicy>,
    /// The catalogs the lane may draw with.
    pub(super) library: &'a Library,
    /// What the viewer may learn of an error.
    pub(super) audience: Audience,
    /// The Session the viewer is in, which scopes the user catalogs a Guest is shown.
    pub(super) session: SessionId,
    /// The form the viewer is sent frames in.
    pub(super) wire: Wire,
}

/// What of the lane's status the viewer was told, to say it again when it changes: the numbering of the samples and the
/// class filters.
fn ready_key(ready: &LaneReady) -> (u32, Vec<String>) {
    (
        ready.data_revision,
        ready
            .filter
            .options
            .iter()
            .map(|option| option.id.to_string())
            .collect(),
    )
}

struct Live<'a, I> {
    viewer: LiveViewer,
    gate: Gate,
    catalogs: CatalogFeed,
    inspections: Inspections,
    incoming: &'a mut I,
    outbox: &'a Outbox,
    closing: &'a mut watch::Receiver<bool>,
    audience: Audience,
    wire: Wire,
    matching: MatchPolicy,
    told: Option<(u32, Vec<String>)>,
    /// Answers to what the viewer sent, to go before anything else.
    notices: VecDeque<Message>,
    /// Whether the owner asked to reopen the connections.
    retry: bool,
    /// Frames that could not be compressed for the viewer.
    failures: u32,
}

/// Tells the viewer about the stream, then serves it.
pub(super) async fn stream(
    viewer: LiveViewer,
    setup: Setup<'_>,
    incoming: &mut impl Incoming,
    outbox: &Outbox,
    closing: &mut watch::Receiver<bool>,
    gate: Gate,
) -> Flow<()> {
    let catalogs = CatalogFeed::new(setup.library, setup.audience, setup.session);
    let opened = text(&ServerMessage::LiveOpened(LiveOpenedWire {
        protocol: PLAYBACK_PROTOCOL,
        lane: LaneInfo {
            label: setup.label.to_owned(),
            slot: LIVE_SLOT.to_owned(),
            source: viewer.has_overlay().then_some(0),
            policy: setup.matching.as_ref().map(policy_wire),
        },
        lane_defaults: lane_defaults(setup.library),
        catalogs: catalogs.listing(),
    }));
    let status = text(&ServerMessage::LiveStatus(
        viewer.project_status(viewer.status(), setup.audience),
    ));
    let mut live = Live {
        viewer,
        gate: gate.clone(),
        catalogs,
        inspections: Inspections::new(gate.inspection_permission()),
        incoming,
        outbox,
        closing,
        audience: setup.audience,
        wire: setup.wire,
        matching: setup.matching.unwrap_or_default(),
        told: None,
        notices: VecDeque::new(),
        retry: false,
        failures: 0,
    };
    live.send(opened).await?;
    live.ready().await?;
    live.send(status).await?;
    live.pump().await
}

impl<I: Incoming> Live<'_, I> {
    /// Applies what the viewer sent. A follower's `Live` actions are ignored.
    fn apply(&mut self, message: Option<Result<ClientMessage, UserError>>) -> Flow<()> {
        let violation =
            |reason: &str| End::Failed(UserError::new(ErrorCode::ProtocolViolation, reason));
        match message {
            Some(Ok(ClientMessage::Live { action })) => {
                if !self.gate.is_own() {
                    tracing::debug!("A follower's live action was ignored");
                    return Ok(());
                }
                match action {
                    api_types::live::LiveAction::Pause => self.viewer.pause(true),
                    api_types::live::LiveAction::Resume => self.viewer.pause(false),
                    api_types::live::LiveAction::Retry => self.retry = true,
                }
                Ok(())
            }
            Some(Ok(ClientMessage::LanePolicy {
                lane,
                revision,
                policy,
                subscribed,
            })) => {
                if lane != 0 {
                    return Err(violation("The live video has one lane"));
                }
                // A policy that cannot be applied keeps the matching and still adopts the revision.
                match policy_from_wire(policy) {
                    Ok(matching) => self.matching = matching,
                    Err(reason) if self.audience.host_details => {
                        self.notify(&ServerMessage::LaneProblem { lane, reason })?;
                    }
                    Err(_) => {}
                }
                self.viewer.set_policy(revision, self.matching, subscribed);
                Ok(())
            }
            Some(Ok(ClientMessage::Catalog { id })) => {
                self.catalogs.ask(id);
                Ok(())
            }
            Some(Ok(ClientMessage::Inspect {
                request,
                source,
                data_revision,
                sample,
                entity,
            })) => {
                let target = self.viewer.inspect_target(source, data_revision, sample);
                self.inspections
                    .ask_target(request, source, data_revision, sample, entity, target);
                Ok(())
            }
            Some(Ok(ClientMessage::InspectRelease)) => {
                self.inspections.release();
                Ok(())
            }
            Some(Ok(ClientMessage::InspectContinue(read))) => {
                let target = self.inspections.continuation_target(&read.token).and_then(
                    |(source, revision, sample)| {
                        self.viewer.inspect_target(source, revision, sample)
                    },
                );
                self.inspections.continue_target(read, target);
                Ok(())
            }
            Some(Ok(ClientMessage::InspectFields {
                request,
                source,
                data_revision,
                sample,
                paths,
            })) => {
                let target = self.viewer.inspect_target(source, data_revision, sample);
                self.inspections.ask_fields_target(
                    request,
                    source,
                    data_revision,
                    sample,
                    paths,
                    target,
                );
                Ok(())
            }
            Some(Ok(
                ClientMessage::FileQuery { lane, request, .. }
                | ClientMessage::Appearances { lane, request, .. }
                | ClientMessage::EventQuery(EventQueryWire { lane, request, .. })
                | ClientMessage::Event(EventAskWire { lane, request, .. }),
            )) => self.notify(&ServerMessage::File {
                lane,
                request,
                answer: FileAnswer::Unavailable {
                    unavailable: FileScopeUnavailable::PlainLane,
                },
            }),
            Some(Ok(ClientMessage::Open { .. })) => Err(violation("The video is already open")),
            Some(Ok(
                ClientMessage::Want(_)
                | ClientMessage::Prepare { .. }
                | ClientMessage::Export { .. }
                | ClientMessage::CancelExport { .. }
                | ClientMessage::FileRelease { .. },
            )) => Ok(()),
            Some(Err(error)) => Err(End::Failed(error)),
            Some(Ok(ClientMessage::Close)) | None => Err(End::Closed),
        }
    }

    /// Queues an answer for the viewer, or ends the session when too many wait for one that is not reading.
    fn notify(&mut self, message: &ServerMessage) -> Flow<()> {
        if self.notices.len() >= MAX_NOTICES {
            return Err(End::Failed(UserError::new(
                ErrorCode::ProtocolViolation,
                "The viewer asks faster than it reads the answers",
            )));
        }
        self.notices.push_back(text(message));
        Ok(())
    }

    /// Sends `message` once the outbox has room, while still hearing from the viewer and the backend.
    async fn send(&mut self, message: Message) -> Flow<()> {
        loop {
            tokio::select! {
                reserved = self.outbox.reserve() => {
                    reserved.map_err(|_| End::Closed)?.send(Outgoing::from(message));
                    return Ok(());
                }
                client = next_client(self.incoming) => self.apply(client)?,
                () = stopped(self.closing) => return Err(End::Closed),
                () = self.gate.revoked() => return Err(End::Revoked),
            }
        }
    }

    /// Tells the viewer the lane's status when it has not been told it, or it changed.
    async fn ready(&mut self) -> Flow<()> {
        let Some(ready) = self.viewer.lane_ready() else {
            return Ok(());
        };
        let key = ready_key(&ready);
        if self.told.as_ref() == Some(&key) {
            return Ok(());
        }
        self.told = Some(key);
        let status = status_wire(LaneStatus::Ready(ready), self.audience);
        self.send(text(&ServerMessage::LaneStatus { lane: 0, status }))
            .await
    }

    /// A frame with what its lane selects. A frame that cannot be compressed is left out as a dropped frame would be;
    /// its samples and map are still sent.
    async fn show(&mut self, shown: Shown) -> Flow<()> {
        self.ready().await?;
        let Shown { frame, out } = shown;
        let frame = match self.wire.message(&frame, false).await {
            Ok(message) => Some(message),
            Err(error) => {
                // A stream whose every frame fails would flood the log: the first is a warning, the rest are debug.
                self.failures += 1;
                if self.failures == 1 {
                    tracing::warn!("Live frame skipped: {}", error.reason);
                } else {
                    tracing::debug!("Live frame skipped: {}", error.reason);
                }
                None
            }
        };
        if let Some(out) = &out {
            for item in &out.items {
                self.send(Message::Binary(envelope::sample(item))).await?;
            }
        }
        if let Some(frame) = frame {
            self.send(frame).await?;
        }
        let Some(out) = out else {
            return Ok(());
        };
        self.send(Message::Binary(envelope::frame_map(&out.map)))
            .await?;
        if !out.evict.is_empty() {
            self.send(text(&ServerMessage::Evict {
                source: 0,
                data_revision: out.map.data_revision,
                samples: out.evict,
            }))
            .await?;
        }
        match out.events {
            Some(events) => self.events(events).await,
            None => Ok(()),
        }
    }

    /// The events a unit made visible.
    async fn events(&mut self, events: LiveEvents) -> Flow<()> {
        self.send(text(&ServerMessage::LiveEvents(LiveEventsWire {
            frame: events.frame.sequence_id,
            samples: events
                .samples
                .iter()
                .map(|sample| LiveSampleEventsWire {
                    time_us: sample.time.as_us(),
                    events: sample.events.iter().map(LiveEventWire::from).collect(),
                })
                .collect(),
        })))
        .await
    }

    /// Applies the viewer's messages and sends what the feed produces until the session ends.
    async fn pump(&mut self) -> Flow<()> {
        loop {
            if let Some(notice) = self.notices.pop_front() {
                self.send(notice).await?;
                continue;
            }
            if std::mem::take(&mut self.retry) {
                self.viewer.retry();
            }
            tokio::select! {
                message = next_client(self.incoming) => self.apply(message)?,
                update = self.viewer.next() => match update {
                    Some(Update::Frame(shown)) => self.show(*shown).await?,
                    Some(Update::Events(events)) => self.events(*events).await?,
                    Some(Update::Status(status)) => {
                        let status = self.viewer.project_status(status, self.audience);
                        self.send(text(&ServerMessage::LiveStatus(status))).await?;
                    }
                    Some(Update::Gap { time_us }) => {
                        self.send(text(&ServerMessage::LiveGap(LiveGapWire { time_us }))).await?;
                    }
                    None => return Err(End::Closed),
                },
                // A follower is shown the stream as the presenter has it: a pause of its own ends.
                () = self.gate.changed() => {
                    if self.gate.is_revoked() {
                        return Err(End::Revoked);
                    }
                    if !self.gate.is_own() {
                        self.viewer.pause(false);
                    }
                }
                message = self.catalogs.next() => self.send_outgoing(message).await?,
                message = self.inspections.next() => {
                    // A live frame has no place on a timeline for the gate to judge.
                    self.send_outgoing(Outgoing { frames: None, ..message }).await?;
                }
                () = stopped(self.closing) => return Err(End::Closed),
            }
        }
    }

    async fn send_outgoing(&mut self, outgoing: Outgoing) -> Flow<()> {
        self.send(outgoing.message).await
    }
}

#[cfg(test)]
mod tests;
