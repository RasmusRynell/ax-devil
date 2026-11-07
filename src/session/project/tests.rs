use super::*;
use foundation::{CancelToken, ErrorCode, HostPath, ItemId, UserError, Warning};
use handlers::Handlers;
use std::path::PathBuf;
use workspace::{
    ContentId, Environment, LiveOverlay, LiveRecipe, OverlayRef, PlaylistContent, PlaylistSpan,
    Protocol, Recipe, Secret, VideoRecipe, VideoRef,
};
use workspace_store::{HiddenRef, WorkspaceState as State};

fn item(id: ItemId, label: &str, folder: &str) -> WorkspaceItem {
    WorkspaceItem {
        id,
        label: label.into(),
        recipe: Recipe::new(Source::Video(VideoRecipe {
            video: HostPath::new(format!("/home/{folder}/{label}")),
            overlays: Vec::new(),
        })),
    }
}

fn entry(id: ContentId, label: &str, folder: &str) -> Entry {
    Entry {
        id,
        label: label.into(),
        video: VideoRef::File(PathBuf::from(format!("/home/{folder}/{label}"))),
        overlays: vec![OverlayRef {
            label: "tracks.txt".into(),
            slot: "tracks.txt".into(),
            path: PathBuf::from(format!("/home/{folder}/tracks.txt")),
            handler: "MOT_FILE".into(),
            options: Default::default(),
        }],
    }
}

/// An unnamed live stream with sentinels in its host, login, broker and stream URL, resolved as a session does. Its
/// camera password is a literal and its broker password a `$NAME` reference.
fn live_stream(item: ItemId) -> (WorkspaceItem, Content) {
    let recipe = LiveRecipe {
        host: "SECRET-CAMERA".into(),
        username: "SECRET-USER".into(),
        password: Secret::new("SECRET-LIVE-PASSWORD"),
        protocol: Protocol::Https,
        port: 0,
        head: 1,
        rtsp_port: 554,
        resolution: None,
        url: Some("rtsp://SECRET-STREAM/axis-media/media.amp".into()),
        overlay: LiveOverlay::Mqtt {
            handler: "ADF_V1_FRAME".into(),
            broker_host: "SECRET-BROKER".into(),
            broker_port: 1883,
            broker_user: "SECRET-BROKER-USER".into(),
            // An unset reference resolves to nothing and sends nothing.
            broker_pass: Secret::new("$SECRET_BROKER_REF"),
            source_key: "SECRET-SOURCE".into(),
        },
    };
    let recipe = Recipe::new(Source::Live(recipe));
    let content = recipe
        .resolve(
            &item,
            "Live stream",
            &Handlers::built_in(),
            &Environment::of_process(),
            &CancelToken::new(),
        )
        .unwrap_or_else(|_| panic!("the live recipe resolves"));
    let item = WorkspaceItem {
        id: item,
        label: String::new(),
        recipe,
    };
    (item, content)
}

/// A Doc with a resolved open video, a resolved open playlist, a resolved open live stream, a failed open item and
/// two items with no viewer (a failed one and a resolved playlist), all carrying sentinels in every place a watcher
/// must not learn of.
fn doc() -> (Doc, [ItemId; 6]) {
    let ids = [(); 6].map(|()| ItemId::generate());
    let [shown, playlist, failed, closed, closed_playlist, live] = ids;
    let (live_item, live_content) = live_stream(live);
    let video = Content::SeekableVideo(SeekableVideoContent {
        item_id: shown,
        entry: entry(ContentId::of_item(&shown), "gate.mp4", "SECRET-PATH-A"),
    });
    let playlist_of = |item: ItemId, name: &str, folder: &str| {
        Content::Playlist(PlaylistContent {
            id: ContentId::of_item(&item),
            item_id: item,
            display_name: name.into(),
            entries: ["SEQ-01", "SEQ-02"]
                .map(|label| entry(ContentId::of_entry(&item, name, label), label, folder))
                .into(),
            playlists: vec![PlaylistSpan {
                id: ContentId::of_playlist(&item, name),
                name: name.into(),
                entries: 2,
            }],
            warnings: vec![Warning {
                problem: "Skipped 1 of 3 sequences".into(),
                reasons: vec!["/home/SECRET-SKIP/SEQ-03: bad seqinfo.ini".into()],
                consequence: "They are not in the playlist.".into(),
                more_reasons: false,
            }],
        })
    };
    let failure = |folder: &str| {
        let reason = "SECRET-REASON rtsp://root:SECRET-PASSWORD@camera";
        UserError::new(ErrorCode::FileUnreadable, reason)
            .at(HostPath::new(format!("/home/{folder}/lost.mp4")))
    };
    let mut state = State::default();
    state.workspace.name = Some("Review".into());
    state.workspace.items = vec![
        item(shown, "gate.mp4", "SECRET-PATH-A"),
        item(playlist, "MOT sequences", "SECRET-PATH-D"),
        item(failed, "lost.mp4", "SECRET-PATH-B"),
        item(closed, "closed.mp4", "SECRET-PATH-C"),
        item(closed_playlist, "closed playlist", "SECRET-PATH-E"),
        live_item,
    ];
    let resolved = |content| Resolution::Resolved {
        content,
        generation: ContentGen(1),
    };
    state.resolutions.insert(shown, resolved(video));
    state.resolutions.insert(live, resolved(live_content));
    state.resolutions.insert(
        playlist,
        resolved(playlist_of(playlist, "MOT sequences", "SECRET-PATH-D")),
    );
    state.resolutions.insert(
        closed_playlist,
        resolved(playlist_of(
            closed_playlist,
            "closed playlist",
            "SECRET-PATH-E",
        )),
    );
    state
        .resolutions
        .insert(failed, Resolution::Failed(failure("SECRET-PATH-B")));
    state
        .resolutions
        .insert(closed, Resolution::Failed(failure("SECRET-PATH-C")));
    state.file = Some(HostPath::new("/data/SECRET-FILE.ax-devil.workspace"));
    state.open.extend([shown, playlist, failed, live]);
    state.hidden.insert(HiddenRef::Entry(ContentId::of_entry(
        &playlist,
        "MOT sequences",
        "SEQ-02",
    )));
    (Doc { workspace: state }, ids)
}

