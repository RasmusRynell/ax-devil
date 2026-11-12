//! The answers to `Inspect`, through real overlays: what each state carries, what a viewer that no longer reaches the
//! sample is not sent, and which request is answered when several arrive.

use super::*;
use crate::routes::playback::gate::Gate;
use api_types::playback::ServerMessage;
use axum::extract::ws::Message;
use foundation::{MediaTime, Timeline};
use overlays::{LaneStatus, OverlayEvent, OverlayService, SampleItem};
use playback::{Authority, ClockIntent, Direction, Priority, Want};
use scene::inspection::{FieldGroup, Leaf};
use std::{path::Path, time::Duration};
use tokio::sync::watch;
use workspace::OverlayRef;

/// A tenth of the viewer's default 512 MiB frame budget.
const SAMPLE_BUDGET: u64 = 53_687_091;

fn timeline() -> Timeline {
    Timeline::new((0..90).map(|n| MediaTime::from_us(n * 33_333)).collect()).unwrap()
}

/// A viewer of a MOT file with objects 1 and 2 on every one of 90 frames, ready, with its lane drawing.
async fn viewer(folder: &Path) -> (ViewerOverlays, u32) {
    let gt = folder.join("gt.txt");
    let rows: String = (1..=90)
        .flat_map(|frame| (1..=2).map(move |id| format!("{frame},{id},10,10,20,20,1,1,1\n")))
        .collect();
    std::fs::write(&gt, rows).unwrap();
    let overlay = OverlayRef {
        label: "GT".into(),
        slot: "GT".into(),
        path: gt,
        handler: "MOT_FILE".into(),
        options: handler_api::Settings::from_iter([
            ("width".to_owned(), 64.into()),
            ("height".to_owned(), 48.into()),
        ]),
        metadata: Default::default(),
    };
    open_overlay(overlay).await
}

async fn open_overlay(overlay: OverlayRef) -> (ViewerOverlays, u32) {
    let service = OverlayService::new(scene_store::TrackRegistry::new(
        handlers::Handlers::built_in().decoder_lookup(),
        None,
    ));
    let mut viewer = service.open_viewer(vec![Some(overlay)], timeline(), SAMPLE_BUDGET);
    loop {
        match event(&mut viewer).await {
            OverlayEvent::Status {
                status: LaneStatus::Ready(ready),
                ..
            } => {
                let policy = overlay_sync::MatchPolicy::default();
                viewer.set_policy(0, 1, Some(policy), true);
                return (viewer, ready.data_revision);
            }
            _ => continue,
        }
    }
}

async fn event(viewer: &mut ViewerOverlays) -> OverlayEvent {
    tokio::time::timeout(Duration::from_secs(20), viewer.next_event(None))
        .await
        .expect("an event in time")
        .expect("the viewer is open")
}

fn around(playhead: u64) -> Want {
    Want {
        playhead,
        ahead: 2,
        behind: 2,
        direction: Direction::Forward,
        priority: Priority::default(),
    }
}

/// Moves the data window and reads until the samples of `frames` were sent.
async fn hold(viewer: &mut ViewerOverlays, playhead: u64) -> Vec<SampleItem> {
    viewer.want(around(playhead));
    let mut held = Vec::new();
    while held.len() < 5 {
        if let OverlayEvent::Sample(item) = event(viewer).await {
            held.push(item);
        }
    }
    held
}

async fn answer(
    inspections: &mut Inspections,
) -> (ServerMessage, Option<std::ops::RangeInclusive<u64>>) {
    let outgoing = tokio::time::timeout(Duration::from_secs(20), inspections.next())
        .await
        .expect("an answer in time");
    let Message::Text(body) = outgoing.message else {
        panic!("a text message");
    };
    (serde_json::from_str(&body).unwrap(), outgoing.frames)
}

fn verdict(message: ServerMessage) -> InspectionWire {
    let ServerMessage::Inspected { inspection, .. } = message else {
        panic!("an Inspected message");
    };
    inspection
}

