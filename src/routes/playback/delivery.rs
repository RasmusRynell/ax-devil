//! The message for a frame or failure the session delivers.

use super::{End, Flow, Outgoing, text, wire::Wire};
use api_types::{Audience, playback::ServerMessage, project};
use foundation::{FrameFailure, HostPath};
use playback::Delivery;

/// The message for something the session delivers. A source that cannot decode at all ends the session; a frame that
/// cannot be decoded or compressed for the viewer fails alone, as a local viewer would see it.
pub(super) async fn encode(
    delivery: Delivery,
    wire: Wire,
    last: u64,
    location: &HostPath,
    audience: Audience,
    failures: &mut u32,
) -> Flow<Outgoing> {
    let failure = match delivery {
        Delivery::Frame(frame) => {
            let sequence_id = frame.id().sequence_id;
            match wire.message(&frame, sequence_id == last).await {
                Ok(message) => return Ok(Outgoing::frame(message, sequence_id)),
                Err(error) => FrameFailure {
                    sequence_id,
                    error: error.at(location.clone()),
                },
            }
        }
        Delivery::Failure(failure) => failure,
        Delivery::Failed(mut error) => {
            if error.location.is_none() {
                error.location = Some(location.clone().into());
            }
            return Err(End::Failed(error));
        }
    };
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

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::{ErrorCode, Location, UserError};

    async fn ended(error: UserError) -> UserError {
        let delivery = Delivery::Failed(error);
        let location = HostPath::new("/clips/a.mp4");
        match encode(delivery, Wire::Raw, 9, &location, Audience::HOST, &mut 0).await {
            Err(End::Failed(error)) => error,
            _ => panic!("a failed source must end the session"),
        }
    }

    #[tokio::test]
    async fn a_source_failure_ends_the_session_naming_the_file_unless_the_failure_already_names_one()
     {
        let at = |path| Some(Location::Host(HostPath::new(path)));
        assert_eq!(
            ended(UserError::new(ErrorCode::DecodeFailed, "no decoder"))
                .await
                .location,
            at("/clips/a.mp4")
        );
        assert_eq!(
            ended(
                UserError::new(ErrorCode::DecodeFailed, "no decoder")
                    .at(HostPath::new("/clips/b.mp4"))
            )
            .await
            .location,
            at("/clips/b.mp4")
        );
    }
}
