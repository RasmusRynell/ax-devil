//! Settings checked against a form: defaults, required keys, kinds, and the text two equal settings share.

use foundation::Location;
use handler_api::{Field, FieldKind, Form, Selection};
use serde_json::{Value, json};

fn form() -> Form {
    let text = Field::new("camera", "Camera", FieldKind::Text);
    Form {
        fields: vec![
            Field::new("root", "Folder", FieldKind::Folder)
                .required()
                .positional(),
            Field {
                default: Some(json!("front")),
                ..text
            },
            Field::new("width", "Width", FieldKind::Integer { min: 1 }),
            Field::new(
                "pick",
                "Pick",
                FieldKind::Scan {
                    depends_on: vec!["root".into()],
                },
            ),
        ],
    }
}

fn raw(value: Value) -> serde_json::Map<String, Value> {
    value.as_object().unwrap().clone()
}

#[test]
fn settings_are_checked_against_the_declared_kinds_and_defaults_fill_the_gaps() {
    let rejected = [
        (json!({}), "Required", "root"),
        (json!({ "root": null }), "Required", "root"),
        (json!({ "root": "" }), "Must not be empty", "root"),
        (json!({ "root": 3 }), "Must be text", "root"),
        (
            json!({ "root": "/d", "width": 0 }),
            "Must be at least 1",
            "width",
        ),
        (
            json!({ "root": "/d", "width": "wide" }),
            "Must be a whole number",
            "width",
        ),
        (
            json!({ "root": "/d", "pick": "a" }),
            "Must be a list of names",
            "pick",
        ),
        (
            json!({ "root": "/d", "pick": [1] }),
            "Must be a list of names",
            "pick",
        ),
    ];
    for (value, reason, key) in rejected {
        let error = form().validate(&raw(value.clone())).unwrap_err();
        assert_eq!(
            (&*error.reason, error.location),
            (reason, Some(Location::field(key))),
            "{value}"
        );
    }

    let omitted = form()
        .validate(&raw(json!({ "root": "/d", "pick": null })))
        .unwrap();
    let explicit = form()
        .validate(&raw(
            json!({ "root": "/d", "camera": "front", "width": null, "extra": [1] }),
        ))
        .unwrap();
    assert_eq!(omitted.text("camera"), Some("front"));
    assert_eq!(
        (omitted.integer("width"), omitted.selection("pick")),
        (None, Selection::All)
    );
    assert_eq!(
        omitted.canonical(),
        explicit.canonical(),
        "undeclared keys and explicit defaults change nothing"
    );
    assert_eq!(omitted.canonical(), r#"{"camera":"front","root":"/d"}"#);
    assert_eq!(explicit.unknown_keys(), ["extra"]);

    let picked = form()
        .validate(&raw(json!({ "root": "/d", "pick": ["a"], "width": 5 })))
        .unwrap();
    assert_eq!(picked.selection("pick"), Selection::Only(vec!["a".into()]));
    assert_eq!(picked.integer("width"), Some(5));
    assert_ne!(picked.canonical(), omitted.canonical());
}
