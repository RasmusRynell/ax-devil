//! A held feed, HTTP reload and coalesced edit, with acceptance checked by the actual export owner.
use super::super::*;
use crate::test_support::TestApp;
use api_types::playback::CatalogRefWire;
use axum::{
    body::Body,
    extract::ws::Message,
    http::{Request as HttpRequest, StatusCode},
};
use catalogs::CatalogId;
use foundation::CancelToken;
use serde_json::{Value, json};
use sessions::SessionId;
use std::{collections::BTreeMap, fs, path::Path};

async fn document(feed: &mut CatalogFeed) -> Value {
    loop {
        let outgoing = tokio::time::timeout(std::time::Duration::from_secs(2), feed.next())
            .await
            .unwrap();
        let Message::Text(text) = outgoing.message else {
            panic!("a JSON reply")
        };
        let value: Value = serde_json::from_str(text.as_str()).unwrap();
        if value["type"] == "Catalog" {
            return value;
        }
    }
}

#[tokio::test]
async fn http_validation_cannot_draw_a_revision_skipped_by_the_feed_frontend_fixture() {
    let app = TestApp::with_catalog_folder();
    let path = app._folder.path().join("catalogs/export.json");
    let original = include_str!("../../../../../assets/catalogs/minimal.v4.json");
    fs::write(&path, original).unwrap();
    let id = CatalogId::User("export.json".into());
    app.catalogs().reread(&id);
    let mut feed = CatalogFeed::new(app.catalogs(), Audience::HOST, SessionId::generate());
    feed.ask(id.to_string());
    let initial = document(&mut feed).await;
    // Explicitly hold polling of the real feed, not merely delivery to a client after keep() has run.
    let (release, held) = tokio::sync::oneshot::channel();
    let waiting = tokio::spawn(async move {
        held.await.unwrap();
        let newest = document(&mut feed).await;
        (feed, newest)
    });
    let mut changed: Value = serde_json::from_str(original).unwrap();
    changed["name"] = "HTTP only".into();
    fs::write(&path, changed.to_string()).unwrap();
    let (status, bytes) = app
        .send(
            HttpRequest::post("/api/catalogs/reload")
                .header("content-type", "application/json")
                .body(Body::from(json!({"id": id.to_string()}).to_string()))
                .unwrap(),
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    let http: Value = serde_json::from_slice(&bytes).unwrap();
    // The library can serve JSON that the frontend compiler rejects. The watch channel coalesces to this edit.
    fs::write(&path, r#"{"schema":4,"name":"Broken","layers":null}"#).unwrap();
    app.catalogs().reread(&id);
    release.send(()).unwrap();
    let (feed, broken) = waiting.await.unwrap();
    let revisions = [&initial, &http, &broken].map(|v| v["revision"].as_u64().unwrap() as u32);
    assert!(revisions[0] < revisions[1] && revisions[1] < revisions[2]);
    assert!(feed.kept(&id.to_string(), revisions[1]).is_none());

    let video = VideoRef::File(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/video/duplicate_pts.mkv"),
    );
    let _opened = app
        .state
        .videos
        .open(&video, &CancelToken::new(), &mut |_| {})
        .unwrap();
    let (_, index) = app.state.videos.open_input(&video).unwrap();
    let overlays = app
        .state
        .overlays
        .open_viewer(vec![None], index.timeline().clone(), 1024);
    let entry = Entry {
        id: workspace::ContentId::of_item(&foundation::ItemId::generate()),
        label: "Video".into(),
        video,
        overlays: vec![],
        starts_hidden: false,
        lanes_starting_hidden: vec![],
        metadata: serde_json::Map::new(),
    };
    let mut accepted = Vec::new();
    for (at, revision) in revisions.into_iter().enumerate() {
        // Rendering is an external boundary. This checks the real owner's freeze/admission, before a renderer starts.
        let service = Arc::new(ExportService::with_renderer(Arc::new(|_| {
            Err(UserError::new(ErrorCode::Internal, "test renderer"))
        })));
        let exports = Exports::new(&entry, Audience::HOST, &service, &app.state.videos);
        let request = Request {
            job: 1,
            first: 0,
            last: 0,
            format: ExportFormatWire::Png,
            path: app
                ._folder
                .path()
                .join(format!("{at}.png"))
                .to_string_lossy()
                .into_owned(),
            lanes: vec![ExportLaneWire {
                lane: 0,
                catalog: CatalogRefWire {
                    id: id.to_string(),
                    revision,
                },
                policy_revision: 0,
                data_revision: None,
                style: export::LaneStyle {
                    label: "Video".into(),
                    enabled_classes: vec![],
                    class_options: 0,
                    hidden: 0,
                    theme: None,
                    retained_opacity: 1.0,
                    filter: None,
                },
            }],
        };
        let result = exports.begin(request, &overlays, &feed, index.timeline().last());
        if at == 1 {
            let error = result.err().unwrap();
            assert!(error.reason.contains("The viewer was not served revision"));
        } else {
            assert!(result.is_ok());
            accepted.push(at + 1);
        }
    }
    // Canonicalize the process-wide revision clock, keeping each actual boundary result paired with its reply.
    let mut recorded =
        json!({"initial": initial, "http": http, "broken": broken, "exportable": accepted});
    for (key, revision) in [("initial", 1), ("http", 2), ("broken", 3)] {
        recorded[key]["revision"] = revision.into();
    }
    crate::test_support::fixtures::check(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures"),
        "catalog-export",
        &BTreeMap::from([(
            "http-feed.json".into(),
            format!("{}\n", serde_json::to_string_pretty(&recorded).unwrap()),
        )]),
    );
}
