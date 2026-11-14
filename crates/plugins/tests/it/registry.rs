//! The registry: what is installed, in which order, and which ids each plugin takes.

use crate::common;

use std::{fs, path::Path};

use common::{base, install};
use plugins::{Registry, Reserved, load};
use serde_json::{Value, json};

/// The ids a build with these built-ins would have taken.
fn reserved(plugin: &str, handler: &str) -> Reserved {
    Reserved {
        plugins: [plugin.to_owned()].into(),
        handlers: [handler.to_owned()].into(),
    }
}

#[test]
fn the_registry_installs_lists_removes_and_keeps_unknown_keys() {
    let root = tempfile::tempdir().expect("root");
    let registry_path = root.path().join("data/ax-devil/plugins.json");
    let first = root.path().join("first");
    install(&first, &base());

    let mut registry = Registry::read(&registry_path).expect("a missing registry is empty");
    assert!(registry.manifests().is_empty());
    let manifest = registry
        .install(&first, &reserved("mot", "MOT_FILE"))
        .expect("installs");
    assert_eq!(manifest.id, "acme");
    let field = |taken: Reserved| {
        let error = registry.clone().install(&first, &taken).expect_err("taken");
        error.field
    };
    assert_eq!(field(Reserved::default()).as_deref(), Some("id"));
    assert_eq!(field(reserved("acme", "")).as_deref(), Some("id"));
    // Plugin ids and handler ids are separate: only a taken handler id refuses the handler.
    let mut elsewhere = Registry::read(&registry_path).expect("empty");
    let error = elsewhere
        .install(&first, &reserved("mot", "acme"))
        .expect_err("the handler id is reserved");
    assert_eq!(error.field.as_deref(), Some("handlers[0].id"));
    fs::create_dir_all(registry_path.parent().expect("parent")).expect("folder");
    registry.save().expect("saves");

    let mut text: Value =
        serde_json::from_str(&fs::read_to_string(&registry_path).expect("file")).expect("json");
    text["future"] = json!({"kept": true});
    // A second folder that declares the same id, as one can once the first failed to load when it was installed.
    let second = fs::canonicalize(install(&root.path().join("second"), &base())).expect("second");
    text["plugins"]
        .as_array_mut()
        .expect("plugins")
        .push(json!({ "manifest": second }));
    fs::write(&registry_path, text.to_string()).expect("edit");
    let mut registry = Registry::read(&registry_path).expect("reads");
    let first = registry.manifests().remove(0);
    assert_eq!(registry.manifests().len(), 2);
    // An update claims ids as loading does, from the earlier plugins only: the one that loads can be updated, and
    // the later duplicate cannot take the id.
    let (first_path, second_path) = (registry.manifests()[0].clone(), second.clone());
    registry
        .clone()
        .update(0, &first_path, &Reserved::default())
        .expect("the plugin that loads updates");
    let error = registry
        .clone()
        .update(1, &second_path, &Reserved::default())
        .expect_err("the duplicate stays refused");
    assert_eq!(error.field.as_deref(), Some("id"));
    // It is also refused when its manifest takes the id of a later plugin that loads, which would stop loading.
    let mut beta_manifest = base();
    beta_manifest["id"] = json!("beta");
    beta_manifest["handlers"][0]["id"] = json!("beta_tracks");
    let beta = install(&root.path().join("beta"), &beta_manifest);
    let mut taking = base();
    taking["handlers"][0]["id"] = json!("beta_tracks");
    let taking = install(&root.path().join("taking"), &taking);
    let mut with_beta = registry.clone();
    with_beta
        .install(&beta, &Reserved::default())
        .expect("beta installs");
    let before = with_beta.manifests();
    let error = with_beta
        .update(0, &taking, &Reserved::default())
        .expect_err("beta would stop loading");
    assert!(
        error.reason.contains("beta") && error.reason.contains("beta_tracks"),
        "{error}"
    );
    assert_eq!(with_beta.manifests(), before);
    // Releasing an id can make a plugin that fails today load, and take an id from one that loads: A owns `x`, B
    // wants `x` and `y` and fails, C owns `y`. Updating A to drop `x` would load B and stop C.
    let owning = |id: &str, handlers: &[&str]| {
        let mut manifest = base();
        manifest["id"] = json!(id);
        let handler = manifest["handlers"][0].clone();
        manifest["handlers"] = handlers
            .iter()
            .map(|handler_id| {
                let mut declared = handler.clone();
                declared["id"] = json!(handler_id);
                declared
            })
            .collect();
        let path = install(
            &root.path().join(format!("abc-{id}-{}", handlers.join("-"))),
            &manifest,
        );
        fs::canonicalize(path).expect("manifest")
    };
    let abc = [
        owning("a", &["x"]),
        owning("b", &["x", "y"]),
        owning("c", &["y"]),
    ];
    let abc_path = root.path().join("abc.json");
    fs::write(
        &abc_path,
        json!({ "version": 2, "plugins": abc.map(|manifest| json!({ "manifest": manifest })) })
            .to_string(),
    )
    .expect("registry");
    let mut abc = Registry::read(&abc_path).expect("reads");
    let before = abc.manifests();
    let error = abc
        .update(0, &owning("a", &["z"]), &Reserved::default())
        .expect_err("c would stop loading");
    assert!(
        error.reason.contains("'c'") && error.reason.contains("'y'"),
        "{error}"
    );
    assert_eq!(abc.manifests(), before);
    let error = registry.remove("acme").expect_err("the id names two");
    assert!(error.0.contains("declared by 2 plugins"), "{error}");
    assert_eq!(registry.manifests().len(), 2);
    assert_eq!(registry.remove(first.to_str().expect("utf-8")), Ok(true));
    assert_eq!(registry.manifests(), [second]);
    assert_eq!(registry.remove("acme"), Ok(true));
    assert_eq!(registry.remove("acme"), Ok(false));
    registry.save().expect("saves");
    let saved: Value =
        serde_json::from_str(&fs::read_to_string(&registry_path).expect("file")).expect("json");
    assert_eq!(saved["future"], json!({"kept": true}));
    assert_eq!(saved["plugins"], json!([]));
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
fn a_broken_registry_or_plugin_loads_the_rest_and_is_listed() {
    let root = tempfile::tempdir().expect("root");
    let registry_path = root.path().join("plugins.json");
    let good = install(&root.path().join("good"), &base());
    let mut broken_manifest = base();
    broken_manifest["id"] = json!("broken");
    broken_manifest["protocol"] = json!(9);
    let broken = install(&root.path().join("broken"), &broken_manifest);
    let missing = root.path().join("gone/ax-devil-plugin.json");
    let entry = |manifest: &Path| json!({ "manifest": manifest });
    fs::write(
        &registry_path,
        json!({ "version": 2, "plugins": [entry(&broken), entry(&missing), entry(&good), entry(&good)] }).to_string(),
    )
    .expect("registry");

    let loaded = load(&registry_path, &Reserved::default());
    assert!(loaded.registry.is_none());
    let outcome: Vec<Option<&str>> = loaded
        .entries
        .iter()
        .map(|entry| {
            entry
                .plugin
                .as_ref()
                .err()
                .map(|error| error.field.as_deref().unwrap_or("file"))
        })
        .collect();
    assert_eq!(outcome, [Some("protocol"), Some("file"), None, Some("id")]);
    assert_eq!(loaded.decoders().len(), 1);

    fs::write(&registry_path, "{ not json").expect("registry");
    let loaded = load(&registry_path, &Reserved::default());
    assert!(loaded.registry.is_some() && loaded.entries.is_empty() && loaded.decoders().is_empty());

    fs::write(&registry_path, r#"{"version":3,"plugins":[]}"#).expect("registry");
    let reason = load(&registry_path, &Reserved::default())
        .registry
        .expect("refused")
        .0;
    assert!(reason.contains("newer ax-devil"), "{reason}");
}