#[tokio::test]
async fn a_held_sample_is_found_or_absent_and_anything_else_is_unavailable_without_a_frame() {
    let folder = tempfile::tempdir().unwrap();
    let (mut viewer, revision) = viewer(folder.path()).await;
    let held = hold(&mut viewer, 10).await;
    let item = held.iter().find(|item| item.sample == 10).unwrap();
    let mut inspections = Inspections::default();

    inspections.ask(&viewer, 0, revision, 10, "2".into());
    let (message, frames) = answer(&mut inspections).await;
    let ServerMessage::Inspected {
        source,
        data_revision,
        sample,
        entity,
        inspection,
    } = message
    else {
        panic!("an Inspected message");
    };
    assert_eq!(
        (source, data_revision, sample, entity.as_str()),
        (0, revision, 10, "2")
    );
    assert_eq!(
        frames,
        Some(item.frame..=item.frame),
        "gated like the sample"
    );
    let InspectionWire::Found {
        fields, truncated, ..
    } = inspection
    else {
        panic!("found");
    };
    assert!(!truncated);
    let at = |group: FieldGroup, name: &str| {
        fields
            .iter()
            .find(|field| field.group == group && field.path == [name])
            .map(|field| field.value.clone())
    };
    assert_eq!(
        at(FieldGroup::Geometry, "width"),
        Some(Leaf::Float(20.0 / 64.0))
    );
    assert_eq!(at(FieldGroup::Raw, "frame_number"), Some(Leaf::Int(10)));
    let groups: Vec<_> = fields.iter().map(|field| field.group as u8).collect();
    assert!(groups.is_sorted(), "in display order");

    inspections.ask(&viewer, 0, revision, 10, "nobody".into());
    let (message, frames) = answer(&mut inspections).await;
    assert_eq!(verdict(message), InspectionWire::Absent);
    assert_eq!(frames, Some(item.frame..=item.frame));

    let unavailable = [
        (0, revision, 80, "2".to_owned()),
        (0, revision + 1, 10, "2".to_owned()),
        (3, revision, 10, "2".to_owned()),
        (0, revision, 10, "x".repeat(MAX_ENTITY_BYTES + 1)),
    ];
    for (source, data_revision, sample, entity) in unavailable {
        inspections.ask(&viewer, source, data_revision, sample, entity);
        let (message, frames) = answer(&mut inspections).await;
        assert_eq!(verdict(message), InspectionWire::Unavailable);
        assert_eq!(frames, None, "it says nothing about a sample");
    }
}

#[tokio::test]
async fn a_search_of_the_fields_is_answered_for_every_object_and_only_for_a_held_sample() {
    let folder = tempfile::tempdir().unwrap();
    let (mut viewer, revision) = viewer(folder.path()).await;
    let held = hold(&mut viewer, 10).await;
    let item = held.iter().find(|item| item.sample == 10).unwrap();
    let mut inspections = Inspections::default();
    let objects = |message| {
        let ServerMessage::InspectedFields { objects, .. } = message else {
            panic!("an InspectedFields message");
        };
        objects
    };

    // A path names a key by its trailing parts, in any case.
    inspections.ask_fields(&viewer, 0, revision, 10, vec!["Frame_Number".into()]);
    let (message, frames) = answer(&mut inspections).await;
    assert_eq!(frames, Some(item.frame..=item.frame));
    let found = objects(message).expect("a held sample");
    let leaves: Vec<_> = found
        .iter()
        .map(|object| {
            let items = object
                .items
                .iter()
                .map(|item| (item.key.as_str(), &item.value));
            (object.entity.as_str(), items.collect::<Vec<_>>())
        })
        .collect();
    assert_eq!(
        leaves,
        [
            ("1", vec![("raw.frame_number", &Leaf::Int(10))]),
            ("2", vec![("raw.frame_number", &Leaf::Int(10))]),
        ]
    );

    // Asking for an object's fields does not replace a search of them: both are answered.
    inspections.ask_fields(&viewer, 0, revision, 10, vec!["no_such_field".into()]);
    inspections.ask(&viewer, 0, revision, 10, "1".into());
    let mut kinds = Vec::new();
    for _ in 0..2 {
        match answer(&mut inspections).await.0 {
            ServerMessage::InspectedFields { objects, .. } => {
                assert_eq!(objects, Some(Vec::new()));
                kinds.push("fields");
            }
            ServerMessage::Inspected { .. } => kinds.push("object"),
            other => panic!("unexpected {other:?}"),
        }
    }
    kinds.sort_unstable();
    assert_eq!(kinds, ["fields", "object"]);

    let unanswerable = [
        (0, revision, 80, vec!["x".to_owned()]),
        (0, revision, 10, vec!["x".to_owned(); MAX_PATHS + 1]),
        (0, revision, 10, vec!["x".repeat(MAX_PATH_BYTES + 1)]),
    ];
    for (source, data_revision, sample, paths) in unanswerable {
        inspections.ask_fields(&viewer, source, data_revision, sample, paths);
        let (message, frames) = answer(&mut inspections).await;
        assert_eq!(objects(message), None);
        assert_eq!(frames, None, "it says nothing about a sample");
    }
}

