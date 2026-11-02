//! What makes a converter loadable: the manifest's rules, the code identity, and the registry.

mod common;

use std::{fs, path::Path};

use common::{TestSink, decode};
use converters::{ConverterDecoder, MANIFEST_FILE, Manifest, Registry, load};
use foundation::CancelToken;
use handler_api::{Decoder, Settings};
use serde_json::{Value, json};

/// Changes the base manifest into one a field of which is wrong.
type Edit = Box<dyn FnOnce(&mut Value)>;

fn base() -> Value {
    json!({
        "manifest": 1, "kind": "file", "id": "acme", "name": "ACME", "version": "1", "protocol": 2,
        "scene_version": "4.0", "extensions": [".acme"], "command": ["python3", "convert.py"],
        "env": ["PYTHONPATH"], "code": ["convert.py"],
        "options": [{ "key": "camera", "label": "Camera", "kind": "text", "default": "front" }]
    })
}

/// A converter folder holding `manifest` and a `convert.py`; returns the manifest path.
fn install(folder: &Path, manifest: &Value) -> std::path::PathBuf {
    fs::create_dir_all(folder).expect("folder");
    fs::write(folder.join("convert.py"), "print('hi')\n").expect("script");
    let path = folder.join(MANIFEST_FILE);
    fs::write(&path, manifest.to_string()).expect("manifest");
    path
}

/// The manifest field a refusal names, for a manifest with `edit` applied to the base one.
fn refused(edit: impl FnOnce(&mut Value)) -> (Option<&'static str>, String) {
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
        ("manifest", Box::new(|m| m["manifest"] = json!(2))),
        ("protocol", Box::new(|m| m["protocol"] = json!(3))),
        (
            "scene_version",
            Box::new(|m| m["scene_version"] = json!("5.0")),
        ),
        (
            "scene_version",
            Box::new(|m| m["scene_version"] = json!("4.1")),
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
            "extensions",
            Box::new(|m| m["extensions"] = json!([".ACME"])),
        ),
        ("code", Box::new(|m| m["code"] = json!(["../elsewhere"]))),
        ("code", Box::new(|m| m["code"] = json!(["no-such-file.py"]))),
        (
            "idle_timeout_s",
            Box::new(|m| m["idle_timeout_s"] = json!(0)),
        ),
        (
            "options",
            Box::new(|m| m["options"] = json!([{ "key": "a", "label": "A", "kind": "slider" }])),
        ),
    ];
    for (field, edit) in cases {
        let (named, reason) = refused(edit);
        assert_eq!(named, Some(field), "{reason}");
    }
    let (_, reason) = refused(|m| m["manifest"] = json!(2));
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
    assert_eq!(error.field, Some("code"));
}

