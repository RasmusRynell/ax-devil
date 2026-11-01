//! The streaming half of a playback session: once the video is open, tell the viewer about it, then apply its windows
//! and send the frames in them.

use super::{
    End, Flow, INITIAL_AHEAD, INITIAL_BEHIND, Incoming, Outbox, Outgoing, apply, envelope,
    gate::Gate, next_client, send_watching, stopped, text,
};
use api_types::{
    Audience,
    playback::{Direction as WireDirection, PLAYBACK_PROTOCOL, ServerMessage, Want as WireWant},
    project,
};
use axum::extract::ws::Message;
use foundation::HostPath;
use playback::{Delivery, Direction, Priority, Session, Want};
use std::time::Duration;
use tokio::{sync::watch, time::MissedTickBehavior};

/// How often a stream judges again what its viewer may have, so a follower's window moves with the presenter's clock.
const REJUDGE: Duration = Duration::from_millis(100);

/// What the stream knows of the viewer that opened it.
pub(super) struct Viewer {
    /// The video's file, which the errors that end the session name when the layer that failed did not.
    pub(super) location: HostPath,
    /// The frame the viewer opened on.
    pub(super) start_frame: Option<u64>,
    /// What the viewer may learn from an error.
    pub(super) audience: Audience,
}

/// Tells the viewer about the video, then applies its windows and sends what it has room for until it leaves.
pub(super) async fn stream(
    session: &mut Session,
    viewer: &Viewer,
    incoming: &mut impl Incoming,
    outbox: &Outbox,
    closing: &mut watch::Receiver<bool>,
    mut gate: Gate,
) -> Flow<()> {
    // What the viewer is told comes from the source it joined, which can be older than this open.
    let spec = session.spec();
    let timeline = spec.timeline.clone();
    let last = timeline.last();
    // Decoding starts at the first frame the viewer shows while the messages below are on their way.
    gate.open(timeline.clone());
    gate.ask(
        session,
        Want {
            playhead: viewer.start_frame.unwrap_or(0).min(last),
            ahead: INITIAL_AHEAD,
            behind: INITIAL_BEHIND,
            direction: Direction::Forward,
            priority: Priority::default(),
        },
    )?;
    let opened = text(&ServerMessage::Opened {
        protocol: PLAYBACK_PROTOCOL,
        frame_count: timeline.len(),
        duration_us: timeline.duration().as_us(),
        frame_bytes: session.spec().frame_bytes as u64,
    });
    send_watching(outbox, opened, session, &mut gate, incoming, closing).await?;
    let times = Message::Binary(envelope::timeline(&timeline));
    send_watching(outbox, times, session, &mut gate, incoming, closing).await?;

    let mut room = None;
    let mut failures = 0_u32;
    // A presenter's clock moves without a message, so what the viewer may have is judged again as it does.
    let mut clock = tokio::time::interval(REJUDGE);
    clock.set_missed_tick_behavior(MissedTickBehavior::Skip);
    loop {
        // Before anything is delivered, a change of authority narrows what the session is asked for.
        if gate.has_changed() {
            gate.refresh(session)?;
        }
        tokio::select! {
            message = next_client(incoming) => apply(message, session, &mut gate)?,
            // Room in the outbox is taken before a frame is, so the frame sent is the best one at that moment.
            reserved = outbox.reserve(), if room.is_none() => {
                room = Some(reserved.map_err(|_| End::Closed)?);
            }
            delivery = session.next_delivery(), if room.is_some() => {
                let (Some(delivery), Some(room)) = (delivery, room.take()) else {
                    return Err(End::Closed);
                };
                room.send(encode(
                    delivery,
                    last,
                    &viewer.location,
                    viewer.audience,
                    &mut failures,
                )?);
            }
            () = stopped(closing) => return Err(End::Closed),
            () = gate.changed() => gate.refresh(session)?,
            _ = clock.tick() => gate.refresh(session)?,
        }
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
            Ok(Outgoing {
                message: Message::Binary(envelope::video_frame(&frame, sequence_id == last)),
                frame: Some(sequence_id),
            })
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
            Ok(Outgoing {
                message: text(&ServerMessage::FrameError {
                    sequence_id: failure.sequence_id,
                    error: project(&failure.error, audience),
                }),
                frame: Some(failure.sequence_id),
            })
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
    use foundation::{ErrorCode, Location, UserError};

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