#[test]
fn a_watcher_learns_no_path_credential_or_reason_and_sees_only_what_is_open() {
    let (doc, [_, _, failed, _, _, _]) = doc();
    let public = Audience {
        host_details: false,
    };
    let watcher = serde_json::to_string(&project(&doc, public)).unwrap();
    for secret in [
        "SECRET",
        "/home",
        "rtsp",
        "closed.mp4",
        "closed playlist",
        // A row's `location` is null for a watcher.
        "\"location\":\"",
    ] {
        assert!(!watcher.contains(secret), "{secret} reached {watcher}");
    }
    for shared in [
        "gate.mp4",
        "MOT sequences",
        "SEQ-01",
        "tracks.txt",
        "Review",
        "FileUnreadable",
        "LiveVideo",
        "Live stream",
        "MQTT",
        &failed.to_string(),
    ] {
        assert!(watcher.contains(shared), "{shared} missing from {watcher}");
    }

    let owner = serde_json::to_string(&project(&doc, Audience::HOST)).unwrap();
    for host_only in [
        "SECRET-REASON",
        "SECRET-PATH-B",
        "SECRET-SKIP",
        "SECRET-FILE",
        "SECRET-CAMERA",
        "closed.mp4",
        "closed playlist",
        // The stored recipe, which Edit starts from.
        "SECRET-PATH-A",
        "SECRET-PATH-D",
        "SECRET-PATH-E",
    ] {
        assert!(
            owner.contains(host_only),
            "{host_only} missing from {owner}"
        );
    }
    // The stored connection reaches the owner as typed, a reference as a reference; no literal password does.
    for recipe in [
        "SECRET-USER",
        "SECRET-BROKER-USER",
        "SECRET-STREAM",
        "SECRET-SOURCE",
        "$SECRET_BROKER_REF",
    ] {
        assert!(owner.contains(recipe), "{recipe} missing from {owner}");
    }
    assert!(
        !owner.contains("SECRET-LIVE-PASSWORD"),
        "a literal password reached {owner}"
    );
    let recipes = |audience| {
        project(&doc, audience)
            .workspace
            .items
            .into_iter()
            .filter_map(|item| match item.resolution {
                ResolutionState::Resolved {
                    content:
                        ContentInfo::LiveVideo {
                            recipe,
                            id: _,
                            generation: _,
                            display_name: _,
                            slot: _,
                            overlay: _,
                            lanes: _,
                        },
                } => Some(recipe),
                _ => None,
            })
            .collect::<Vec<_>>()
    };
    assert_eq!(recipes(public), [None]);
    let recipe = recipes(Audience::HOST).pop().flatten();
    let recipe = recipe.expect("an owner gets the live stream's recipe");
    assert_eq!(recipe.password, None);
    assert_eq!(recipe.label, None);
}

#[test]
fn rows_follow_the_items_a_watcher_sees_and_carry_what_was_hidden() {
    let (doc, ids) = doc();
    let public = Audience {
        host_details: false,
    };
    let rows = |audience| -> Vec<(String, bool)> {
        project(&doc, audience)
            .workspace
            .rows
            .into_iter()
            .map(|row| (row.label, row.hidden))
            .collect()
    };

    let watcher = rows(public);
    assert!(watcher.contains(&("SEQ-02".into(), true)), "{watcher:?}");
    assert!(watcher.contains(&("SEQ-01".into(), false)), "{watcher:?}");
    let live = ids[5];
    let wire_rows = project(&doc, public).workspace.rows;
    let row = wire_rows
        .iter()
        .find(|row| row.label == "Live stream")
        .unwrap();
    assert_eq!(row.entry.as_deref(), Some(live.to_string().as_str()));
    assert!(!watcher.iter().any(|(label, _)| label.starts_with("closed")));
    let owner = rows(Audience::HOST);
    assert!(owner.iter().any(|(label, _)| label == "closed playlist"));
    let located = project(&doc, Audience::HOST).workspace.rows;
    let video = located.iter().find(|row| row.label == "gate.mp4").unwrap();
    assert_eq!(
        video.location.as_deref(),
        Some("/home/SECRET-PATH-A/gate.mp4")
    );
}

#[test]
fn a_renamed_video_shows_its_new_name_without_resolving_again() {
    let (mut doc, [shown, ..]) = doc();
    doc.workspace.workspace.items[0].label = "Gate".into();
    let wire = project(&doc, Audience::HOST).workspace;
    let item = wire.items.iter().find(|item| item.id == shown.to_string());
    let Some(ItemState {
        resolution:
            ResolutionState::Resolved {
                content:
                    ContentInfo::SeekableVideo {
                        display_name,
                        entry,
                        ..
                    },
            },
        ..
    }) = item
    else {
        panic!("a resolved video");
    };
    assert_eq!(
        (display_name.as_str(), entry.label.as_str()),
        ("Gate", "Gate")
    );
}
