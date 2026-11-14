//! Scenarios of the shell, Live view and Bookmarks view tests.

use super::{Build, Recording, Scene, close_viewer, content, open_viewer, remove};
use foundation::BookmarkId;
use serde_json::{Value, json};
use workspace::store::{BookmarkCommand, DocCommand};
use workspace::{BookmarkKind, BookmarkTarget};

/// The scenarios of this file.
pub(super) const SCENARIOS: &[(&str, Build)] = &[
    ("two-videos", two_videos),
    ("streams", streams),
    ("bookmarks", bookmarks),
];

/// Videos `a` and `b` (`a.mp4`, `b.mp4`), with the viewers closed, then `a` open, `b` open, and both open.
fn two_videos() -> Recording {
    let mut scene = Scene::new();
    scene.video("a", "a.mp4", &[]);
    scene.video("b", "b.mp4", &[]);
    let mut recording = Recording::initial(&scene);
    scene.open("a");
    let a_open = scene.session().clone();
    recording.state("a-open", &a_open);
    let ids = scene.ids().clone();
    let close_a = close_viewer(&ids["a"]);
    recording.branch("a-open", &a_open, close_a, "a-closed");
    scene.close("a");
    scene.open("b");
    recording.state("b-open", scene.session());
    scene.open("a");
    recording.state("both-open", scene.session());
    recording
}

/// A live stream `lobby` and, from `pair` on, a second `dock`. Steps: open `lobby`, then close it; from `dock-open`,
/// open `lobby`. Remove `lobby` from `initial` and from `lobby-open`.
fn streams() -> Recording {
    let mut scene = Scene::new();
    scene.live("lobby", Some("Lobby"), "10.0.0.9");
    let ids = scene.ids().clone();
    let mut recording = Recording::new(&scene);
    let initial = scene.session().clone();
    recording.state("initial", &initial);
    let lobby = &ids["lobby"];
    let cycle = [
        (open_viewer(lobby), "lobby-open"),
        (close_viewer(lobby), "lobby-closed"),
    ];
    recording.chain("initial", &initial, cycle);
    recording.branch("initial", &initial, remove(lobby), "removed");
    let mut opened = scene.clone();
    opened.open("lobby");
    recording.branch(
        "lobby-open",
        opened.session(),
        remove(lobby),
        "open-removed",
    );

    scene.live("dock", Some("Dock"), "10.0.0.4");
    scene.open("dock");
    let mut session = scene.session().clone();
    recording.state("dock-open", &session);
    let ids = scene.ids().clone();
    recording.ids.clone_from(&ids);
    recording.step(
        "dock-open",
        &mut session,
        open_viewer(&ids["lobby"]),
        "both-open",
    );
    recording
}

/// A video `v` with two bookmarks (the second says Blur) and a playlist `mot` of entry A, bookmarked at frame 6, and a
/// bookmark of entry Z, which the playlist has since lost. Then: `v-open`; `renamed` (`v` is `w.mp4`); `sharp` (Blur
/// reads Sharp); `replaced` and `recovered` (`sharp`'s workspace opened from a file, and then restored from a
/// recovery copy, an epoch later each); `reviewed` (no bookmarks, A reviewed). Steps from `initial`: each bookmark change
/// a client may make to Blur, reviewing `v`, and opening `v`, then `mot`, then closing `v`.
fn bookmarks() -> Recording {
    let mut scene = Scene::new();
    scene.video("v", "v.mp4", &[]);
    let mot = scene.playlist("mot", &[("MOT", &[("A", &["GT"]), ("Z", &[])])]);
    scene.bookmark("v", 75, BookmarkKind::Note, "");
    scene.bookmark("v", 90, BookmarkKind::Issue, "Blur");
    scene.bookmark("mot/A", 6, BookmarkKind::Note, "");
    scene.bookmark("mot/Z", 4, BookmarkKind::Note, "");
    scene.reread_playlist("mot", &[("MOT", &[("A", &["GT"])])]);
    let mut recording = Recording::initial(&scene);
    let mut saved = scene.session().clone();
    std::sync::Arc::make_mut(&mut saved.doc).workspace.file = Some(foundation::HostPath::new(
        "/workspaces/review.ax-devil.workspace",
    ));
    recording.state("saved", &saved);
    let initial = scene.session().clone();
    let ids: Vec<BookmarkId> = {
        let marks = &initial.doc.workspace.workspace.bookmarks;
        marks.iter().map(|mark| mark.id).collect()
    };
    let (v, mot_id) = (recording.ids["v"].clone(), recording.ids["mot"].clone());
    let blur = ids[1].to_string();
    let mark = |command: Value| content("bookmark", json!({ "command": command }));
    let review = json!({ "type": "setReviewed", "item": &v, "reviewed": true });
    let by_the_user = [
        (json!({ "type": "remove", "id": blur }), "blur-removed"),
        (
            json!({ "type": "edit", "id": blur, "kind": "question" }),
            "blur-question",
        ),
        (
            json!({ "type": "edit", "id": blur, "note": "" }),
            "blur-unnamed",
        ),
        (review, "v-reviewed"),
    ];
    for (command, to) in by_the_user {
        recording.branch("initial", &initial, mark(command), to);
    }
    let front = [
        (open_viewer(&v), "v-open"),
        (open_viewer(&mot_id), "both-open"),
        (close_viewer(&v), "mot-open"),
    ];
    recording.chain("initial", &initial, front);
    scene.open("v");
    recording.state("v-open", scene.session());
    scene.close("v");
    scene.rename("v", "w.mp4");
    recording.state("renamed", scene.session());
    scene.apply(DocCommand::Bookmarks(BookmarkCommand::Edit {
        id: ids[1],
        note: Some("Sharp".into()),
        kind: None,
    }));
    recording.state("sharp", scene.session());
    let sharp = scene.session().clone();
    recording.branch("sharp", &sharp, open_viewer(&v), "sharp-open");
    let mut replaced = scene.clone();
    replaced.reopen();
    recording.state("replaced", replaced.session());
    replaced.restore();
    recording.state("recovered", replaced.session());
    for id in ids {
        scene.apply(DocCommand::Bookmarks(BookmarkCommand::Remove(id)));
    }
    scene.apply(DocCommand::Bookmarks(BookmarkCommand::SetReviewed {
        target: BookmarkTarget {
            item: mot,
            entry: Some("MOT/A".into()),
        },
        reviewed: true,
    }));
    recording.state("reviewed", scene.session());
    recording
}
