//! Catalogs over the playback socket, through the real routes: a viewer fetches a catalog by id, is told the catalogs
//! served and the lane defaults when it opens, and is pushed the new document of the catalogs it asked for and the new
//! list of catalogs; a file that cannot be served says why. The tests drive the library's polls themselves.

use crate::common;

use api_types::playback::{
    ClientMessage, LaneDefaultsWire, LanePolicyWire, ServerMessage, StickyWire,
};
use catalogs::Library;
use common::{Backend, Event, Viewer, fixture};
use sessions::{
    CursorOptions,
    session::{Access, Command, Control, Reply},
};
use std::{collections::BTreeMap, fs, path::Path};

/// A catalog document of `name`, padded so that two versions of one file differ in size.
fn document(name: &str, padding: usize) -> String {
    format!(
        r#"{{"schema":4,"name":"{name}","description":"About {name}","layers":[]}}{}"#,
        " ".repeat(padding)
    )
}

/// Polls the folder until the library has read what the test wrote or removed: the second poll sees it unchanged.
fn settle(library: &Library) {
    library.rescan();
    assert!(library.rescan(), "the second poll finds the change");
}

/// A viewer that opened a video, and the ids of the catalogs it was told about.
async fn viewer_of(backend: &Backend) -> (Viewer, Vec<String>) {
    let added = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    opened_with(backend, &added.ticket()).await
}

/// A viewer that opened the video of `ticket`, and the ids of the catalogs it was told about.
async fn opened_with(backend: &Backend, ticket: &str) -> (Viewer, Vec<String>) {
    let mut viewer = backend.viewer().await;
    viewer.open(ticket, None).await;
    loop {
        match viewer.next().await {
            Event::Message(ServerMessage::Indexing { .. }) => {}
            Event::Message(ServerMessage::Opened { catalogs, .. }) => {
                let Event::Timeline(_) = viewer.next().await else {
                    panic!("the Timeline envelope must follow Opened");
                };
                let ids = catalogs.into_iter().map(|catalog| catalog.id).collect();
                return (viewer, ids);
            }
            _ => panic!("expected Indexing or Opened"),
        }
    }
}

/// The next text message, leaving out the frames that stream meanwhile.
async fn message(viewer: &mut Viewer) -> ServerMessage {
    loop {
        match viewer.next().await {
            Event::Message(message) => return message,
            Event::Frame(_) => {}
            _ => panic!("expected a text message"),
        }
    }
}

async fn ask(viewer: &mut Viewer, id: &str) {
    viewer.send(&ClientMessage::Catalog { id: id.into() }).await;
}

fn catalog_ids(message: ServerMessage) -> Vec<String> {
    let ServerMessage::Catalogs { catalogs } = message else {
        panic!("expected the list of catalogs, got {message:?}");
    };
    catalogs.into_iter().map(|catalog| catalog.id).collect()
}

fn catalog(message: ServerMessage) -> (String, u32, Option<String>, Option<String>) {
    let ServerMessage::Catalog {
        id,
        revision,
        document,
        problem,
    } = message
    else {
        panic!("expected a catalog, got {message:?}");
    };
    (id, revision, document, problem)
}

#[tokio::test]
async fn a_viewer_is_pushed_the_catalogs_it_asked_for_and_every_viewer_the_changed_list() {
    let folder = tempfile::tempdir().unwrap();
    let night = folder.path().join("night.json");
    fs::write(&night, document("Night", 0)).unwrap();
    let library = Library::with_folder(folder.path().to_path_buf());
    let backend = Backend::start_with_catalogs(library.clone()).await;
    let (mut asking, listed) = viewer_of(&backend).await;
    let (mut bystander, _) = viewer_of(&backend).await;
    assert_eq!(listed.last().unwrap(), "user:night.json");
    ask(&mut asking, "user:night.json").await;
    let (_, first, text, problem) = catalog(message(&mut asking).await);
    assert!(text.unwrap().contains("Night"));
    assert_eq!(problem, None);

    // A new document under the same name: the viewer that asked is pushed it, the other is not (its next message is
    // the answer it asks for now).
    fs::write(&night, document("Night", 5)).unwrap();
    settle(&library);
    let (id, second, text, _) = catalog(message(&mut asking).await);
    assert_eq!((id.as_str(), text.is_some()), ("user:night.json", true));
    assert!(second > first);
    ask(&mut bystander, "built-in:standard").await;
    assert_eq!(
        catalog(message(&mut bystander).await).0,
        "built-in:standard"
    );

    // A file that appears changes the list for everyone.
    fs::write(folder.path().join("dawn.json"), document("Dawn", 0)).unwrap();
    settle(&library);
    for viewer in [&mut asking, &mut bystander] {
        assert_eq!(
            catalog_ids(message(viewer).await)
                .iter()
                .rev()
                .take(2)
                .collect::<Vec<_>>(),
            ["user:night.json", "user:dawn.json"]
        );
    }

    // One that vanishes: the list again, and the viewer that asked for it is told it is gone, at a newer revision.
    fs::remove_file(&night).unwrap();
    settle(&library);
    assert_eq!(
        catalog_ids(message(&mut asking).await).last().unwrap(),
        "user:dawn.json"
    );
    let (id, third, text, problem) = catalog(message(&mut asking).await);
    assert_eq!(
        (id.as_str(), text, problem),
        ("user:night.json", None, None)
    );
    assert!(third > second);
    assert_eq!(
        catalog_ids(message(&mut bystander).await).last().unwrap(),
        "user:dawn.json"
    );
}

