//! Capability identity and held-sample lifetime through real viewer overlays.
use super::*;

#[tokio::test]
async fn a_cursor_cannot_cross_sockets_or_outlast_its_held_sample() {
    let folder = tempfile::tempdir().unwrap();
    let (mut viewer, revision) = viewer(folder.path()).await;
    hold(&mut viewer, 10).await;
    let mut inspections = Inspections::default();
    inspections.ask(&viewer, 0, 0, revision, 10, "2".into());
    let (message, _) = answer(&mut inspections).await;
    let InspectionWire::Found {
        continuation: Some(cursor),
        ..
    } = verdict(message)
    else {
        panic!("cursor");
    };
    let read = InspectionRead {
        pins: None,
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
    assert!(position.lock().unwrap().scene.is_some());
    inspections.release();
    assert!(
        position.lock().unwrap().traversal.is_none(),
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
    inspections.ask(&viewer, 0, 0, revision, 10, "2".into());
    let InspectionWire::Found {
        continuation: Some(cursor),
        ..
    } = verdict(answer(&mut inspections).await.0)
    else {
        panic!("fresh capability");
    };
    let read = InspectionRead {
        pins: None,
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
