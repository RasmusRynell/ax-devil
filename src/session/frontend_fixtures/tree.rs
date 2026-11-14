//! Scenarios of the workspace tree.

use super::outcomes::Groups;
use super::{Build, Recording, Scene, close_viewer, open_viewer, remove, set_hidden};
use serde_json::json;

/// The scenarios of this file.
pub(super) const SCENARIOS: &[(&str, Build)] = &[
    ("tree", tree),
    ("tree-kinds", kinds),
    ("tree-sort", sort),
    ("tree-locations", locations),
    ("runs", runs),
    ("runs-lanes", runs_lanes),
    ("reveal", reveal),
    ("reveal-names", reveal_names),
    ("long-playlist", long_playlist),
    ("nine-videos", nine_videos),
    ("many-videos", many_videos),
    ("tree-info", info),
];

/// The playlist `mot`: entry A with lanes GT and DPM, entry B with GT.
const MOT: Groups = &[("Train", &[("A", &["GT", "DPM"]), ("B", &["GT"])])];

/// Hides the DPM lane of `mot/A` and the entry `mot/B`.
fn hide_in_mot(scene: &mut Scene) {
    scene.hide("mot/A", Some("DPM"));
    scene.hide("mot/B", None);
}

/// A closed playlist `mot`, a video `v` without lanes and a failed `bad`; the DPM lane of A and the entry B are
/// hidden. Steps: hide A; open `mot`; unhide the DPM lane and then open `mot` (a client sends those two commands one
/// after the other); remove `v`; open `bad`.
fn tree() -> Recording {
    let mut scene = Scene::new();
    scene.playlist("mot", MOT);
    scene.video("v", "v.mp4", &[]);
    scene.failed("bad", "bad.mp4");
    hide_in_mot(&mut scene);
    let ids = scene.ids().clone();
    let mut recording = Recording::new(&scene);
    let initial = scene.session().clone();
    recording.state("initial", &initial);

    recording.branch(
        "initial",
        &initial,
        set_hidden(&ids["mot/A"], None, true),
        "a-hidden",
    );
    recording.branch("initial", &initial, open_viewer(&ids["mot"]), "mot-open");
    recording.branch("initial", &initial, remove(&ids["v"]), "v-removed");
    recording.branch("initial", &initial, open_viewer(&ids["bad"]), "bad-open");
    let mut session = initial.clone();
    let shown = set_hidden(&ids["mot/A"], Some("DPM"), false);
    recording.step("initial", &mut session, shown, "dpm-shown");
    let open = open_viewer(&ids["mot"]);
    recording.step("dpm-shown", &mut session, open, "dpm-open");
    let (v, mot) = (&ids["v"], &ids["mot"]);
    let swap = [
        (open_viewer(v), "v-open"),
        (open_viewer(mot), "v-mot-open"),
        (close_viewer(v), "swapped"),
    ];
    recording.chain("initial", &initial, swap);
    let hide = [
        (set_hidden(&ids["mot/A"], Some("GT"), true), "gt-hidden"),
        (set_hidden(&ids["mot/A"], None, true), "gt-a-hidden"),
    ];
    recording.chain("initial", &initial, hide);
    let show_b = set_hidden(&ids["mot/B"], None, false);
    recording.branch("initial", &initial, show_b, "b-shown");
    recording
}

/// One of each kind of item: `mot` as in `tree`, a video `v` with a lane GT, a failed `bad`, a resolving `wait`, an
/// unreadable `odd`, live streams `cam` (Lobby) and `dock` (Dock), and a video `w`. States: `initial` and `all-open`
/// (`cam`, `dock`, `w` and `v` have viewers). Step: open `cam`.
fn kinds() -> Recording {
    let mut scene = Scene::new();
    scene.playlist("mot", MOT);
    scene.video("v", "v.mp4", &["GT"]);
    scene.failed("bad", "bad.mp4");
    scene.pending("wait", "wait.mp4");
    scene.loaded("odd", json!({ "kind": "mystery" }));
    scene.live("cam", Some("Lobby"), "10.0.0.5");
    scene.live("dock", Some("Dock"), "10.0.0.6");
    scene.video("w", "w.mp4", &[]);
    hide_in_mot(&mut scene);
    let ids = scene.ids().clone();
    let mut recording = Recording::new(&scene);
    let initial = scene.session().clone();
    recording.state("initial", &initial);
    let cam = [
        (open_viewer(&ids["cam"]), "cam-open"),
        (close_viewer(&ids["cam"]), "cam-closed"),
    ];
    recording.chain("initial", &initial, cam);
    let dock = open_viewer(&ids["dock"]);
    recording.branch("initial", &initial, dock, "dock-open");
    for key in ["cam", "dock", "w", "v"] {
        scene.open(key);
    }
    recording.state("all-open", scene.session());
    recording
}

/// Playlists whose names differ in case, accents, dots and scripts, and two with entries, which sort in
/// different orders by workspace and by name.
fn sort() -> Recording {
    let mut scene = Scene::new();
    scene.playlist("b", &[("b", &[("1", &["Z"]), ("0", &["Y"])])]);
    scene.playlist("a", &[("a", &[("1", &["X"])])]);
    for name in [
        "B",
        "zebra",
        "éclair",
        "eagle",
        "😀",
        "\u{e000}",
        "ırmak",
        "irmak",
        "İstanbul",
        "Straße",
        "οδος",
    ] {
        scene.playlist(name, &[(name, &[("1", &[])])]);
    }
    Recording::initial(&scene)
}

