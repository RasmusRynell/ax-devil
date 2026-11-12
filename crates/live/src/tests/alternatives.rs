//! Host-side alternatives never enter the shared status or a watcher's projection.
use super::*;
use api_types::Audience;

#[tokio::test]
async fn unavailable_sources_are_projected_for_each_viewers_rights() {
    let rig = rig();
    let mut owner = rig.open(&content("camera.local", mqtt("missing#2"))).await;
    let watcher = rig.open(&content("camera.local", mqtt("missing#2"))).await;
    let wire = rig.fake.messages()[0].clone();
    let mut error = CameraError::fake(ErrorKind::SourceUnavailable, "");
    error.source_alternatives = Some(((0..8).map(|n| format!("topic{n}#2")).collect(), 3));
    wire.status.send(Status::Failed(error)).unwrap();
    loop {
        if let Update::Status(status) = update(&mut owner).await
            && matches!(
                status.overlay,
                Some(LiveFeedStatus::Failed {
                    reason: LiveReason::SourceUnavailable
                })
            )
        {
            assert_eq!(status.alternatives, None);
            assert_eq!(
                owner
                    .project_status(status.clone(), Audience::HOST)
                    .alternatives
                    .as_deref(),
                Some(
                    "topic0#2, topic1#2, topic2#2, topic3#2, topic4#2, topic5#2, topic6#2, topic7#2 (+3 more)"
                )
            );
            let projected = watcher.project_status(
                status,
                Audience {
                    host_details: false,
                },
            );
            assert_eq!(projected.alternatives, None);
            break;
        }
    }
    drop((owner, watcher));
    rig.sources.shutdown(Duration::from_secs(2)).await;
}
