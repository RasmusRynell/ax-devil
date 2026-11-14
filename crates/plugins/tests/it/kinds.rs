//! The manifest's task, view, command and lanes-decoder kinds: what loads, what is skipped, what is refused.

use crate::common::{base, install};

use std::fs;

use plugins::{LoadedPlugin, Manifest};
use serde_json::{Value, json};

/// Changes the base manifest into one a field of which is wrong.
type Edit = Box<dyn FnOnce(&mut Value)>;

/// The manifest field a refusal names, for a manifest with `edit` applied to the base one.
fn refused(edit: impl FnOnce(&mut Value)) -> (Option<String>, String) {
    let folder = tempfile::tempdir().expect("folder");
    let mut manifest = base();
    edit(&mut manifest);
    let path = install(folder.path(), &manifest);
    let error = Manifest::load(&path).expect_err("the manifest is refused");
    (error.field, error.reason)
}

/// A manifest of one view and one command over it, with no program.
fn views_only() -> Value {
    json!({
        "manifest": 2, "id": "pages", "name": "Pages", "version": "1", "protocol": 3,
        "handlers": [
            { "kind": "view", "id": "page", "title": "Page", "place": "video", "document": "page.html" },
            { "kind": "command", "id": "open_page", "title": "Open", "on": "app", "runs": "page" }
        ]
    })
}

#[test]
fn a_plugin_of_views_and_commands_has_no_program_but_a_task_needs_one() {
    let folder = tempfile::tempdir().expect("folder");
    let path = install(folder.path(), &views_only());
    fs::write(folder.path().join("page.html"), "<p>hi</p>").expect("page");
    let plugin = LoadedPlugin::load(&path).expect("loads without a program");
    assert!(plugin.manifest.program.is_empty() && plugin.skipped.is_empty());
    assert!(plugin.decoders.is_empty() && plugin.tasks.is_empty());

    let mut with_task = views_only();
    with_task["handlers"]
        .as_array_mut()
        .expect("list")
        .push(json!({ "kind": "task", "id": "work", "name": "Work", "lanes": [{ "key": "a", "label": "A" }] }));
    let error =
        Manifest::load(&install(folder.path(), &with_task)).expect_err("a task runs the program");
    assert_eq!(error.field.as_deref(), Some("program"));
}

#[test]
fn a_handler_whose_document_or_runs_does_not_resolve_is_skipped_with_its_reason() {
    let folder = tempfile::tempdir().expect("folder");
    let mut manifest = base();
    let handlers = manifest["handlers"].as_array_mut().expect("list");
    handlers.extend([
        json!({ "kind": "view", "id": "page", "title": "Page", "place": "sidebar", "document": "missing.html" }),
        json!({ "kind": "command", "id": "open", "title": "Open", "on": "app", "runs": "page" }),
        json!({ "kind": "command", "id": "path_decoder", "title": "Decode", "on": "lane", "runs": "acme" }),
        json!({ "kind": "command", "id": "nothing", "title": "Nothing", "on": "item", "runs": "nope" }),
    ]);
    let plugin =
        LoadedPlugin::load(&install(folder.path(), &manifest)).expect("the decoder still loads");

    assert_eq!(plugin.decoders.len(), 1);
    let skipped: Vec<_> = plugin
        .skipped
        .iter()
        .map(|handler| {
            (
                handler.kind.as_str(),
                handler.id.as_deref().unwrap_or_default(),
            )
        })
        .collect();
    assert_eq!(
        skipped,
        [
            ("view", "page"),
            ("command", "open"),
            ("command", "path_decoder"),
            ("command", "nothing")
        ]
    );
    assert!(
        plugin.skipped[0].reason.contains("missing.html"),
        "{}",
        plugin.skipped[0].reason
    );
    assert!(
        plugin.skipped[3].reason.contains("'nope'"),
        "{}",
        plugin.skipped[3].reason
    );
}

#[test]
fn the_new_kinds_and_settings_are_refused_naming_the_field() {
    let lanes = json!([{ "key": "a", "label": "A" }]);
    let cases: Vec<(&str, Edit)> = vec![
        (
            "handlers[0].lanes",
            Box::new(|m| m["handlers"][0]["input"] = json!("lanes")),
        ),
        (
            "handlers[0].lanes",
            Box::new(|m| m["handlers"][0]["lanes"] = json!([{ "key": "a", "label": "A" }])),
        ),
        (
            "handlers[0].lanes",
            Box::new(|m| {
                m["handlers"][0] = json!({ "kind": "task", "id": "t", "name": "T", "lanes": [] });
            }),
        ),
        (
            "handlers[0].lanes[1].key",
            Box::new(move |m| {
                m["handlers"][0] = json!({
                    "kind": "task", "id": "t", "name": "T",
                    "lanes": [{ "key": "a", "label": "A" }, { "key": "a", "label": "B" }]
                });
            }),
        ),
        (
            "handlers[0].extensions",
            Box::new(move |m| {
                m["handlers"][0] = json!({
                    "kind": "decoder", "id": "d", "name": "D", "input": "lanes", "scene_version": "4.0",
                    "lanes": lanes, "extensions": [".d"]
                });
            }),
        ),
        (
            "handlers[0].place",
            Box::new(|m| {
                m["handlers"][0] = json!({
                    "kind": "view", "id": "v", "title": "V", "place": "window", "document": "v.html"
                });
            }),
        ),
        (
            "handlers[0].document",
            Box::new(|m| {
                m["handlers"][0] = json!({
                    "kind": "view", "id": "v", "title": "V", "place": "video", "document": "../v.html"
                });
            }),
        ),
        (
            "handlers[0].on",
            Box::new(|m| {
                m["handlers"][0] = json!({
                    "kind": "command", "id": "c", "title": "C", "on": "frame", "runs": "acme"
                });
            }),
        ),
        (
            "settings[0].kind",
            Box::new(|m| {
                m["settings"] = json!([{ "key": "n", "label": "N", "kind": "integer" }]);
            }),
        ),
    ];
    for (field, edit) in cases {
        let (named, reason) = refused(edit);
        assert_eq!(named.as_deref(), Some(field), "{reason}");
    }
}
