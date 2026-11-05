//! `Inspect` over the real playback socket: the fields of an object of a sample the viewer holds, and what is said
//! when the object, the sample, the revision or the source is not there.

mod common;

use api_types::{
    inspect::InspectionWire,
    playback::{
        ClientMessage, Direction, LanePolicyWire, LaneStatusWire, ServerMessage, StickyWire,
    },
};
use common::{Backend, Event, Viewer, fixture};
use scene::inspection::{FieldGroup, Leaf};

/// A video whose one lane holds object 1 on frames 0 to 2 and object 2 on frame 1, with the lane's three samples
/// sent. Returns the viewer and the source's data revision.
async fn viewer_holding_three_samples(backend: &Backend) -> (Viewer, u32) {
    let gt = backend.mot_file("1,1,10,10,20,20,1,1,1\n2,1,12,10,20,20,1,1,1\n2,2,40,10,20,20,1,1,1\n3,1,14,10,20,20,1,1,1\n");
    let id = backend
        .add_with_overlays(&fixture("cfr_h264_gop10_bf2.mp4"), &[gt])
        .await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    viewer.opened().await;
    viewer.want(0, 5, 0, Direction::Forward).await;
    viewer
        .send(&ClientMessage::LanePolicy {
            lane: 0,
            revision: 1,
            policy: LanePolicyWire {
                basis: None,
                tolerance_ms: None,
                sticky: StickyWire {
                    enabled: false,
                    timeout_ms: None,
                },
            },
            subscribed: true,
        })
        .await;
    let (mut revision, mut samples) = (0, 0);
    while samples < 3 {
        match viewer.next().await {
            Event::Message(ServerMessage::LaneStatus {
                status: LaneStatusWire::Ready { data_revision, .. },
                ..
            }) => revision = data_revision,
            Event::Sample(_) => samples += 1,
            _ => {}
        }
    }
    (viewer, revision)
}

/// Asks about an object and reads until the answer.
async fn inspect(
    viewer: &mut Viewer,
    source: u16,
    data_revision: u32,
    sample: u32,
    entity: &str,
) -> InspectionWire {
    viewer
        .send(&ClientMessage::Inspect {
            source,
            data_revision,
            sample,
            entity: entity.into(),
        })
        .await;
    loop {
        if let Event::Message(ServerMessage::Inspected {
            source: s,
            data_revision: r,
            sample: n,
            entity: e,
            inspection,
        }) = viewer.next().await
        {
            assert_eq!(
                (s, r, n, e.as_str()),
                (source, data_revision, sample, entity)
            );
            return inspection;
        }
    }
}

#[tokio::test]
async fn an_object_of_a_held_sample_is_found_with_its_fields_and_a_missing_one_is_absent() {
    let backend = Backend::start().await;
    let (mut viewer, revision) = viewer_holding_three_samples(&backend).await;

    let InspectionWire::Found { fields, truncated } =
        inspect(&mut viewer, 0, revision, 1, "2").await
    else {
        panic!("object 2 is in sample 1");
    };
    assert!(!truncated);
    let value = |group, name: &str| {
        fields
            .iter()
            .find(|field| field.group == group && field.path == [name])
            .map(|field| field.value.clone())
    };
    assert!(value(FieldGroup::Geometry, "x").is_some());
    assert_eq!(value(FieldGroup::Raw, "frame_number"), Some(Leaf::Int(1)));
    let order: Vec<_> = fields.iter().map(|field| field.group as u8).collect();
    assert!(order.is_sorted(), "groups are in display order");
    assert!(
        fields
            .iter()
            .all(|field| field.origin.is_none() || field.group == FieldGroup::Debug),
        "only Debug fields have an origin"
    );

    assert_eq!(
        inspect(&mut viewer, 0, revision, 0, "2").await,
        InspectionWire::Absent,
        "object 2 is not in sample 0"
    );
}

#[tokio::test]
async fn a_sample_not_held_under_this_revision_or_a_source_that_is_not_there_is_unavailable() {
    let backend = Backend::start().await;
    let (mut viewer, revision) = viewer_holding_three_samples(&backend).await;

    for (source, data_revision, sample, entity) in [
        (0, revision, 2_000, "1"),
        (0, revision + 1, 0, "1"),
        (7, revision, 0, "1"),
        (0, revision, 0, &"x".repeat(1025)),
    ] {
        assert_eq!(
            inspect(&mut viewer, source, data_revision, sample, entity).await,
            InspectionWire::Unavailable,
            "{source} {data_revision} {sample}"
        );
    }
    // The session goes on.
    viewer.want(1, 1, 0, Direction::Forward).await;
}
