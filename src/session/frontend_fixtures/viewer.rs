//! Scenarios of the viewer tests.

use super::{Build, Recording, Scene, close_viewer, content, remove};
use serde_json::{Value, json};

/// The scenarios of this file.
pub(super) const SCENARIOS: &[(&str, Build)] = &[
    ("live-video", live_video),
    ("one-video", one_video),
    ("playlist", playlist),
    ("playlist-retirement", playlist_retirement),
    ("playlist-groups", playlist_groups),
    ("playlist-lanes", playlist_lanes),
    ("split", split),
];

/// The command a client sends to read `item` again.
fn resolve(item: &str) -> Value {
    content("resolve", json!({ "item": item }))
}

/// A scene with the one open item `key` of `build`, which adds it.
fn opened(key: &str, build: impl FnOnce(&mut Scene)) -> Scene {
    let mut scene = Scene::new();
    build(&mut scene);
    scene.open(key);
    scene
}

/// A live stream `a`, open. Step: it is edited, as the dialog saves it.
fn live_video() -> Recording {
    let scene = opened("a", |scene| {
        scene.live("a", Some("Camera"), "camera.local");
    });
    let mut recording = Recording::initial(&scene);
    let new = json!({
        "type": "Live", "label": "Gate", "host": "10.0.0.7", "username": "root", "password": "",
        "head": 1, "resolution": "", "url": "",
        "overlay": { "mode": "datahub", "handler": "ADF_V1_FRAME", "topic": "saved.topic", "channel": 3 },
        "kept_mqtt": {
            "broker_host": "", "broker_port": 1883, "broker_user": "", "broker_pass": "",
            "source_key": "custom"
        },
    });
    let edit = content(
        "editItem",
        json!({ "item": recording.ids["a"], "new": new }),
    );
    recording.branch("initial", scene.session(), edit, "edited");
    recording
}

/// A video `a`, open. Its files change twice, it is read again, and it is removed; or it is read again and fails, twice;
/// or it is still opening, or failed to open.
fn one_video() -> Recording {
    let video = || {
        opened("a", |scene| {
            scene.video("a", "a.mp4", &[]);
        })
    };
    let mut scene = video();
    let mut recording = Recording::initial(&scene);
    let ids = scene.ids().clone();
    let initial = scene.session().clone();
    recording.branch("initial", &initial, remove(&ids["a"]), "removed-at-once");
    recording.branch("initial", &initial, close_viewer(&ids["a"]), "closed");
    scene.source_changed("a");
    recording.state("changed-1", scene.session());
    scene.source_changed("a");
    let changed = scene.session().clone();
    recording.state("changed-2", &changed);
    recording.branch("changed-2", &changed, resolve(&ids["a"]), "reloaded");
    scene.resolve("a");
    recording.state("reloaded", scene.session());
    scene.remove("a");
    recording.state("removed", scene.session());

    let mut scene = video();
    scene.fail_resolving("a");
    scene.resolve("a");
    recording.state("reload-failed-1", scene.session());
    scene.resolve("a");
    recording.state("reload-failed-2", scene.session());

    let scene = opened("a", |scene| {
        scene.pending("a", "a.mp4");
    });
    recording.state("pending", scene.session());
    let scene = opened("a", |scene| {
        scene.failed("a", "a.mp4");
    });
    recording.state("failed", scene.session());
    recording
}

/// A playlist `list` of entries A to D, each with a lane of its own name but D, which has none, open. The entries
/// named hide, in the order of the states.
fn playlist() -> Recording {
    let hiding = |entries: &[&str]| {
        let mut scene = opened("list", |scene| {
            scene.playlist(
                "list",
                &[(
                    "list",
                    &[("A", &["A"]), ("B", &["B"]), ("C", &["C"]), ("D", &[])],
                )],
            );
        });
        for entry in entries {
            scene.hide(&format!("list/{entry}"), None);
        }
        scene
    };
    let shown = hiding(&[]);
    let mut recording = Recording::initial(&shown);
    let rename = content(
        "renameItem",
        json!({ "item": recording.ids["list"], "label": "Later" }),
    );
    recording.branch("initial", shown.session(), rename, "renamed");
    recording.branch(
        "initial",
        shown.session(),
        remove(&recording.ids["list"]),
        "removed",
    );
    for (name, entries) in [
        ("a-hidden", &["A"][..]),
        ("b-hidden", &["B"]),
        ("c-hidden", &["C"]),
        ("bc-hidden", &["C", "B"]),
        ("bcd-hidden", &["C", "B", "D"]),
        ("abcd-hidden", &["C", "B", "D", "A"]),
    ] {
        recording.state(name, hiding(entries).session());
    }
    let mut scene = hiding(&["B"]);
    scene.hide("list/C", Some("C"));
    recording.state("b-hidden-lane-c-hidden", scene.session());
    recording
}

