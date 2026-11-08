use super::*;
use foundation::{CancelToken, ErrorCode, HostPath, ItemId, UserError, Warning};
use handlers::Handlers;
use std::path::PathBuf;
use workspace::{
    ContentId, Environment, LiveOverlay, LiveRecipe, OverlayRef, PlaylistContent, PlaylistSpan,
    Protocol, Recipe, RtspRoute, Secret, VideoRecipe, VideoRef,
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
            metadata: Default::default(),
        }],
        starts_hidden: false,
        lanes_starting_hidden: Vec::new(),
        metadata: Default::default(),
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
        rtsp_route: RtspRoute::Direct,
        resolution: None,
        url: Some(
            "rtsp://SECRET-URL-USER:SECRET-URL-PASSWORD@SECRET-STREAM/axis-media/media.amp".into(),
        ),
        overlay: LiveOverlay::Mqtt {
            handler: "ADF_V1_FRAME".into(),
            broker_host: "SECRET-BROKER".into(),
            broker_port: 1883,
            broker_user: "SECRET-BROKER-USER".into(),
            // An unset reference resolves to nothing and sends nothing.
            broker_pass: Secret::new("$SECRET_BROKER_REF"),
            source_key: "SECRET-SOURCE".into(),
        },
        kept: Default::default(),
    };
    let recipe = Recipe::new(Source::Live(recipe));
    let content = recipe
        .resolve(
            &item,
            "",
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
                matches: None,
                sequence_groups: None,
                metadata: Default::default(),
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
    state.hidden.insert(
        HiddenRef::Entry(ContentId::of_entry(&playlist, "MOT sequences", "SEQ-02")),
        true,
    );
    (Doc { workspace: state }, ids)
}

#[test]
fn a_watcher_learns_no_path_credential_or_reason_and_sees_only_what_is_open() {
    let (doc, [_, _, failed, _, _, _]) = doc();
    let public = Audience {
        host_details: false,
    };
    let watcher = serde_json::to_string(&project(&doc, public, false)).unwrap();
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

    let owner = serde_json::to_string(&project(&doc, Audience::HOST, false)).unwrap();
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
        !owner.contains("SECRET-LIVE-PASSWORD") && !owner.contains("SECRET-URL-PASSWORD"),
        "a literal password reached {owner}"
    );
    assert!(owner.contains("SECRET-URL-USER@SECRET-STREAM"));
    let recipes = |audience| {
        project(&doc, audience, false)
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
    // A hidden password is a missing key, which is how the form tells it from an empty one.
    assert!(
        serde_json::to_value(&recipe)
            .unwrap()
            .get("password")
            .is_none()
    );
    assert_eq!(recipe.label, None);
}

