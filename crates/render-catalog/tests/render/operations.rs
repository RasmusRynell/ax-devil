//! Operations whose results the reference pins: colors by id, number text, trimmed ids and bars.

use render_catalog::FeatureSet;
use serde_json::{Value, json};

use super::support::*;

fn stroke(color: Value) -> Value {
    json!({"stroke": {"color": color, "width": px(1.0), "pattern": "solid"}})
}

#[test]
fn pick_color_is_the_crc32_of_the_text_modulo_the_palette_like_the_reference() {
    let palette = json!([
        [255, 0, 0],
        [0, 255, 0],
        [0, 0, 255],
        [255, 255, 0],
        [0, 255, 255],
        [255, 0, 255],
        [128, 128, 128]
    ]);
    let color = call(
        "pick_color",
        json!({"text": reference(&["scene", "entity", "id"]), "colors": palette}),
    );
    let mut step = point(geometry("x"), json!(0));
    step["style"] = stroke(color);
    let document = catalog(vec![step], vec![], vec![], vec![]);
    // (id, color) from Python's `colors[zlib.crc32(id.encode()) % 7]`.
    let expected = [
        ("1", [0, 0, 255]),
        ("12", [0, 255, 0]),
        ("person", [0, 255, 255]),
        ("untracked:3:7", [255, 255, 0]),
        ("é", [0, 255, 255]),
        ("track-0042", [255, 255, 0]),
    ];
    let entities = expected
        .iter()
        .enumerate()
        .map(|(index, (id, _))| {
            entity(
                id,
                [index as f64 / 10.0, 0.0, index as f64 / 10.0 + 0.05, 0.1],
                None,
            )
        })
        .collect();
    let (drawn, problems) = render(
        &compiled(&document),
        &scene(entities, vec![]),
        target(),
        FeatureSet::default(),
    );
    assert!(problems.is_empty(), "{problems:?}");
    let drawn = drawn["points"].as_array().expect("points");
    assert_eq!(drawn.len(), expected.len());
    for (index, (id, color)) in expected.iter().enumerate() {
        assert_eq!(drawn[index]["pen"], json!(color), "{id}");
    }
}

fn label_style() -> Value {
    json!({"label": {"size": px(12.0), "family": null, "background": null, "padding_x": px(0.0),
                     "padding_y": px(0.0), "radius": px(0.0), "gap": px(0.0)}})
}

/// The text of the label that `run` draws for each entity, in the order of the entities' ids.
fn label_runs(run: Value, entities: Vec<Value>) -> Vec<Value> {
    let label = json!({"primitive": "label", "style": label_style(), "fields": {
        "position": {"x": 0.1, "y": geometry("y")}, "anchor": "top-left", "runs": [run]}});
    let document = catalog(vec![], vec![label.clone()], vec![], vec![]);
    let mut document = document;
    document["recipes"]["fallbacks"][1]["steps"] = json!([label]);
    let (drawn, problems) = render(
        &compiled(&document),
        &scene(entities, vec![]),
        target(),
        FeatureSet::default(),
    );
    assert!(problems.is_empty(), "{problems:?}");
    drawn["labels"]
        .as_array()
        .expect("labels")
        .iter()
        .map(|label| label["runs"][0].clone())
        .collect()
}

fn text_run(text: Value) -> Value {
    json!({"text": text, "color": [255, 255, 255], "weight": "regular"})
}

/// Entities of the generic recipe whose boxes start at `top = index / 10`, so labels sort in entity order.
fn generic(entities: &[(&str, f64)]) -> Vec<Value> {
    entities
        .iter()
        .enumerate()
        .map(|(index, (id, score))| {
            let id = if id.is_empty() {
                format!("id{index}")
            } else {
                (*id).to_string()
            };
            let mut found = entity(&id, [0.0, index as f64 / 10.0, 0.5, 0.95], Some("zebra"));
            found["observations"][0]["classifications"][0]["score"] = json!(score);
            found
        })
        .collect()
}

