//! What the rendering tests share: building catalogs and Scenes from JSON and reading the drawn instructions.

use drawing::DrawingInstructions;
use render_catalog::{CatalogDiagnostic, CompiledCatalog, FeatureSet, RenderContext, compile};
use scene::{Scene, SceneView, serial};
use serde_json::{Value, json};

pub fn target() -> RenderContext {
    RenderContext {
        width: 1280.0,
        height: 720.0,
    }
}

pub fn scenes(text: &str) -> Vec<Scene> {
    serial::from_json_str(text).expect("a valid Scene document")
}

/// Renders `scene` and returns the canonical instructions and the problems reported.
pub fn render(
    catalog: &CompiledCatalog,
    scene: &Scene,
    context: RenderContext,
    hidden: FeatureSet,
) -> (Value, Vec<CatalogDiagnostic>) {
    let mut out = DrawingInstructions::default();
    let mut problems = Vec::new();
    let view = SceneView::all(scene);
    catalog.render(&view, &context, hidden, &mut out, &mut |p| problems.push(p));
    (out.canonical(), problems)
}

pub fn call(name: &str, args: Value) -> Value {
    json!({"call": name, "args": args})
}

pub fn reference(path: &[&str]) -> Value {
    json!({"ref": path})
}

pub fn px(value: f64) -> Value {
    json!({"value": value, "unit": "px"})
}

pub fn geometry(field: &str) -> Value {
    reference(&["scene", "observation", "geometry", field])
}

/// A point at `x`, `y`.
pub fn point(x: Value, y: Value) -> Value {
    json!({"primitive": "point", "fields": {"position": {"x": x, "y": y}}})
}

/// A catalog whose recipes run these steps: the two fallbacks, `car` for the type `car`, `has_part` for relations.
pub fn catalog(
    unclassified: Vec<Value>,
    classified: Vec<Value>,
    car: Vec<Value>,
    has_part: Vec<Value>,
) -> Value {
    let recipe = |id: &str, selector: Value, steps: Vec<Value>| json!({"id": id, "selector": selector, "steps": steps});
    json!({
        "metadata": {"id": "test", "name": "Test", "schema_version": 3},
        "templates": {},
        "recipes": {
            "fallbacks": [
                recipe("motion", json!({"kind": "unclassified"}), unclassified),
                recipe("generic", json!({"kind": "classified"}), classified),
            ],
            "classifications": [recipe("car", json!({"kind": "classification", "types": ["car"]}), car)],
            "relations": [recipe("has_part", json!({"kind": "relation", "types": ["has_part"]}), has_part)],
        },
    })
}

pub fn compiled(document: &Value) -> CompiledCatalog {
    compile(&document.to_string()).unwrap_or_else(|problems| panic!("{problems:#?}"))
}

/// An entity with one observation at `[left, top, right, bottom]` of this class, none for no class.
pub fn entity(id: &str, bounds: [f64; 4], class: Option<&str>) -> Value {
    let classes: Vec<Value> = class
        .map(|class| json!({"type": class, "score": 0.5, "confidence": null, "attributes": {}}))
        .into_iter()
        .collect();
    json!({"id": id, "observations": [{"time": {"frame": 0}, "geometry": {"box": bounds}, "classifications": classes}],
           "end_reason": null, "motion_state": null})
}

pub fn without_observations(id: &str) -> Value {
    json!({"id": id, "observations": [], "end_reason": null, "motion_state": null})
}

pub fn relation(kind: &str, source: &str, target: &str) -> Value {
    json!({"type": kind, "source": source, "target": target})
}

pub fn scene(entities: Vec<Value>, relations: Vec<Value>) -> Scene {
    let document = json!({"scene_version": "4.0", "scenes": [{
        "time": {"start": {"frame": 0}, "end": {"frame": 0}},
        "entities": entities, "events": [], "relations": relations, "debug": {}}]});
    scenes(&document.to_string()).remove(0)
}

/// The `x`, `y` pairs of the points drawn, sorted.
pub fn points(drawn: &Value) -> Vec<(f64, f64)> {
    let mut found: Vec<(f64, f64)> = drawn["points"]
        .as_array()
        .expect("points")
        .iter()
        .map(|p| (p["x"].as_f64().expect("x"), p["y"].as_f64().expect("y")))
        .collect();
    found.sort_by(|a, b| a.partial_cmp(b).expect("finite"));
    found
}
