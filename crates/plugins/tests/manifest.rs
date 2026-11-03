//! What makes a plugin loadable: the manifest's rules and the code identity.

mod common;

use std::{fs, path::Path, sync::Arc};

use common::{TestSink, base, decode, install};
use foundation::CancelToken;
use handler_api::{Cli, Decoder, FieldKind, PlaylistResolver, Settings};
use plugins::{LoadedPlugin, Manifest, PluginDecoder};
use serde_json::{Value, json};

/// Changes the base manifest into one a field of which is wrong.
type Edit = Box<dyn FnOnce(&mut Value)>;

/// The first decoder of the plugin at `path`.
fn decoder(path: &Path) -> Arc<PluginDecoder> {
    LoadedPlugin::load(path).expect("loads").decoders.remove(0)
}

/// The manifest field a refusal names, for a manifest with `edit` applied to the base one.
fn refused(edit: impl FnOnce(&mut Value)) -> (Option<String>, String) {
    let folder = tempfile::tempdir().expect("folder");
    let mut manifest = base();
    edit(&mut manifest);
    let path = install(folder.path(), &manifest);
    let error = Manifest::load(&path).expect_err("the manifest is refused");
    (error.field, error.reason)
}

#[test]
fn every_refusal_names_the_manifest_field() {
    let cases: Vec<(&str, Edit)> = vec![
        ("manifest", Box::new(|m| m["manifest"] = json!(3))),
        ("protocol", Box::new(|m| m["protocol"] = json!(2))),
        (
            "handlers[0].scene_version",
            Box::new(|m| m["handlers"][0]["scene_version"] = json!("5.0")),
        ),
        (
            "handlers[0].scene_version",
            Box::new(|m| m["handlers"][0]["scene_version"] = json!("4.1")),
        ),
        (
            "env",
            Box::new(|m| m["env"] = json!(["AX_DEVIL_TARGET_PASS"])),
        ),
        (
            "command",
            Box::new(|m| drop(m.as_object_mut().expect("object").remove("command"))),
        ),
        ("command", Box::new(|m| m["command"] = json!([]))),
        (
            "command",
            Box::new(|m| m["command"] = json!(["./missing.sh"])),
        ),
        ("id", Box::new(|m| m["id"] = json!("Not Valid"))),
        (
            "handlers[0].extensions",
            Box::new(|m| m["handlers"][0]["extensions"] = json!([".ACME"])),
        ),
        ("code", Box::new(|m| m["code"] = json!(["../elsewhere"]))),
        ("code", Box::new(|m| m["code"] = json!(["no-such-file.py"]))),
        (
            "idle_timeout_s",
            Box::new(|m| m["idle_timeout_s"] = json!(0)),
        ),
        (
            "handlers[0].options[0].kind",
            Box::new(|m| m["handlers"][0]["options"][0]["kind"] = json!("slider")),
        ),
        (
            "handlers",
            Box::new(|m| m["handlers"][0]["kind"] = json!("encoder")),
        ),
        (
            "handlers[0].kind",
            Box::new(|m| {
                drop(
                    m["handlers"][0]
                        .as_object_mut()
                        .expect("object")
                        .remove("kind"),
                )
            }),
        ),
        (
            "handlers[0].id",
            Box::new(|m| {
                m["handlers"] = json!([{
                    "kind": "playlist_resolver", "id": "help", "name": "Help", "description": "Runs"
                }]);
            }),
        ),
    ];
    for (field, edit) in cases {
        let (named, reason) = refused(edit);
        assert_eq!(named.as_deref(), Some(field), "{reason}");
    }
    let (_, reason) = refused(|m| m["manifest"] = json!(3));
    assert!(reason.contains("newer ax-devil"), "{reason}");
}

#[test]
fn more_than_20000_files_of_code_are_refused() {
    let folder = tempfile::tempdir().expect("folder");
    let mut manifest = base();
    manifest["code"] = json!(["data"]);
    let path = install(folder.path(), &manifest);
    let data = folder.path().join("data");
    fs::create_dir(&data).expect("data");
    for number in 0..=20_000 {
        fs::write(data.join(number.to_string()), "").expect("file");
    }
    let error = Manifest::load(&path).expect_err("too much code");
    assert_eq!(error.field.as_deref(), Some("code"));
}

#[test]
fn the_code_hash_follows_the_hashed_files_and_not_dot_files() {
    let folder = tempfile::tempdir().expect("folder");
    let mut manifest = base();
    manifest["code"] = json!(["."]);
    let path = install(folder.path(), &manifest);
    let decoder = decoder(&path);
    let first = decoder.program().code_hash().expect("hash");

    fs::write(folder.path().join(".hidden"), "x").expect("dot file");
    fs::create_dir(folder.path().join("__pycache__")).expect("cache folder");
    fs::write(folder.path().join("__pycache__/convert.pyc"), "x").expect("cache file");
    assert_eq!(
        decoder.program().code_hash().expect("hash"),
        first,
        "skipped files are not identity"
    );

    fs::write(
        folder.path().join("convert.py"),
        "print('edited, and longer')\n",
    )
    .expect("edit");
    let edited = decoder.program().code_hash().expect("hash");
    assert_ne!(edited, first);

    fs::write(folder.path().join("helper.py"), "x = 1\n").expect("new file");
    assert_ne!(
        decoder.program().code_hash().expect("hash"),
        edited,
        "a new file is identity"
    );
}