/// Shares the session of `added` and opens its video as a watcher of it, with the ids of the catalogs it was told.
async fn watcher_of(backend: &Backend, added: &common::Added) -> (Viewer, Vec<String>) {
    added
        .owner
        .command(Command::Control(Control::Share {
            access: Access::Watch,
        }))
        .unwrap();
    let link = added.owner.state().sharing.as_ref().unwrap().link.clone();
    let seat = backend
        .state
        .sessions
        .join(&link, CursorOptions::default())
        .unwrap();
    let Ok(Reply::Ticket(ticket)) = seat.command(Command::RequestTicket(added.key.clone())) else {
        panic!("a watcher may stream what the owner has open");
    };
    opened_with(backend, &ticket.to_string()).await
}

#[tokio::test]
async fn a_watcher_is_shown_a_user_catalog_only_while_a_host_lane_uses_it() {
    let folder = tempfile::tempdir().unwrap();
    fs::write(folder.path().join("night.json"), document("Night", 0)).unwrap();
    let library = Library::with_folder(folder.path().to_path_buf());
    let backend = Backend::start_with_catalogs(library).await;
    let added = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let (mut owner, _) = opened_with(&backend, &added.ticket()).await;
    let (mut watcher, listed) = watcher_of(&backend, &added).await;
    assert!(
        listed.iter().all(|id| id.starts_with("built-in:")),
        "the host's files are not listed"
    );
    ask(&mut watcher, "user:night.json").await;
    let (_, unused, text, problem) = catalog(message(&mut watcher).await);
    assert_eq!((unused, text, problem), (0, None, None));

    // A host lane draws with it: the watcher is told it exists and given its text.
    ask(&mut owner, "user:night.json").await;
    assert!(
        catalog_ids(message(&mut watcher).await).contains(&"user:night.json".to_owned()),
        "the list now names it"
    );
    let (_, used, text, _) = catalog(message(&mut watcher).await);
    assert!(used > 0 && text.unwrap().contains("Night"));

    // The host's viewer leaves: nothing uses it, so the watcher loses it again.
    drop(owner);
    assert!(
        !catalog_ids(message(&mut watcher).await).contains(&"user:night.json".to_owned()),
        "the list drops it"
    );
    let (_, gone, text, _) = catalog(message(&mut watcher).await);
    assert_eq!((gone, text), (0, None));
}

#[tokio::test]
async fn a_catalog_a_host_lane_uses_is_not_shown_to_the_watchers_of_another_session() {
    let folder = tempfile::tempdir().unwrap();
    fs::write(folder.path().join("night.json"), document("Night", 0)).unwrap();
    let library = Library::with_folder(folder.path().to_path_buf());
    let backend = Backend::start_with_catalogs(library).await;
    let video = fixture("cfr_h264_gop10_bf2.mp4");
    let first = backend.add(&video).await;
    let (mut first_host, _) = opened_with(&backend, &first.ticket()).await;
    let second = backend.add(&video).await;
    let (mut second_host, _) = opened_with(&backend, &second.ticket()).await;
    let (mut watcher, _) = watcher_of(&backend, &second).await;

    // A lane of the first session draws with the catalog; the watcher of the second session is not shown it.
    ask(&mut first_host, "user:night.json").await;
    catalog(message(&mut first_host).await);
    ask(&mut watcher, "user:night.json").await;
    let (_, revision, text, problem) = catalog(message(&mut watcher).await);
    assert_eq!((revision, text, problem), (0, None, None));

    // A lane of its own session's host makes it visible.
    ask(&mut second_host, "user:night.json").await;
    assert!(
        catalog_ids(message(&mut watcher).await).contains(&"user:night.json".to_owned()),
        "the list names it"
    );
}

