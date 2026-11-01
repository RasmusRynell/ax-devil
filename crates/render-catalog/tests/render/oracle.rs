//! Every built-in catalog against the reference app's Drawing Instructions (`fixtures/oracle`).

use render_catalog::{FeatureSet, OverlayFeature, compile};
use serde_json::Value;

use super::support::{render, scenes, target};

const ORACLE: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/oracle");
const CATALOGS: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../assets/catalogs");
const TOLERANCE: f64 = 2e-5;

fn approx(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => {
            (x.as_f64().unwrap_or(f64::NAN) - y.as_f64().unwrap_or(f64::NAN)).abs() <= TOLERANCE
        }
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(x, y)| approx(x, y))
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len()
                && x.iter()
                    .all(|(key, x)| y.get(key).is_some_and(|y| approx(x, y)))
        }
        _ => a == b,
    }
}

/// What differs between two lists of rows, as messages: rows only on one side. Rows are matched one to one in any
/// order, so a rounding tie that sorts two rows differently does not matter.
fn row_differences(kind: &str, expected: &[Value], actual: &[Value]) -> Vec<String> {
    let mut unused: Vec<&Value> = actual.iter().collect();
    let mut differences = Vec::new();
    for row in expected {
        match unused.iter().position(|candidate| approx(row, candidate)) {
            Some(index) => {
                unused.remove(index);
            }
            None => differences.push(format!("{kind}: missing {row}")),
        }
    }
    differences.extend(unused.iter().map(|row| format!("{kind}: unexpected {row}")));
    differences
}

/// Renders the oracle sheet with the oracle catalog file's features hidden and lists every difference.
fn compare(catalog: &str, file: &std::path::Path) -> Vec<String> {
    let expected: Value =
        serde_json::from_str(&std::fs::read_to_string(file).expect("oracle file"))
            .expect("oracle JSON");
    let sheet = expected["sheet"].as_str().expect("sheet name");
    let hidden = expected["hidden"]
        .as_array()
        .expect("hidden features")
        .iter()
        .filter_map(|name| OverlayFeature::parse(name.as_str()?))
        .fold(FeatureSet::default(), FeatureSet::with);
    let source = std::fs::read_to_string(format!("{CATALOGS}/{catalog}.json")).expect("catalog");
    let compiled = compile(&source).expect("built-in catalog compiles");
    let sheet_text =
        std::fs::read_to_string(format!("{ORACLE}/sheets/{sheet}.json")).expect("sheet");
    let scene = scenes(&sheet_text).remove(0);
    let (actual, problems) = render(&compiled, &scene, target(), hidden);
    let mut differences: Vec<String> = problems
        .iter()
        .map(|p| format!("problem {} at {}: {}", p.code, p.location, p.message))
        .collect();
    let expected = &expected["instructions"];
    for kind in [
        "boxes",
        "circles",
        "lines",
        "points",
        "polygons",
        "polylines",
        "texts",
        "labels",
    ] {
        let rows = |value: &Value| value[kind].as_array().cloned().unwrap_or_default();
        differences.extend(row_differences(kind, &rows(expected), &rows(&actual)));
    }
    differences
}

/// Every oracle file of `catalog` (every sheet and the feature-hidden variants) matches.
fn matches_oracle(catalog: &str) {
    let folder = format!("{ORACLE}/catalogs/{catalog}");
    let mut failures = Vec::new();
    let mut files = 0;
    for entry in std::fs::read_dir(&folder).expect("oracle folder") {
        let path = entry.expect("entry").path();
        files += 1;
        let differences = compare(catalog, &path);
        if !differences.is_empty() {
            let name = path.file_name().map(|n| n.to_string_lossy().into_owned());
            failures.push(format!(
                "{}:\n  {}",
                name.unwrap_or_default(),
                differences.join("\n  ")
            ));
        }
    }
    assert!(files >= 3, "{catalog} has its oracle files");
    assert!(
        failures.is_empty(),
        "{catalog} differs from the reference:\n{}",
        failures.join("\n")
    );
}

#[test]
fn standard_matches_the_reference_on_every_sheet() {
    matches_oracle("standard");
}

#[test]
fn tracking_matches_the_reference_on_every_sheet() {
    matches_oracle("tracking");
}

#[test]
fn the_other_built_in_catalogs_match_the_reference() {
    for catalog in ["minimal", "chunky", "glass", "classic"] {
        matches_oracle(catalog);
    }
}

#[test]
fn every_built_in_catalog_draws_the_decoded_mot_scenes_without_problems() {
    for catalog in [
        "standard", "minimal", "chunky", "glass", "tracking", "classic",
    ] {
        let source =
            std::fs::read_to_string(format!("{CATALOGS}/{catalog}.json")).expect("catalog");
        let compiled = compile(&source).expect("built-in catalog compiles");
        for file in ["det", "gt"] {
            let text = std::fs::read_to_string(format!("{ORACLE}/mot/{file}.scenes.json"))
                .expect("scenes");
            for scene in scenes(&text) {
                let (drawn, problems) = render(&compiled, &scene, target(), FeatureSet::default());
                assert!(problems.is_empty(), "{catalog} {file}: {problems:?}");
                let boxes = drawn["boxes"].as_array().map_or(0, Vec::len);
                assert!(boxes > 0, "{catalog} {file} draws its boxes");
            }
        }
    }
}