#[test]
fn rows_follow_the_items_a_watcher_sees_and_carry_what_was_hidden() {
    let (doc, ids) = doc();
    let public = Audience {
        host_details: false,
    };
    let rows = |audience| -> Vec<(String, bool)> {
        project(&doc, audience, false)
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
    let wire_rows = project(&doc, public, false).workspace.rows;
    let row = wire_rows
        .iter()
        .find(|row| row.label == "Live stream")
        .unwrap();
    assert_eq!(row.entry.as_deref(), Some(live.to_string().as_str()));
    assert!(!watcher.iter().any(|(label, _)| label.starts_with("closed")));
    let owner = rows(Audience::HOST);
    assert!(owner.iter().any(|(label, _)| label == "closed playlist"));
    let located = project(&doc, Audience::HOST, false).workspace.rows;
    let video = located.iter().find(|row| row.label == "gate.mp4").unwrap();
    assert_eq!(
        video.location.as_deref(),
        Some("/home/SECRET-PATH-A/gate.mp4")
    );
}

/// A Doc of one open, unnamed live stream, pending or resolved in `env`, whose camera host and MQTT broker are both
/// `$CAM`.
fn referenced_stream(resolved: bool, env: &Environment) -> Doc {
    let id = ItemId::generate();
    let (mut item, _) = live_stream(id);
    let Source::Live(recipe) = &mut item.recipe.source else {
        panic!("a live recipe");
    };
    recipe.host = "$CAM".into();
    if let LiveOverlay::Mqtt { broker_host, .. } = &mut recipe.overlay {
        *broker_host = "$CAM".into();
    }
    let mut state = State::default();
    if resolved {
        let content = item
            .recipe
            .resolve(&id, "", &Handlers::built_in(), env, &CancelToken::new())
            .unwrap_or_else(|_| panic!("the live recipe resolves"));
        let generation = ContentGen(1);
        let resolution = Resolution::Resolved {
            content,
            generation,
        };
        state.resolutions.insert(id, resolution);
    }
    state.open.insert(id);
    state.workspace.items = vec![item];
    Doc { workspace: state }
}

/// What `audience` is shown of `doc`: its item label, row label, row detail and location, and camera and broker host.
/// Whatever it is shown, `SECRETPW` is not in any of the wire.
fn shown_of(doc: &Doc, audience: Audience, env: &Environment) -> Vec<String> {
    let wire = project_in(doc, audience, env);
    let json = serde_json::to_string(&wire).unwrap();
    assert!(!json.contains("SECRETPW"), "a login reached {json}");
    let item = &wire.workspace.items[0];
    let row = &wire.workspace.rows[0];
    let (host, broker) = item
        .shown
        .as_ref()
        .map(|shown| {
            (
                shown.host.clone(),
                shown.broker_host.clone().unwrap_or_default(),
            )
        })
        .unwrap_or_default();
    let location = row.location.clone().unwrap_or_default();
    vec![
        item.label.clone(),
        row.label.clone(),
        row.detail.clone(),
        location,
        host,
        broker,
    ]
}

fn env_of(value: &'static str) -> Environment {
    Environment::new(move |name| (name == "CAM").then(|| value.to_owned()))
}

const WATCHER: Audience = Audience {
    host_details: false,
};

#[test]
fn an_unnamed_stream_with_a_host_reference_is_named_by_its_value_only_for_a_session_that_expands() {
    for resolved in [false, true] {
        let owner =
            |env: &Environment| shown_of(&referenced_stream(resolved, env), Audience::HOST, env);
        // The row is named by the value; once resolved it also says where the camera is (nothing more, as the name
        // is that already).
        let location = if resolved { "192.0.2.9" } else { "" };
        let read: Vec<String> = [
            "192.0.2.9",
            "192.0.2.9",
            "",
            location,
            "192.0.2.9",
            "192.0.2.9",
        ]
        .map(String::from)
        .into();
        assert_eq!(owner(&env_of("192.0.2.9")), read, "resolved: {resolved}");
        // A session that does not expand shows the labels and hosts as typed, and no place: a reference is none.
        let typed: Vec<String> = ["$CAM", "$CAM", "", "", "$CAM", "$CAM"]
            .map(String::from)
            .into();
        assert_eq!(
            owner(&Environment::literal()),
            typed,
            "resolved: {resolved}"
        );
        // (An unset variable leaves a stream nothing to resolve, so only a pending one is shown.)
        if !resolved {
            let unset: Vec<String> = ["Live stream", "Live stream", "", "", "", ""]
                .map(String::from)
                .into();
            assert_eq!(owner(&Environment::new(|_| None)), unset);
        }
    }
}

#[test]
fn what_may_be_shown_as_a_host_is_what_the_camera_connection_takes_as_one() {
    let name = |length: usize| "a".repeat(length);
    let mut corpus: Vec<String> = [
        // Names and addresses, in any case, with or without white space around.
        "cam.local",
        "Cam_1.LOCAL",
        "cam-1",
        " cam.local ",
        "\tcam.local\n",
        "cam.local.",
        "192.0.2.7",
        "999.0.2.7",
        "192.0.2",
        // IPv6, bracketed or not, and what is not one.
        "::1",
        "[::1]",
        "2001:db8::1",
        "[2001:db8::1]",
        "fe80::1%eth0",
        "[fe80::1%eth0]",
        "[::1",
        "::1]",
        "[]",
        "[cam]",
        "[[::1]]",
        // Unicode.
        "caf\u{e9}.local",
        "\u{65e5}\u{672c}.local",
        "cam\u{ff0e}local",
        // Nothing, and the characters of a URL.
        "",
        " ",
        "user:SECRETPW@cam.local",
        "cam.local:554",
        "cam.local/path",
        "cam.local?key=1",
        "cam.local#top",
        "cam%2elocal",
        "rtsp://cam.local",
        "@",
        ":",
        "/",
        "?",
        "#",
        "%",
        "cam local",
        "$CAM",
    ]
    .map(String::from)
    .into();
    // Control and separator characters, inside and at the edges of a name.
    for control in [
        '\n', '\r', '\t', '\0', '\u{7f}', '\u{85}', '\u{2028}', '\u{a0}',
    ] {
        corpus.push(format!("cam{control}local"));
        corpus.push(format!("{control}cam.local{control}"));
    }
    // The length limit of a name, and far beyond it.
    for length in [1, 252, 253, 254, 255, 10_000] {
        corpus.push(name(length));
    }
    for text in corpus {
        assert_eq!(
            foundation::is_plain_host(&text),
            camera::Host::parse(&text).is_ok(),
            "{text:?}"
        );
    }
}

#[test]
fn an_expanded_host_that_is_no_plain_host_is_never_shown_to_anyone() {
    // A value that carries a login, a scheme, a path or a port could leak a password; none is a host. The
    // projections are checked whole for the password, and no label, detail, location or host is made from the value.
    for hostile in [
        "user:SECRETPW@cam.local",
        "cam.local/path?key=SECRETPW",
        "rtsp://cam.local",
        "cam.local:8554",
        "cam local",
    ] {
        for resolved in [false, true] {
            let env = env_of(hostile);
            let doc = referenced_stream(resolved, &env);
            let hidden: Vec<String> = ["Live stream", "Live stream", "", "", "", ""]
                .map(String::from)
                .into();
            assert_eq!(shown_of(&doc, Audience::HOST, &env), hidden, "{hostile}");
            // The watcher is told no host at all, and the same label.
            assert_eq!(shown_of(&doc, WATCHER, &env)[..2], hidden[..2], "{hostile}");
        }
    }
}

#[test]
fn a_renamed_video_shows_its_new_name_without_resolving_again() {
    let (mut doc, [shown, ..]) = doc();
    doc.workspace.workspace.items[0].label = "Gate".into();
    let wire = project(&doc, Audience::HOST, false).workspace;
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