#[tokio::test]
async fn the_newest_request_replaces_one_that_has_not_started_and_only_one_runs_at_a_time() {
    let folder = tempfile::tempdir().unwrap();
    let (mut viewer, revision) = viewer(folder.path()).await;
    hold(&mut viewer, 10).await;
    let mut inspections = Inspections::default();

    for entity in ["1", "nobody", "2"] {
        inspections.ask(&viewer, 0, revision, 10, entity.into());
    }

    let mut answered = Vec::new();
    for _ in 0..2 {
        let (message, _) = answer(&mut inspections).await;
        let ServerMessage::Inspected { entity, .. } = message else {
            panic!("an Inspected message");
        };
        answered.push(entity);
    }
    assert_eq!(answered, ["1", "2"], "the one running, then the newest");
    assert!(
        tokio::time::timeout(Duration::from_millis(200), inspections.next())
            .await
            .is_err(),
        "the replaced request is never answered"
    );
}

#[tokio::test]
async fn a_follower_is_not_sent_what_a_presenter_who_moved_on_no_longer_shows() {
    let folder = tempfile::tempdir().unwrap();
    let (mut viewer, revision) = viewer(folder.path()).await;
    let held = hold(&mut viewer, 40).await;
    assert!(held.iter().any(|item| item.sample == 40));
    hold(&mut viewer, 10).await;
    let mut inspections = Inspections::default();
    let (_presenter, authority) = watch::channel(Authority::Follow {
        intent: ClockIntent {
            frame: 10,
            playing: false,
            speed_centi: 100,
            at_us: 0,
        },
    });
    let gate = Gate::new(authority, || 0);
    gate.open(timeline());

    inspections.ask(&viewer, 0, revision, 10, "1".into());
    let (_, near) = answer(&mut inspections).await;
    assert!(
        gate.admits(near.as_ref().unwrap()),
        "the presenter's own frame goes"
    );

    // Sample 40 was evicted when the window moved: the viewer no longer holds it, so there is nothing to say.
    inspections.ask(&viewer, 0, revision, 40, "1".into());
    let (message, far) = answer(&mut inspections).await;
    assert_eq!(verdict(message), InspectionWire::Unavailable);
    assert!(far.is_none_or(|frames| !gate.admits(&frames)));
}