/// A playlist `list` of the playlists P and Q, open: P of A and B and Q of C and D; or with all of P hidden, or A, B
/// and C; or P of A alone; or a single playlist of A; or one without entries, which the backend refuses.
fn playlist_groups() -> Recording {
    let list = |groups: super::outcomes::Groups| {
        opened("list", |scene| {
            scene.playlist("list", groups);
        })
    };
    let a = &[("A", &["A"][..])][..];
    let ab = &[("A", &["A"][..]), ("B", &["B"])][..];
    let cd = &[("C", &["C"][..]), ("D", &["D"])][..];
    let mut two = list(&[("P", ab), ("Q", cd)]);
    let mut recording = Recording::new(&two);
    recording.state("two", two.session());
    two.hide("list/A", None);
    two.hide("list/B", None);
    recording.state("p-hidden", two.session());
    two.hide("list/C", None);
    recording.state("abc-hidden", two.session());
    let bcd = &[("B", &["B"][..]), ("C", &["C"]), ("D", &["D"])][..];
    recording.state("p-single", list(&[("P", a), ("Q", bcd)]).session());
    recording.state("one-entry", list(&[("P", a)]).session());
    recording.state("empty-refused", list(&[("P", &[])]).session());
    recording
}

/// A playlist `list`, open, of an entry A without overlays, B with a lane B and C with one lane whose label is empty,
/// which a plugin overlay may have; and a playlist `grid` of an entry E with lanes GT, DPM and SEG, whose middle lane is
/// hidden in the state `dpm-hidden`.
fn playlist_lanes() -> Recording {
    let mut scene = opened("list", |scene| {
        scene.playlist(
            "list",
            &[("list", &[("A", &[]), ("B", &["B"]), ("C", &[""])])],
        );
        scene.playlist("grid", &[("grid", &[("E", &["GT", "DPM", "SEG"])])]);
    });
    let mut recording = Recording::initial(&scene);
    scene.hide("grid/E", Some("DPM"));
    recording.state("dpm-hidden", scene.session());
    recording
}

/// Videos `a` to `i`, of which a; a, b and c; or all are open. Steps: close the viewer of c, leaving a and b; close the ninth.
fn split() -> Recording {
    let open = |keys: &[&str]| {
        let mut scene = Scene::new();
        for key in "abcdefghi".chars().map(String::from) {
            scene.video(&key, &format!("{key}.mp4"), &[]);
        }
        for key in keys {
            scene.open(key);
        }
        scene
    };
    let scene = open(&["a"]);
    let mut recording = Recording::new(&scene);
    recording.state("a-open", scene.session());
    let abc = open(&["a", "b", "c"]);
    recording.state("abc-open", abc.session());
    let all: Vec<String> = "abcdefghi".chars().map(String::from).collect();
    let all: Vec<&str> = all.iter().map(String::as_str).collect();
    let nine = open(&all);
    recording.state("nine-open", nine.session());
    let ids = recording.ids.clone();
    recording.branch(
        "nine-open",
        nine.session(),
        close_viewer(&ids["i"]),
        "eight-open",
    );
    recording.step(
        "abc-open",
        &mut abc.session().clone(),
        close_viewer(&ids["c"]),
        "ab-open",
    );
    recording
}

/// Entry B disappears with one pane, duplicate panes, or a surviving pinned pane of A.
fn playlist_retirement() -> Recording {
    let initial = opened("list", |scene| {
        scene.playlist("list", &[("list", &[("A", &[]), ("B", &[])])]);
    });
    let mut recording = Recording::new(&initial);
    for (name, entries) in [
        ("single", vec!["B"]),
        ("duplicates", vec!["B", "B"]),
        ("survivor", vec!["B", "A"]),
    ] {
        let mut scene = initial.clone();
        let viewers: serde_json::Map<String, Value> = entries.iter().enumerate().map(|(at, entry)| {
            ((at + 1).to_string(), json!({ "item": scene.ids()["list"], "entry": scene.ids()[&format!("list/{entry}")], "pinned": true, "clock": { "frame": 7, "playing": false, "speedCenti": 125, "atUs": 0 } }))
        }).collect();
        let first = json!({ "type": "pane", "viewer": 1 });
        let layout = if entries.len() == 1 {
            first
        } else {
            json!({ "type": "split", "axis": "row", "share": 4000, "first": first, "second": { "type": "pane", "viewer": 2 } })
        };
        scene.present(serde_json::from_value(json!({ "sidebar": { "view": "workspace", "open": true }, "active": 1, "viewers": viewers, "layout": layout })).unwrap());
        use sessions::session::{Command, Overlay, ViewerId, Viewport};
        scene.command(Command::SetViewport {
            viewer: ViewerId(entries.len() as u32),
            viewport: Some(Viewport {
                x: 0.2,
                y: 0.1,
                w: 0.5,
                h: 0.5,
            }),
        });
        scene.command(Command::SetOverlay {
            overlay: Overlay::LaneNames,
            shown: false,
        });
        recording.view_state(name, scene.session());
        scene.reread_playlist("list", &[("list", &[("A", &[])])]);
        recording.view_state(&format!("{name}-resolved"), scene.session());
    }
    recording
}
