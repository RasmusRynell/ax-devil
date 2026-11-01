//! Changing a lane's view, and what the lookup gives for frames without a sample.

use catalogs::CatalogId;
use overlay_sync::{MatchKind, Selection, Sticky};

use super::{Env, SIZE, next, overlays, places, quiet, until_ready, want};
use crate::{LaneView, OverlayEvent, prepare_lane_frame, tests::timeline};

#[tokio::test]
async fn a_new_revision_resends_that_lane_and_only_that_lane() {
    let env = Env::new();
    let lanes = vec![Some(env.mot("a.txt", 0..10)), Some(env.mot("b.txt", 0..10))];
    let mut viewer = env.open(lanes, 10);
    until_ready(&mut viewer, &[0, 1]).await;
    viewer.want(want(4, 1, 1));
    let (found, _) = overlays(&mut viewer, 6).await;
    assert!(found.iter().all(|overlay| overlay.revision == 0));

    let mut view = env.service.lane_defaults();
    view.filter
        .overrides
        .insert("show_pedestrian".into(), false);
    viewer.set_view(1, 7, view);

    let (found, _) = overlays(&mut viewer, 3).await;
    assert_eq!(places(&found), [(1, 4), (1, 5), (1, 3)]);
    assert!(found.iter().all(|overlay| overlay.revision == 7));
    assert!(quiet(&mut viewer).await, "lane 0 keeps what it sent");
}

#[tokio::test]
async fn a_catalog_that_fails_keeps_the_last_one_and_reports_a_problem() {
    let env = Env::new();
    let mut viewer = env.open(vec![Some(env.mot("a.txt", 0..10))], 10);
    until_ready(&mut viewer, &[0]).await;
    viewer.want(want(4, 0, 1));
    overlays(&mut viewer, 2).await;
    let standard = CatalogId::BuiltIn("standard");
    assert_eq!(viewer.loaded_catalog(0), Some(standard.clone()));

    let mut view = env.service.lane_defaults();
    view.catalog = CatalogId::User("bad".into());
    viewer.set_view(0, 1, view);

    let OverlayEvent::Problem { lane: 0, reason } = next(&mut viewer).await else {
        panic!("the problem comes before the lane's next overlays");
    };
    assert!(reason.contains("The catalog is damaged"), "{reason}");
    assert!(reason.contains("bad"), "{reason}");
    let (found, others) = overlays(&mut viewer, 2).await;
    assert_eq!(places(&found), [(0, 4), (0, 5)], "the lane still draws");
    assert!(found.iter().all(|overlay| overlay.revision == 1));
    assert!(others.is_empty(), "the lane stays ready: {others:?}");
    assert_eq!(viewer.loaded_catalog(0), Some(standard));

    // The same bad catalog is not tried, or reported, again.
    let mut view = env.service.lane_defaults();
    view.catalog = CatalogId::User("bad".into());
    view.visible = true;
    viewer.set_view(0, 2, view);
    overlays(&mut viewer, 2).await;
    let asked = env.catalogs.asked.lock().expect("asked").clone();
    assert_eq!(
        asked
            .iter()
            .filter(|id| **id == CatalogId::User("bad".into()))
            .count(),
        1
    );
}

#[tokio::test]
async fn a_dense_file_covers_a_frame_without_rows_with_nothing() {
    let env = Env::new();
    // Rows on frames 0 and 2 only.
    let mut viewer = env.open(vec![Some(env.mot("a.txt", [0, 2]))], 4);
    until_ready(&mut viewer, &[0]).await;
    viewer.want(want(1, 1, 2));

    let (found, _) = overlays(&mut viewer, 4).await;
    let at = |frame: u64| {
        found
            .iter()
            .find(|overlay| overlay.frame.sequence_id == frame)
            .expect("every frame of the window")
    };
    assert_eq!(at(1).selection, Selection::Empty);
    assert!(at(1).drawing.is_empty());
    assert_eq!(at(3).selection, Selection::Empty);
    assert_eq!((at(1).key, at(2).key), (0, 2), "a frame key; none is 0");
    assert!(matches!(
        at(0).selection,
        Selection::Sample {
            kind: MatchKind::Exact,
            ..
        }
    ));
}

#[tokio::test]
async fn a_kept_sample_carries_the_sticky_opacity() {
    let env = Env::new();
    // Two records at 0 s and 1 s; the video runs at 30 fps.
    let text = [0, 1]
        .map(|second| format!(
            "<tt:Frame xmlns:tt=\"http://www.onvif.org/ver10/schema\" UtcTime=\"1970-01-01T00:00:0{second}Z\">\
             <tt:Object ObjectId=\"o\"><tt:Appearance><tt:Class><tt:Type Likelihood=\"0.9\">person</tt:Type></tt:Class>\
             <tt:Shape><tt:BoundingBox left=\"-0.2\" top=\"0.2\" right=\"0.2\" bottom=\"-0.2\" /></tt:Shape></tt:Appearance>\
             </tt:Object></tt:Frame>\n"
        ))
        .concat();
    let overlay = env.overlay("onvif.xml", "ONVIF_XML", &text);
    let mut viewer = env.open(vec![Some(overlay)], 60);
    until_ready(&mut viewer, &[0]).await;
    let mut view = env.service.lane_defaults();
    view.policy.sticky = Sticky {
        enabled: true,
        timeout: Some(foundation::MediaTime(2_000_000)),
        opacity: 0.4,
    };
    viewer.set_view(0, 1, view);

    // Frame 45 is half a second after the second record.
    viewer.want(want(45, 0, 0));
    let (found, _) = overlays(&mut viewer, 1).await;
    assert_eq!(
        found[0].key, 1_000_000,
        "a timestamp key is in microseconds"
    );
    assert!(matches!(
        found[0].selection,
        Selection::Sample { kind: MatchKind::Retained, opacity, .. } if (opacity - 0.4).abs() < 1e-6
    ));
    assert!(quiet(&mut viewer).await);
}

#[test]
fn prepare_lane_frame_selects_without_a_viewer() {
    let env = Env::new();
    let overlay = env.mot("a.txt", [0]);
    let track = env
        .tracks
        .registry
        .open(&overlay, SIZE, &foundation::CancelToken::new())
        .expect("track");
    let catalog = render_catalog::compile(include_str!("../../../../assets/catalogs/minimal.json"))
        .expect("catalog");
    let timeline = timeline(2);
    let view: LaneView = env.service.lane_defaults();
    let frame = |id| timeline.frame(id).expect("frame");

    let (selection, _) =
        prepare_lane_frame(&track, &timeline, &view, &catalog, frame(0)).expect("prepared");
    assert!(matches!(
        selection,
        Selection::Sample {
            index: 0,
            kind: MatchKind::Exact,
            ..
        }
    ));
    let (selection, drawing) =
        prepare_lane_frame(&track, &timeline, &view, &catalog, frame(1)).expect("prepared");
    assert_eq!(selection, Selection::Empty);
    assert!(drawing.is_empty());
}