#[tokio::test]
async fn a_cursor_cannot_cross_sockets_or_outlast_its_held_sample() {
    let folder = tempfile::tempdir().unwrap();
    let (mut viewer, revision) = viewer(folder.path()).await;
    hold(&mut viewer, 10).await;
    let mut inspections = Inspections::default();
    inspections.ask(&viewer, 0, revision, 10, "2".into());
    let (message, _) = answer(&mut inspections).await;
    let InspectionWire::Found {
        continuation: Some(cursor),
        ..
    } = verdict(message)
    else {
        panic!("cursor");
    };
    let read = InspectionRead {
        raw: None,
        path: false,
        token: cursor.token,
        request: 1,
        field: None,
        offset: 0,
    };
    let mut other_seat = Inspections::default();
    other_seat.continue_target(read.clone(), viewer.inspect_target(0, revision, 10));
    let (message, frames) = answer(&mut other_seat).await;
    assert!(matches!(
        message,
        ServerMessage::InspectContinued {
            result: InspectionReadResult::Refused { .. },
            ..
        }
    ));
    assert_eq!(frames, None);
    inspections.continue_target(read.clone(), viewer.inspect_target(0, revision, 10));
    let (message, frames) = answer(&mut inspections).await;
    assert!(matches!(
        message,
        ServerMessage::InspectContinued {
            result: InspectionReadResult::Page { .. },
            ..
        }
    ));
    assert!(frames.is_some());
    let position = inspections.cursor.as_ref().unwrap().5.clone();
    let cached = Arc::downgrade(position.lock().unwrap().traversal.as_ref().unwrap());
    assert!(position.lock().unwrap().scene.is_some());
    inspections.release();
    assert!(
        cached.upgrade().is_none(),
        "releasing the capability releases its prepared permutation"
    );
    assert!(
        position.lock().unwrap().scene.is_none(),
        "released input reference"
    );
    inspections.continue_target(read.clone(), viewer.inspect_target(0, revision, 10));
    assert!(matches!(
        answer(&mut inspections).await.0,
        ServerMessage::InspectContinued {
            result: InspectionReadResult::Refused { .. },
            ..
        }
    ));
    inspections.ask(&viewer, 0, revision, 10, "2".into());
    let InspectionWire::Found {
        continuation: Some(cursor),
        ..
    } = verdict(answer(&mut inspections).await.0)
    else {
        panic!("fresh capability");
    };
    let read = InspectionRead {
        token: cursor.token,
        ..read
    };
    hold(&mut viewer, 40).await;
    inspections.continue_target(read, viewer.inspect_target(0, revision, 10));
    let (message, frames) = answer(&mut inspections).await;
    let ServerMessage::InspectContinued {
        result: InspectionReadResult::Refused { reason },
        ..
    } = message
    else {
        panic!("stale cursor refused");
    };
    assert_eq!(reason, "Inspection expired; select the object again");
    assert_eq!(frames, None);
}

#[test]
fn an_abbreviated_path_advertises_how_to_retrieve_its_last_byte() {
    use scene::{
        BoundingBox, Entity, EntityId, Geometry, Name, Observation, SceneTime, TimeSlice, Value,
    };

    let time = SceneTime::Frame(0);
    let path = "p".repeat(9 * 1024 * 1024);
    let observation = Observation::new(
        time,
        Geometry::Box(BoundingBox::new(0.0, 0.0, 1.0, 1.0).unwrap()),
    )
    .with_details(vec![(Name::Owned(path), Value::Int(1))], Vec::new())
    .unwrap();
    let mut entity = Entity::new(EntityId::from(7_i64));
    entity.push_observation(observation);
    let scene = Scene::new(TimeSlice::at(time))
        .with_entities(vec![entity])
        .unwrap();
    let InspectionWire::Found { fields, .. } = inspection(&scene, "7", "held") else {
        panic!("inspection");
    };
    let [ordinal, bytes] = fields
        .iter()
        .find_map(|field| field.path_cut)
        .expect("full path access");
    assert_eq!(bytes, 9 * 1024 * 1024);
    let read = InspectionRead {
        raw: None,
        token: "held".into(),
        request: 1,
        field: Some(ordinal as u32),
        path: true,
        offset: (bytes - 1) as u32,
    };
    let InspectionReadResult::Chunk { text, total, next } = continued(Some(&scene), "7", &read)
    else {
        panic!("path chunk");
    };
    assert_eq!(text, "p");
    assert_eq!(u64::from(total), bytes);
    assert_eq!(next, None);
}

mod permission;