#[test]
fn scores_are_formatted_like_python_with_half_to_even_on_the_binary_value() {
    let text = call(
        "number_text",
        json!({"value": reference(&["scene", "classification", "score"]), "precision": 2, "hide_zero": false}),
    );
    let scores = [
        (0.125, "0.12"),
        (0.375, "0.38"),
        (0.91, "0.91"),
        (5.3, "5.30"),
        (123.456, "123.46"),
        (0.0, "0.00"),
        (-0.001, "-0.00"),
        (0.145, "0.14"),
    ];
    let entities = generic(&scores.map(|(score, _)| ("", score)));
    let found: Vec<Value> = label_runs(text_run(text), entities)
        .iter()
        .map(|run| run["text"].clone())
        .collect();
    assert_eq!(found, scores.map(|(_, text)| json!(text)));
}

#[test]
fn hide_zero_leaves_the_run_out() {
    let text = call(
        "number_text",
        json!({"value": reference(&["scene", "classification", "score"]), "precision": 1, "hide_zero": true}),
    );
    let found = label_runs(text_run(text), generic(&[("a", 0.0), ("b", 0.25)]));
    assert_eq!(found.len(), 1, "a label without a visible run is not drawn");
    assert_eq!(found[0]["text"], "0.2");
}

#[test]
fn long_ids_are_trimmed_like_the_reference() {
    let trim = |max: i64, elide: Option<&str>, delimiter: Option<&str>| {
        let mut args = json!({"text": reference(&["scene", "entity", "id"]), "max_length": max, "suffix": "…", "delimiter": null});
        if let Some(elide) = elide {
            args["elide"] = json!(elide);
        }
        if let Some(delimiter) = delimiter {
            args["delimiter"] = json!(delimiter);
        }
        call("trim_text", args)
    };
    let ids = |ids: &[&str]| generic(&ids.iter().map(|id| (*id, 0.5)).collect::<Vec<_>>());
    let texts = |found: Vec<Value>| {
        found
            .iter()
            .map(|run| run["text"].as_str().expect("text").to_string())
            .collect::<Vec<_>>()
    };

    let middle = label_runs(
        text_run(trim(10, Some("middle"), None)),
        ids(&["untracked:0:12", "short", "exactly10!"]),
    );
    assert_eq!(texts(middle), ["untra…:0:12", "short", "exactly10!"]);
    let middle = label_runs(
        text_run(trim(8, Some("middle"), None)),
        ids(&["héllo wörld ok"]),
    );
    assert_eq!(
        texts(middle),
        ["héll…d ok"],
        "counted in characters, not bytes"
    );
    let end = label_runs(
        text_run(trim(10, None, None)),
        ids(&["abcdefghijkl", "abc"]),
    );
    assert_eq!(texts(end), ["abcdefghij…", "abc"]);
    let end = label_runs(
        text_run(trim(10, None, Some(":"))),
        ids(&["ab:cdefghijkl", "a:b", "plain"]),
    );
    assert_eq!(
        texts(end),
        ["ab…", "a…", "plain"],
        "the delimiter cuts short text too"
    );
}

#[test]
fn bars_are_clamped_and_rounded_to_hundredths() {
    let bar = json!({"bar": reference(&["scene", "observation", "geometry", "x"]), "color": [255, 176, 32], "weight": "regular"});
    let entities = [0.125, 0.375, 1.5, -1.0, 0.5]
        .iter()
        .enumerate()
        .map(|(index, left)| {
            let top = index as f64 / 10.0;
            entity(
                &format!("id{index}"),
                [*left, top, left + 0.5, 0.95],
                Some("zebra"),
            )
        })
        .collect();
    let found = label_runs(bar, entities);
    let bars: Vec<f64> = found
        .iter()
        .map(|run| run["bar"].as_f64().expect("bar"))
        .collect();
    assert_eq!(bars, [0.12, 0.38, 1.0, 0.0, 0.5]);
    assert!(
        found.iter().all(|run| run["text"] == ""),
        "a bar run has no text"
    );
}