#[test]
fn the_code_hash_names_the_manifest_that_was_loaded_not_the_file_on_disk() {
    let folder = tempfile::tempdir().expect("folder");
    let mut manifest = base();
    manifest["code"] = json!(["."]);
    let path = install(folder.path(), &manifest);
    let decoder = decoder(&path);
    let loaded = decoder.program().code_hash().expect("hash");

    manifest["name"] = json!("Edited while running");
    install(folder.path(), &manifest);
    assert_eq!(
        decoder.program().code_hash().expect("hash"),
        loaded,
        "the running decoder keeps the identity of the manifest it runs"
    );
    let restarted = self::decoder(&path);
    assert_ne!(restarted.program().code_hash().expect("hash"), loaded);
}

#[test]
fn the_store_gets_the_code_hash_and_a_plugin_whose_files_cannot_be_hashed_refuses_to_run() {
    let folder = tempfile::tempdir().expect("folder");
    let mut manifest = base();
    manifest["code"] = json!(["convert.py"]);
    let path = install(folder.path(), &manifest);
    let decoder = decoder(&path);
    assert_eq!(
        Decoder::code_hash(&*decoder),
        decoder.program().code_hash().ok()
    );
    assert!(Decoder::code_hash(&*decoder).is_some());

    fs::remove_file(folder.path().join("convert.py")).expect("remove");

    assert_eq!(Decoder::code_hash(&*decoder), None);
    let run = decode(
        &decoder,
        &folder.path().join("a.test"),
        &Settings::new(),
        &CancelToken::new(),
        TestSink::default(),
    );
    assert!(
        run.failure().contains("files cannot be read"),
        "{}",
        run.failure()
    );
    assert!(run.sink.samples.is_empty());
}

#[test]
fn options_are_filled_from_defaults_and_restricted_to_the_declared_keys() {
    let folder = tempfile::tempdir().expect("folder");
    let decoder = decoder(&install(folder.path(), &base()));
    let given = |pairs: &[(&str, &str)]| {
        pairs
            .iter()
            .map(|(key, value)| ((*key).to_owned(), Value::from(*value)))
            .collect()
    };
    let form = &decoder.spec().options;
    let omitted = form
        .validate(&given(&[("other", "1")]))
        .expect("options")
        .declared();
    let explicit = form
        .validate(&given(&[("camera", "front")]))
        .expect("options")
        .declared();
    assert_eq!(omitted, explicit);
    assert_eq!(omitted["camera"], "front");
    assert_eq!(omitted.len(), 1);
}

#[test]
fn a_plugin_provides_decoders_and_resolvers_skips_unknown_kinds_and_refuses_a_handler_id_twice() {
    let folder = tempfile::tempdir().expect("folder");
    let mut manifest = base();
    let second = json!({
        "kind": "decoder", "id": "acme_times", "name": "ACME times", "input": "path",
        "scene_version": "4.0", "extensions": [".times"]
    });
    let runs = json!({
        "kind": "playlist_resolver", "id": "acme_runs", "name": "ACME runs", "description": "Runs"
    });
    let handlers = manifest["handlers"].as_array_mut().expect("list");
    handlers.push(second);
    handlers.push(runs.clone());
    // A kind this build does not know is set aside unread, takes no id, and leaves the rest working.
    handlers.push(json!({ "kind": "encoder", "id": "acme", "future": [1] }));
    handlers.push(json!({ "kind": "encoder" }));
    let plugin = LoadedPlugin::load(&install(folder.path(), &manifest)).expect("three handlers");
    let ids: Vec<&str> = plugin
        .decoders
        .iter()
        .map(|d| d.spec().id.as_str())
        .collect();
    assert_eq!(ids, ["acme", "acme_times"]);
    assert_eq!(plugin.resolvers[0].spec().id, "acme_runs");
    let skipped: Vec<_> = plugin
        .skipped
        .iter()
        .map(|handler| (handler.kind.as_str(), handler.id.as_deref()))
        .collect();
    assert_eq!(skipped, [("encoder", Some("acme")), ("encoder", None)]);
    assert!(plugin.skipped[0].reason.contains("newer ax-devil"));
    assert_eq!(plugin.resolvers[0].spec().plugin, "acme");
    assert!(Arc::ptr_eq(
        plugin.decoders[0].program(),
        plugin.decoders[1].program()
    ));

    let (field, reason) = refused(|m| {
        let mut twin = runs;
        twin["id"] = json!("acme");
        m["handlers"].as_array_mut().expect("list").push(twin);
    });
    assert_eq!(field.as_deref(), Some("handlers[1].id"), "{reason}");
}