#[tokio::test]
async fn a_file_that_cannot_be_served_answers_with_its_problem_until_it_is_fixed() {
    let folder = tempfile::tempdir().unwrap();
    let big = folder.path().join("big.json");
    fs::write(&big, vec![b' '; catalogs::MAX_CATALOG_BYTES as usize + 1]).unwrap();
    let library = Library::with_folder(folder.path().to_path_buf());
    let backend = Backend::start_with_catalogs(library.clone()).await;
    let (mut viewer, listed) = viewer_of(&backend).await;
    assert_eq!(listed.last().unwrap(), "user:big.json");

    ask(&mut viewer, "user:big.json").await;
    let (_, broken, text, problem) = catalog(message(&mut viewer).await);
    assert_eq!(text, None);
    assert!(problem.unwrap().contains("at most"));

    fs::write(&big, document("Small", 0)).unwrap();
    settle(&library);
    // The list changes too, as the file now has its document's name.
    assert!(catalog_ids(message(&mut viewer).await).contains(&"user:big.json".to_owned()));
    let (_, fixed, text, problem) = catalog(message(&mut viewer).await);
    assert!(text.unwrap().contains("Small"));
    assert_eq!(problem, None);
    assert!(fixed > broken);

    // The folder is only read.
    assert_eq!(fs::read_dir(Path::new(folder.path())).unwrap().count(), 1);
}

#[tokio::test]
async fn a_catalog_is_fetched_by_id_and_one_that_is_not_served_answers_with_none() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;
    viewer.opened().await;

    for id in ["built-in:standard", "user:night.json", "nope"] {
        viewer.send(&ClientMessage::Catalog { id: id.into() }).await;
    }

    let mut answers = BTreeMap::new();
    while answers.len() < 3 {
        match viewer.next().await {
            Event::Message(ServerMessage::Catalog {
                id,
                revision,
                document,
                ..
            }) => {
                answers.insert(id, (revision, document));
            }
            Event::Frame(_) => {}
            _ => panic!("expected the answers to the catalog requests"),
        }
    }
    let (revision, document) = &answers["built-in:standard"];
    assert_eq!(*revision, 1);
    let document: serde_json::Value = serde_json::from_str(document.as_ref().unwrap()).unwrap();
    assert_eq!(
        (document["schema"].as_u64(), document["name"].as_str()),
        (Some(4), Some("Standard"))
    );
    for unserved in ["user:night.json", "nope"] {
        assert_eq!(answers[unserved], (0, None), "{unserved}");
    }
}

#[tokio::test]
async fn opened_lists_the_served_catalogs_and_what_every_lane_starts_with() {
    let backend = Backend::start().await;
    let id = backend.add(&fixture("cfr_h264_gop10_bf2.mp4")).await;
    let mut viewer = backend.viewer().await;
    viewer.open(&id.ticket(), None).await;

    let (lane_defaults, catalogs) = loop {
        match viewer.next().await {
            Event::Message(ServerMessage::Indexing { .. }) => {}
            Event::Message(ServerMessage::Opened {
                lane_defaults,
                catalogs,
                ..
            }) => break (lane_defaults, catalogs),
            _ => panic!("expected Indexing or Opened"),
        }
    };

    let ids: Vec<_> = catalogs.iter().map(|catalog| catalog.id.as_str()).collect();
    assert_eq!(
        ids,
        [
            "built-in:standard",
            "built-in:minimal",
            "built-in:chunky",
            "built-in:glass",
            "built-in:tracking",
            "built-in:classic"
        ]
    );
    assert_eq!(
        lane_defaults,
        LaneDefaultsWire {
            catalog: "built-in:standard".into(),
            policy: LanePolicyWire {
                basis: None,
                tolerance_ms: Some(50),
                sticky: StickyWire {
                    enabled: true,
                    timeout_ms: Some(2050),
                },
            },
        }
    );
}