/// Videos in folders that differ in case, two of them named alike, and two failed ones that have no location.
/// State `added` has one more video.
fn locations() -> Recording {
    let mut scene = Scene::new();
    scene.failed("none", "none.mp4");
    scene.video("c", "c.mp4", &[]);
    scene.video("B", "B.mp4", &[]);
    scene.failed("also-none", "also-none.mp4");
    scene.video("a", "a.mp4", &[]);
    scene.video("p", "same.mp4", &[]);
    scene.video("q", "same.mp4", &[]);
    let mut recording = Recording::initial(&scene);
    scene.video("ab", "ab.mp4", &[]);
    recording.state("added", scene.session());
    recording
}

/// One item with two playlists, Train and Train (2), of one entry each. Step: open the item.
fn runs() -> Recording {
    let mut scene = Scene::new();
    let groups: Groups = &[
        ("Train", &[("A", &["GT"])]),
        ("Train (2)", &[("A", &["GT"])]),
    ];
    scene.playlist("runs", groups);
    let ids = scene.ids().clone();
    let mut recording = Recording::new(&scene);
    let initial = scene.session().clone();
    recording.state("initial", &initial);
    recording.branch("initial", &initial, open_viewer(&ids["runs"]), "open");
    recording
}

/// One item `runs` with a playlist Train of an entry A with a lane GT and a playlist Test of an entry B with two
/// lanes, GT and DPM.
fn runs_lanes() -> Recording {
    let mut scene = Scene::new();
    scene.playlist(
        "runs",
        &[
            ("Train", &[("A", &["GT"])]),
            ("Test", &[("B", &["GT", "DPM"])]),
        ],
    );
    Recording::initial(&scene)
}

/// Playlists `mot` and `other` and a video `v`. States: `initial`, `mot-open` (by a step), `open` (all three),
/// `resolving` (`mot` resolves again), `reread-b` (`mot` read again with only the entry B) and `reread-a` (read
/// again with both entries once more).
fn reveal() -> Recording {
    let mut scene = Scene::new();
    scene.playlist("mot", MOT);
    scene.playlist("other", &[("other", &[("X", &["GT"])])]);
    scene.video("v", "v.mp4", &[]);
    let ids = scene.ids().clone();
    let mut recording = Recording::new(&scene);
    let initial = scene.session().clone();
    recording.state("initial", &initial);
    recording.branch("initial", &initial, open_viewer(&ids["mot"]), "mot-open");
    let mut resolving = scene.clone();
    resolving.resolving("mot");
    recording.state("resolving", resolving.session());
    let mut reread = scene.clone();
    reread.reread_playlist("mot", &[("Train", &[("B", &["GT"])])]);
    recording.state("reread-b", reread.session());
    reread.reread_playlist("mot", MOT);
    recording.state("reread-a", reread.session());
    for key in ["mot", "other", "v"] {
        scene.open(key);
    }
    recording.state("open", scene.session());
    recording
}

/// Playlists `mot` and `other`, each with an entry A, and a video `v` with a lane GT.
fn reveal_names() -> Recording {
    let mut scene = Scene::new();
    scene.playlist("mot", &[("mot", &[("A", &["GT"])])]);
    scene.playlist("other", &[("other", &[("A", &["GT"])])]);
    scene.video("v", "v.mp4", &["GT"]);
    Recording::initial(&scene)
}

/// Playlists of 200 and 201 entries, which have no lanes, and, in state `added`, a video.
fn long_playlist() -> Recording {
    let mut scene = Scene::new();
    for (key, count) in [("p200", 200), ("p201", 201)] {
        let names: Vec<String> = (0..count).map(|at| at.to_string()).collect();
        let entries: Vec<(&str, &[&str])> =
            names.iter().map(|name| (name.as_str(), &[][..])).collect();
        scene.playlist(key, &[(key, entries.as_slice())]);
    }
    let mut recording = Recording::initial(&scene);
    scene.video("v", "v.mp4", &[]);
    recording.state("added", scene.session());
    recording
}

/// Nine videos, `v1` to `v9`. Step `open-n` opens the first `n` of them, one after the other.
fn nine_videos() -> Recording {
    let mut scene = Scene::new();
    for at in 1..=9 {
        scene.video(&format!("v{at}"), &format!("v{at}.mp4"), &[]);
    }
    let ids = scene.ids().clone();
    let mut recording = Recording::new(&scene);
    let mut session = scene.session().clone();
    recording.state("initial", &session);
    let mut from = "initial".to_owned();
    for at in 1..=8 {
        let to = format!("open-{at}");
        recording.step(
            &from,
            &mut session,
            open_viewer(&ids[&format!("v{at}")]),
            &to,
        );
        from = to;
    }
    recording
}

/// Fifty videos, more than a window draws.
fn many_videos() -> Recording {
    let mut scene = Scene::new();
    for at in 1..=50 {
        scene.video(&format!("v{at}"), &format!("v{at}.mp4"), &[]);
    }
    Recording::initial(&scene)
}

/// A playlist `runs` the resolver "Folder Pair" found one match in, whose one entry has one overlay file; the
/// playlist, the entry and the overlay each state a fact.
fn info() -> Recording {
    let mut scene = Scene::new();
    let groups: Groups = &[("Train", &[("SEQ-01", &["GT"])])];
    scene.playlist_with("runs", "Folder Pair", groups, |resolved| {
        let playlist = &mut resolved.playlists[0];
        playlist.matches = Some(1);
        playlist.metadata.insert("owner".into(), "lab".into());
        let entry = &mut playlist.entries[0];
        entry.metadata.insert("split".into(), "train".into());
        entry.overlays[0]
            .metadata
            .insert("detector".into(), "Yes".into());
    });
    Recording::initial(&scene)
}
