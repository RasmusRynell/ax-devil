//! The workspace file: what a round trip keeps, and what makes a whole load fail.

use foundation::{BookmarkId, HostPath, ItemId, MediaTime};
use handler_api::Settings;
use overlay_sync::MatchPolicy;
use serde_json::{Value, json};
use std::{fs, path::Path};
use workspace::{
    Bookmark, BookmarkKind, BookmarkTarget, OverlayRecipe, PlaylistRecipe, Recipe, Source,
    Unreadable, VideoRecipe, Workspace, WorkspaceItem,
};
use workspace_files::{SUFFIX, load, save};

fn item(label: &str, source: Source) -> WorkspaceItem {
    WorkspaceItem {
        id: ItemId::generate(),
        label: label.into(),
        recipe: Recipe::new(source),
    }
}

fn video(path: &Path, overlay: &Path) -> Source {
    let options: Settings = json!({"unit": "px"}).as_object().cloned().unwrap();
    Source::Video(VideoRecipe {
        video: HostPath::new(path),
        overlays: vec![OverlayRecipe {
            path: HostPath::new(overlay),
            handler: "ONVIF_XML".into(),
            label: "gate.xml".into(),
            options,
        }],
    })
}

fn bookmark(target: BookmarkTarget, note: &str) -> Bookmark {
    Bookmark {
        id: BookmarkId::generate(),
        target,
        frame: 120,
        time: MediaTime::from_us(4_000_000),
        kind: BookmarkKind::Issue,
        note: note.into(),
    }
}

fn target(item: &WorkspaceItem, entry: Option<&str>) -> BookmarkTarget {
    BookmarkTarget {
        item: item.id,
        entry: entry.map(str::to_owned),
    }
}

fn sample(dir: &Path, outside: &Path) -> Workspace {
    let mut inside = item(
        "a.mp4",
        video(&dir.join("clips/a.mp4"), &dir.join("clips/a.xml")),
    );
    let policy = MatchPolicy {
        basis: None,
        ..MatchPolicy::default()
    };
    inside
        .recipe
        .lane_matching
        .insert("gate.xml".into(), policy);
    let elsewhere = item("b.mp4", video(outside, outside));
    let playlist = item(
        "MOT17",
        Source::Playlist(PlaylistRecipe {
            resolver: "mot_challenge".into(),
            settings: json!({"folder": "/data/MOT17"})
                .as_object()
                .cloned()
                .unwrap(),
        }),
    );
    let id = ItemId::generate();
    let unknown = WorkspaceItem {
        id,
        label: "live_stream".into(),
        recipe: Recipe::new(Source::Unreadable(Unreadable {
            raw: json!({"kind": "live_stream", "id": id.to_string(), "extra": [1, {"z": null}]}),
            reason: "Unknown item kind: \"live_stream\". A newer version or a missing plugin?"
                .into(),
        })),
    };
    let bookmarks = vec![
        bookmark(target(&inside, None), "video"),
        bookmark(target(&playlist, Some("MOT17-02")), "entry"),
        bookmark(target(&playlist, Some("removed")), "orphan"),
    ];
    let reviewed = [
        target(&playlist, Some("MOT17-02")),
        target(&inside, None),
        target(&playlist, None),
    ]
    .into_iter()
    .collect();
    Workspace {
        name: Some("Gate review".into()),
        items: vec![inside, elsewhere, playlist, unknown],
        bookmarks,
        reviewed,
    }
}

