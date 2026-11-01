//! The declared settings of every resolver, through the public registry.

use foundation::{Location, UserError};
use playlist_resolvers::{Cli, FieldKind, ResolverSchema, Settings, resolver, resolvers};
use serde_json::json;

fn settings(value: serde_json::Value) -> Settings {
    value.as_object().unwrap().clone()
}

fn schema(id: &str) -> &'static ResolverSchema {
    resolver(id).unwrap().schema()
}

fn rejected(id: &str, value: serde_json::Value) -> UserError {
    schema(id).validate(&settings(value)).unwrap_err()
}

#[test]
fn every_schema_can_be_turned_into_a_form_and_a_command_line() {
    for resolver in resolvers() {
        let fields = resolver.schema().fields;
        let mut keys: Vec<_> = fields.iter().map(|field| field.key).collect();
        keys.sort_unstable();
        keys.dedup();
        assert_eq!(
            keys.len(),
            fields.len(),
            "{}: unique keys",
            resolver.spec().id
        );
        for field in fields {
            if let FieldKind::Scan { depends_on } = &field.kind {
                assert!(!depends_on.is_empty());
                assert!(depends_on.iter().all(|key| {
                    fields
                        .iter()
                        .any(|other| other.key == *key && other.key != field.key)
                }));
            }
            if matches!(field.cli, Cli::Positional) {
                assert!(field.required, "{}: a positional is required", field.key);
            }
        }
    }
}

#[test]
fn mot_challenge_takes_a_root_and_optionally_a_subset_of_its_sequences() {
    let accepted = [
        json!({ "root": "/data/mot" }),
        json!({ "root": "/data/mot", "sequences": [] }),
        json!({ "root": "/data/mot", "sequences": ["a", "b"] }),
        json!({ "root": "/data/mot", "sequences": null, "extra": 1 }),
    ];
    for value in accepted {
        assert_eq!(schema("mot_challenge").validate(&settings(value)), Ok(()));
    }
    let table = [
        (json!({}), "Required", "root"),
        (json!({ "root": null }), "Required", "root"),
        (json!({ "root": "" }), "Must not be empty", "root"),
        (json!({ "root": 3 }), "Must be text", "root"),
        (
            json!({ "root": "/d", "sequences": "a" }),
            "Must be a list of names",
            "sequences",
        ),
        (
            json!({ "root": "/d", "sequences": [1] }),
            "Must be a list of names",
            "sequences",
        ),
    ];
    for (value, reason, location) in table {
        let error = rejected("mot_challenge", value.clone());
        assert_eq!(
            (&*error.reason, error.location),
            (reason, Some(Location::field(location))),
            "{value}"
        );
    }
}

#[test]
fn folder_pair_requires_both_folders_and_the_overlay_format() {
    let full = json!({ "videos_dir": "/v", "overlays_dir": "/o", "handler_type": "MOT_FILE" });
    assert_eq!(
        schema("folder_pair").validate(&settings(full.clone())),
        Ok(())
    );
    for key in ["videos_dir", "overlays_dir", "handler_type"] {
        let mut value = full.clone();
        value.as_object_mut().unwrap().remove(key);
        assert_eq!(
            rejected("folder_pair", value).location,
            Some(Location::field(key))
        );
    }
}