#[test]
fn the_code_hash_follows_the_hashed_files_and_not_dot_files() {
    let folder = tempfile::tempdir().expect("folder");
    let mut manifest = base();
    manifest["code"] = json!(["."]);
    let path = install(folder.path(), &manifest);
    let decoder = ConverterDecoder::load(&path).expect("loads");
    let first = decoder.code_hash().expect("hash");

    fs::write(folder.path().join(".hidden"), "x").expect("dot file");
    fs::create_dir(folder.path().join("__pycache__")).expect("cache folder");
    fs::write(folder.path().join("__pycache__/convert.pyc"), "x").expect("cache file");
    assert_eq!(
        decoder.code_hash().expect("hash"),
        first,
        "skipped files are not identity"
    );

    fs::write(
        folder.path().join("convert.py"),
        "print('edited, and longer')\n",
    )
    .expect("edit");
    let edited = decoder.code_hash().expect("hash");
    assert_ne!(edited, first);

    fs::write(folder.path().join("helper.py"), "x = 1\n").expect("new file");
    assert_ne!(
        decoder.code_hash().expect("hash"),
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
    let decoder = ConverterDecoder::load(&path).expect("loads");
    let loaded = decoder.code_hash().expect("hash");

    manifest["name"] = json!("Edited while running");
    install(folder.path(), &manifest);
    assert_eq!(
        decoder.code_hash().expect("hash"),
        loaded,
        "the running decoder keeps the identity of the manifest it runs"
    );
    let restarted = ConverterDecoder::load(&path).expect("loads again");
    assert_ne!(restarted.code_hash().expect("hash"), loaded);
}

#[test]
fn the_store_gets_the_code_hash_and_a_converter_whose_files_cannot_be_hashed_refuses_to_run() {
    let folder = tempfile::tempdir().expect("folder");
    let mut manifest = base();
    manifest["code"] = json!(["convert.py"]);
    let path = install(folder.path(), &manifest);
    let decoder = ConverterDecoder::load(&path).expect("loads");
    assert_eq!(Decoder::code_hash(&decoder), decoder.code_hash().ok());
    assert!(Decoder::code_hash(&decoder).is_some());

    fs::remove_file(folder.path().join("convert.py")).expect("remove");

    assert_eq!(Decoder::code_hash(&decoder), None);
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
    let decoder = ConverterDecoder::load(&install(folder.path(), &base())).expect("loads");
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
fn the_registry_installs_lists_removes_and_keeps_unknown_keys() {
    let root = tempfile::tempdir().expect("root");
    let registry_path = root.path().join("data/ax-devil/plugins.json");
    let first = root.path().join("first");
    install(&first, &base());

    let mut registry = Registry::read(&registry_path).expect("a missing registry is empty");
    assert!(registry.manifests().is_empty());
    let manifest = registry.install(&first, &["mot"]).expect("installs");
    assert_eq!(manifest.id, "acme");
    let again = registry.install(&first, &[]).expect_err("the id is taken");
    assert_eq!(again.field, Some("id"));
    let reserved = registry
        .install(&first, &["acme"])
        .expect_err("the id is reserved");
    assert_eq!(reserved.field, Some("id"));
    fs::create_dir_all(registry_path.parent().expect("parent")).expect("folder");
    registry.save().expect("saves");

    let mut text: Value =
        serde_json::from_str(&fs::read_to_string(&registry_path).expect("file")).expect("json");
    text["future"] = json!({"kept": true});
    fs::write(&registry_path, text.to_string()).expect("edit");
    let mut registry = Registry::read(&registry_path).expect("reads");
    assert_eq!(registry.manifests().len(), 1);
    assert!(registry.remove("acme"));
    assert!(!registry.remove("acme"));
    registry.save().expect("saves");
    let saved: Value =
        serde_json::from_str(&fs::read_to_string(&registry_path).expect("file")).expect("json");
    assert_eq!(saved["future"], json!({"kept": true}));
    assert_eq!(saved["converters"], json!([]));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = fs::metadata(&registry_path)
            .expect("stat")
            .permissions()
            .mode();
        assert_eq!(mode & 0o077, 0, "the registry is private");
    }
}

#[test]
fn a_broken_registry_or_converter_loads_the_rest_and_is_listed() {
    let root = tempfile::tempdir().expect("root");
    let registry_path = root.path().join("plugins.json");
    let good = install(&root.path().join("good"), &base());
    let mut broken_manifest = base();
    broken_manifest["id"] = json!("broken");
    broken_manifest["protocol"] = json!(9);
    let broken = install(&root.path().join("broken"), &broken_manifest);
    let missing = root.path().join("gone/ax-devil-converter.json");
    let entry = |manifest: &Path| json!({ "manifest": manifest });
    fs::write(
        &registry_path,
        json!({ "version": 1, "converters": [entry(&broken), entry(&missing), entry(&good), entry(&good)] }).to_string(),
    )
    .expect("registry");

    let loaded = load(&registry_path, &[]);
    assert!(loaded.registry.is_none());
    let outcome: Vec<Option<&str>> = loaded
        .plugins
        .iter()
        .map(|plugin| {
            plugin
                .decoder
                .as_ref()
                .err()
                .map(|error| error.field.unwrap_or("file"))
        })
        .collect();
    assert_eq!(outcome, [Some("protocol"), Some("file"), None, Some("id")]);
    assert_eq!(loaded.decoders().len(), 1);

    fs::write(&registry_path, "{ not json").expect("registry");
    let loaded = load(&registry_path, &[]);
    assert!(loaded.registry.is_some() && loaded.plugins.is_empty() && loaded.decoders().is_empty());

    fs::write(&registry_path, r#"{"version":2,"converters":[]}"#).expect("registry");
    let reason = load(&registry_path, &[]).registry.expect("refused").0;
    assert!(reason.contains("newer ax-devil"), "{reason}");
}