fn read_json(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

#[test]
fn a_workspace_round_trips_with_paths_inside_its_folder_relative() {
    let temp = tempfile::tempdir().unwrap();
    let outside = tempfile::tempdir().unwrap();
    let (dir, outside) = (temp.path().join("one"), outside.path().join("b.mp4"));
    fs::create_dir(&dir).unwrap();
    let mut original = sample(&dir, &outside);
    original.items.push(item(
        "Unreadable item",
        Source::Unreadable(Unreadable {
            raw: json!("not an item"),
            reason: "This item is not a JSON object.".into(),
        }),
    ));
    let file = dir.join(format!("ws{SUFFIX}"));
    save(&original, &file).unwrap();

    let json = read_json(&file);
    assert_eq!(json["version"], 2);
    assert_eq!(json["items"][0]["video"], "clips/a.mp4");
    assert_eq!(json["items"][0]["overlays"][0]["path"], "clips/a.xml");
    assert_eq!(json["items"][1]["video"], outside.to_str().unwrap());
    assert_eq!(json["items"][3], original_raw(&original, 3));
    assert_eq!(json["items"][4], "not an item");
    let reviewed: Vec<Value> = json["reviewed"].as_array().unwrap().clone();
    assert_eq!(reviewed.len(), 3);
    assert!(
        reviewed
            .windows(2)
            .all(|pair| pair[0]["item"].as_str() <= pair[1]["item"].as_str()),
        "reviewed is sorted: {reviewed:?}"
    );

    let mut loaded = load(&file).unwrap();
    // A non-object item has no id of its own in the file, so it gets a fresh one on each load.
    loaded.items[4].id = original.items[4].id;
    assert_eq!(loaded, original);

    let copy = temp.path().join("two");
    fs::rename(&dir, &copy).unwrap();
    let moved = load(&copy.join(format!("ws{SUFFIX}"))).unwrap();
    let Source::Video(recipe) = &moved.items[0].recipe.source else {
        panic!("a video item stays a video");
    };
    assert_eq!(recipe.video.as_path(), copy.join("clips/a.mp4"));
    let Source::Video(recipe) = &moved.items[1].recipe.source else {
        panic!("a video item stays a video");
    };
    assert_eq!(recipe.video.as_path(), outside);
}

fn original_raw(workspace: &Workspace, index: usize) -> Value {
    match &workspace.items[index].recipe.source {
        Source::Unreadable(unreadable) => unreadable.raw.clone(),
        _ => panic!("not an unreadable item"),
    }
}

#[cfg(unix)]
#[test]
fn saving_through_a_symlink_writes_the_target_and_anchors_to_its_folder() {
    let temp = tempfile::tempdir().unwrap();
    // Reach the folder through a symlinked parent, as macOS temp folders are (`/var` -> `/private/var`).
    let aliases = tempfile::tempdir().unwrap();
    let through = aliases.path().join("alias");
    std::os::unix::fs::symlink(temp.path(), &through).unwrap();
    let (real, links) = (through.join("real"), through.join("links"));
    fs::create_dir_all(&real).unwrap();
    fs::create_dir_all(&links).unwrap();
    let target = real.join(format!("ws{SUFFIX}"));
    let link = links.join(format!("ws{SUFFIX}"));
    fs::write(&target, "").unwrap();
    std::os::unix::fs::symlink(&target, &link).unwrap();

    let workspace = sample(&real, &temp.path().join("b.mp4"));
    save(&workspace, &link).unwrap();

    assert!(fs::symlink_metadata(&link).unwrap().is_symlink());
    assert_eq!(read_json(&target)["items"][0]["video"], "clips/a.mp4");
    let loaded = load(&link).unwrap();
    let Source::Video(recipe) = &loaded.items[0].recipe.source else {
        panic!("a video item stays a video");
    };
    let anchor = fs::canonicalize(&real).unwrap();
    assert_eq!(recipe.video.as_path(), anchor.join("clips/a.mp4"));

    // A plain file reached through the alias stores the same relative path.
    let plain = real.join(format!("plain{SUFFIX}"));
    save(&workspace, &plain).unwrap();
    assert_eq!(read_json(&plain)["items"][0]["video"], "clips/a.mp4");
}

#[test]
fn an_item_with_a_setting_the_format_does_not_know_is_kept_as_it_was() {
    let dir = tempfile::tempdir().unwrap();
    let item = |key: &str, matching: Value| {
        json!({"kind": "playlist", "id": ItemId::generate().to_string(), "label": key,
               "resolver": "r", "lane_matching": matching})
    };
    let slot = |extra: Value| {
        let mut policy =
            json!({"tolerance": "Exact", "sticky": {"enabled": false, "timeout": null}});
        policy
            .as_object_mut()
            .unwrap()
            .extend(extra.as_object().unwrap().clone());
        json!({"slot": policy})
    };
    let mut newer_sticky = slot(json!({}));
    newer_sticky["slot"]["sticky"]["newer"] = json!(1);
    let items = vec![
        item("plain", slot(json!({}))),
        item("policy", slot(json!({"newer": true}))),
        item("sticky", newer_sticky),
    ];
    let file = dir.path().join(format!("ws{SUFFIX}"));
    fs::write(&file, json!({"version": 2, "items": items}).to_string()).unwrap();

    let loaded = load(&file).unwrap();
    let readable = |i: usize| !matches!(loaded.items[i].recipe.source, Source::Unreadable(_));
    assert_eq!(
        [readable(0), readable(1), readable(2)],
        [true, false, false]
    );
    for i in [1, 2] {
        let Source::Unreadable(unreadable) = &loaded.items[i].recipe.source else {
            unreachable!();
        };
        assert!(
            unreadable.reason.contains("does not know"),
            "{}",
            unreadable.reason
        );
    }
    save(&loaded, &file).unwrap();
    assert_eq!(read_json(&file)["items"][1], items[1]);
    assert_eq!(read_json(&file)["items"][2], items[2]);
}

fn bookmark_json(item: &str, extra: Value) -> Value {
    let mut value = json!({
        "id": BookmarkId::generate().to_string(), "item": item, "frame": 1, "time_us": 0,
    });
    value
        .as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    value
}

#[test]
fn a_file_the_format_forbids_fails_the_whole_load_naming_the_file() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join(format!("bad{SUFFIX}"));
    let item = ItemId::generate().to_string();
    let playlist = json!({"kind": "playlist", "id": item, "label": "p", "resolver": "r"});
    let document = |extra: Value| {
        let mut document = json!({"version": 2, "items": [playlist]});
        document
            .as_object_mut()
            .unwrap()
            .extend(extra.as_object().unwrap().clone());
        document.to_string()
    };
    let other = ItemId::generate().to_string();
    let texts = [
        r#"{"version": 1, "items": []}"#.to_owned(),
        r#"{"version": "2", "items": []}"#.to_owned(),
        r#"{"version": 2.0, "items": []}"#.to_owned(),
        r#"{"version": true, "items": []}"#.to_owned(),
        r#"{"items": []}"#.to_owned(),
        r#"{"version": 2, "items": [], "x": NaN}"#.to_owned(),
        document(json!({"items": [playlist, playlist]})),
        document(json!({"bookmarks": [
            bookmark_json(&item, json!({"id": item})),
            bookmark_json(&item, json!({"id": item})),
        ]})),
        document(json!({"bookmarks": [bookmark_json(&other, json!({}))]})),
        document(json!({"reviewed": [{"item": other}]})),
        document(json!({"bookmarks": [bookmark_json(&item, json!({"time_us": -1}))]})),
        document(json!({"bookmarks": [bookmark_json(&item, json!({"kind": "praise"}))]})),
    ];
    for text in texts {
        fs::write(&file, &text).unwrap();
        let error = load(&file).unwrap_err();
        assert!(
            error.reason.contains(&format!("bad{SUFFIX}")),
            "{text}: {}",
            error.reason
        );
    }
    fs::write(
        &file,
        document(json!({"bookmarks": [bookmark_json(&item, json!({}))]})),
    )
    .unwrap();
    load(&file).unwrap();
}