#[test]
fn resolver_settings_of_every_kind_load_and_each_refusal_names_the_field() {
    let setting = |extra: Value| {
        let mut manifest = base();
        manifest["handlers"] = json!([{
            "kind": "playlist_resolver", "id": "acme_runs", "name": "ACME runs", "description": "Runs",
            "settings": [
                { "key": "root", "label": "Root", "kind": "folder", "required": true, "positional": true },
                { "key": "list", "label": "List", "kind": "file", "extensions": [".txt"] },
                { "key": "note", "label": "Note", "kind": "text", "default": "x" },
                { "key": "depth", "label": "Depth", "kind": "integer", "min": 1, "default": 2 },
                { "key": "handler_type", "label": "Decoder", "kind": "handler" },
                { "key": "options", "label": "Options", "kind": "decoder_options", "handler_field": "handler_type" },
                { "key": "runs", "label": "Runs", "kind": "scan", "depends_on": ["root"] },
                extra
            ]
        }]);
        manifest
    };
    let folder = tempfile::tempdir().expect("folder");
    let counted =
        json!({ "missing": "Pick a root.", "found": "{count} runs.", "none": "No runs." });
    let valid = setting(json!({ "key": "all", "label": "All", "kind": "scan", "count": counted }));
    let plugin = LoadedPlugin::load(&install(folder.path(), &valid)).expect("every kind loads");
    let form = &plugin.resolvers[0].spec().form;
    assert_eq!(form.fields.len(), 8);
    assert_eq!(form.fields[0].cli, Cli::Positional);
    assert_eq!(form.fields[3].kind, FieldKind::Integer { min: 1 });
    assert!(!form.fields[7].holds_value());
    assert_eq!(form.fields[4].cli, Cli::Flag("handler-type".to_owned()));
    let empty = setting(json!({ "key": "all", "label": "All", "kind": "scan", "depends_on": [] }));
    LoadedPlugin::load(&install(folder.path(), &empty)).expect("empty depends_on");

    // Refusals in the settings, and a kind only a resolver's settings take.
    let cases = [
        (
            json!({ "key": "a", "label": "A", "kind": "slider" }),
            "handlers[0].settings[7].kind",
        ),
        (
            json!({ "key": "a", "label": "A", "kind": "scan", "depends_on": ["nope"] }),
            "handlers[0].settings[7].depends_on",
        ),
        (
            json!({ "key": "a", "label": "A", "kind": "scan", "count": { "missing": "m", "found": "none", "none": "n" } }),
            "handlers[0].settings[7].count.found",
        ),
        (
            json!({ "key": "a", "label": "A", "kind": "decoder_options", "handler_field": "note" }),
            "handlers[0].settings[7].handler_field",
        ),
        (
            json!({ "key": "a", "label": "A", "kind": "integer", "min": 1, "default": 0 }),
            "handlers[0].settings[7].default",
        ),
        (
            json!({ "key": "a", "label": "A", "kind": "scan", "depends_on": ["runs"] }),
            "handlers[0].settings[7].depends_on",
        ),
        (
            json!({ "key": "a", "label": "A", "kind": "scan", "depends_on": ["note"], "required": true }),
            "handlers[0].settings[7].required",
        ),
        (
            json!({ "key": "a", "label": "A", "kind": "text", "positional": true }),
            "handlers[0].settings[7].positional",
        ),
        (
            json!({ "key": "a", "label": "A", "kind": "text", "required": true, "default": "x", "positional": true }),
            "handlers[0].settings[7].positional",
        ),
        (
            json!({ "key": "a", "label": "A", "kind": "text", "min": 1 }),
            "handlers[0].settings[7].min",
        ),
        (
            json!({ "key": "note", "label": "A", "kind": "text" }),
            "handlers[0].settings[7].key",
        ),
        (
            json!({ "key": "handler-type", "label": "A", "kind": "text" }),
            "handlers[0].settings[7].key",
        ),
        (
            json!({ "key": "-x", "label": "A", "kind": "text" }),
            "handlers[0].settings[7].key",
        ),
        (
            json!({ "key": "_x", "label": "A", "kind": "text" }),
            "handlers[0].settings[7].key",
        ),
        (
            json!({ "key": "log_level", "label": "A", "kind": "text" }),
            "handlers[0].settings[7].key",
        ),
    ];
    for (extra, field) in cases {
        let folder = tempfile::tempdir().expect("folder");
        let path = install(folder.path(), &setting(extra));
        let error = Manifest::load(&path).expect_err("refused");
        assert_eq!(error.field.as_deref(), Some(field), "{error}");
    }
    let (field, _) = refused(|m| {
        m["handlers"][0]["options"] = json!([{ "key": "n", "label": "N", "kind": "integer" }]);
    });
    assert_eq!(field.as_deref(), Some("handlers[0].options[0].kind"));
}
