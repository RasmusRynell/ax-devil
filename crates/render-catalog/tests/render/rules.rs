//! Routing, row isolation and feature gating.

use drawing::DrawingInstructions;
use render_catalog::{FeatureSet, OverlayFeature, RenderContext};
use scene::SceneView;
use serde_json::{Value, json};

use super::support::*;

#[test]
fn entities_go_to_unclassified_their_own_recipe_or_classified() {
    let y = || reference(&["scene", "observation", "geometry", "y"]);
    let document = catalog(
        vec![point(json!(0.1), y())],
        vec![point(json!(0.2), y())],
        vec![point(json!(0.3), y())],
        vec![],
    );
    let scene = scene(
        vec![
            entity("plain", [0.0, 0.1, 0.5, 0.5], None),
            entity("car", [0.0, 0.2, 0.5, 0.5], Some("car")),
            entity("zebra", [0.0, 0.3, 0.5, 0.5], Some("zebra")),
            without_observations("ghost"),
        ],
        vec![],
    );
    let (drawn, problems) = render(
        &compiled(&document),
        &scene,
        target(),
        FeatureSet::default(),
    );
    assert!(problems.is_empty(), "{problems:?}");
    assert_eq!(points(&drawn), [(0.1, 0.1), (0.2, 0.3), (0.3, 0.2)]);
}

#[test]
fn an_entity_is_routed_by_its_latest_observation_and_its_best_classification() {
    let document = catalog(
        vec![point(json!(0.1), json!(0.5))],
        vec![point(json!(0.2), json!(0.5))],
        vec![point(json!(0.3), json!(0.5))],
        vec![],
    );
    let observation = |frame: i64, classes: Value| json!({"time": {"frame": frame}, "geometry": {"box": [0.0, 0.0, 0.5, 0.5]}, "classifications": classes});
    let class = |name: &str, score: f64| json!({"type": name, "score": score, "confidence": null, "attributes": {}});
    let moved = json!({"id": "moved", "end_reason": null, "motion_state": null, "observations": [
        observation(0, json!([class("zebra", 0.9)])),
        observation(1, json!([class("zebra", 0.2), class("car", 0.8)]))]});
    let scene = scene(vec![moved], vec![]);
    let (drawn, _) = render(
        &compiled(&document),
        &scene,
        target(),
        FeatureSet::default(),
    );
    assert_eq!(
        points(&drawn),
        [(0.3, 0.5)],
        "the latest observation's highest-scored class decides"
    );
}

#[test]
fn relations_need_both_endpoints_in_the_view() {
    let line = json!({"primitive": "line", "fields": {
        "start": {"x": reference(&["scene", "source", "observation", "geometry", "x"]), "y": 0.0},
        "end": {"x": reference(&["scene", "target", "observation", "geometry", "x"]), "y": 0.0}}});
    let document = catalog(vec![], vec![], vec![], vec![line]);
    let source = |left| entity("p", [left, 0.0, left + 0.1, 0.1], Some("zebra"));
    let scene = scene(
        vec![
            source(0.1),
            entity("q", [0.2, 0.0, 0.3, 0.1], Some("zebra")),
            entity("r", [0.3, 0.0, 0.4, 0.1], Some("zebra")),
            without_observations("ghost"),
        ],
        vec![
            relation("has_part", "p", "q"),
            relation("has_part", "p", "r"),
            relation("has_part", "p", "ghost"),
            relation("has_part", "p", "missing"),
            relation("follows", "p", "q"),
        ],
    );
    let catalog = compiled(&document);
    let drawn_from = |view: &SceneView<'_>| {
        let mut out = DrawingInstructions::default();
        catalog.render(
            view,
            &target(),
            FeatureSet::default(),
            &mut out,
            &mut |_| {},
        );
        out.lines.len()
    };
    assert_eq!(
        drawn_from(&SceneView::all(&scene)),
        2,
        "q and r; not ghost, missing or the unrouted type"
    );
    let mut without_r = SceneView::all(&scene);
    without_r.entities.retain(|&index| index != 2);
    assert_eq!(
        drawn_from(&without_r),
        1,
        "a filtered-out endpoint hides the relation"
    );
}

fn steps_dividing_by_width() -> Vec<Value> {
    vec![
        point(json!(0.5), json!(0)),
        point(
            call("div", json!({"numerator": 1, "denominator": geometry("w")})),
            json!(0),
        ),
    ]
}

fn check_failure_is_located(problems: &[render_catalog::CatalogDiagnostic], step: &str) {
    assert_eq!(
        problems.len(),
        1,
        "one problem, however many rows hit it: {problems:?}"
    );
    assert_eq!(problems[0].code, "invalid_value");
    assert!(
        problems[0].location.contains(step),
        "{}",
        problems[0].location
    );
    assert!(
        problems[0].message.contains("zero"),
        "{}",
        problems[0].message
    );
}

#[test]
fn a_failing_row_draws_nothing_while_the_other_rows_do() {
    let document = catalog(steps_dividing_by_width(), vec![], vec![], vec![]);
    let scene = scene(
        vec![
            entity("good", [0.0, 0.0, 0.5, 0.2], None),
            entity("bad", [0.0, 0.0, 0.0, 0.2], None),
            entity("also good", [0.0, 0.0, 0.25, 0.2], None),
        ],
        vec![],
    );
    let (drawn, problems) = render(
        &compiled(&document),
        &scene,
        target(),
        FeatureSet::default(),
    );
    assert_eq!(
        points(&drawn),
        [(0.5, 0.0), (0.5, 0.0), (2.0, 0.0), (4.0, 0.0)],
        "the bad row's first point is taken back"
    );
    check_failure_is_located(&problems, "/recipes/fallbacks/0/steps/1");
}

#[test]
fn a_failing_relation_row_does_not_lose_the_others() {
    let document = catalog(
        vec![],
        vec![],
        vec![],
        vec![
            point(json!(0.5), json!(0)),
            point(
                call(
                    "div",
                    json!({"numerator": 1, "denominator": reference(&["scene", "source", "observation", "geometry", "w"])}),
                ),
                json!(0),
            ),
        ],
    );
    let scene = scene(
        vec![
            entity("bad", [0.0, 0.0, 0.0, 0.2], Some("zebra")),
            entity("good", [0.0, 0.0, 0.5, 0.2], Some("zebra")),
        ],
        vec![
            relation("has_part", "bad", "good"),
            relation("has_part", "good", "bad"),
        ],
    );
    let (drawn, problems) = render(
        &compiled(&document),
        &scene,
        target(),
        FeatureSet::default(),
    );
    assert_eq!(points(&drawn), [(0.5, 0.0), (2.0, 0.0)]);
    check_failure_is_located(&problems, "/recipes/relations/0/steps/1");
}

#[test]
fn a_failing_recipe_leaves_the_other_recipes_alone() {
    let document = catalog(
        steps_dividing_by_width(),
        vec![point(json!(0.7), json!(0.7))],
        vec![],
        vec![],
    );
    let scene = scene(
        vec![
            entity("bad", [0.0, 0.0, 0.0, 0.2], None),
            entity("zebra", [0.0, 0.0, 0.5, 0.2], Some("zebra")),
        ],
        vec![],
    );
    let (drawn, problems) = render(
        &compiled(&document),
        &scene,
        target(),
        FeatureSet::default(),
    );
    assert_eq!(points(&drawn), [(0.7, 0.7)]);
    assert_eq!(problems.len(), 1);
}

#[test]
fn a_guarded_value_missing_on_one_row_does_not_stop_the_row_after_it() {
    // Only entities with a score read it; the others skip the step instead of failing it.
    let score = reference(&["scene", "classification", "score"]);
    let step = json!({"primitive": "point", "enabled": call("is_present", json!({"value": score.clone()})),
        "fields": {"position": {"x": score, "y": 0.5}}});
    let document = catalog(vec![step.clone()], vec![step], vec![], vec![]);
    let scene = scene(
        vec![
            entity("scored", [0.0; 4], Some("zebra")),
            entity("plain", [0.0; 4], None),
        ],
        vec![],
    );
    let (drawn, problems) = render(
        &compiled(&document),
        &scene,
        target(),
        FeatureSet::default(),
    );
    assert_eq!(points(&drawn), [(0.5, 0.5)]);
    assert!(problems.is_empty(), "{problems:?}");
}

#[test]
fn a_computed_box_that_breaks_its_type_fails_its_row_with_the_reason() {
    let width = call("sub", json!({"left": 0, "right": geometry("x")}));
    let step =
        json!({"primitive": "box", "fields": {"geometry": {"x": 0, "y": 0, "w": width, "h": 0.1}}});
    let document = catalog(vec![step], vec![], vec![], vec![]);
    let scene = scene(
        vec![
            entity("negative", [0.2, 0.0, 0.4, 0.1], None),
            entity("positive", [-0.2, 0.0, 0.4, 0.1], None),
        ],
        vec![],
    );
    let (drawn, problems) = render(
        &compiled(&document),
        &scene,
        target(),
        FeatureSet::default(),
    );
    assert_eq!(drawn["boxes"].as_array().expect("boxes").len(), 1);
    assert_eq!(problems.len(), 1);
    assert_eq!(problems[0].code, "invalid_value");
    assert!(
        problems[0]
            .location
            .starts_with("/recipes/fallbacks/0/steps/0"),
        "{}",
        problems[0].location
    );
}

#[test]
fn a_target_without_size_reports_a_problem_and_draws_nothing() {
    let document = catalog(vec![point(json!(0.5), json!(0.5))], vec![], vec![], vec![]);
    let scene = scene(vec![entity("a", [0.0, 0.0, 0.5, 0.5], None)], vec![]);
    let context = RenderContext {
        width: 0.0,
        height: 720.0,
    };
    let (drawn, problems) = render(&compiled(&document), &scene, context, FeatureSet::default());
    assert!(points(&drawn).is_empty());
    assert_eq!(problems.len(), 1);
}

#[test]
fn hidden_features_skip_their_steps_and_templates_without_evaluating_them() {
    let failing = |feature: &str| {
        let mut step = steps_dividing_by_width().remove(1);
        step["feature"] = json!(feature);
        step
    };
    let mut document = catalog(
        vec![
            failing("ids"),
            json!({"template": "marker"}),
            point(json!(0.2), json!(0.5)),
        ],
        vec![],
        vec![],
        vec![],
    );
    document["templates"]["marker"] =
        json!({"feature": "score", "steps": [point(json!(0.3), json!(0.5))]});
    let catalog = compiled(&document);
    let bad = scene(vec![entity("bad", [0.0, 0.0, 0.0, 0.2], None)], vec![]);

    let (drawn, problems) = render(&catalog, &bad, target(), FeatureSet::default());
    assert!(points(&drawn).is_empty(), "the failing row draws nothing");
    assert_eq!(problems.len(), 1);

    let (drawn, problems) = render(
        &catalog,
        &bad,
        target(),
        FeatureSet::default().with(OverlayFeature::Ids),
    );
    assert_eq!(points(&drawn), [(0.2, 0.5), (0.3, 0.5)]);
    assert!(
        problems.is_empty(),
        "a hidden step is not evaluated: {problems:?}"
    );

    let hidden = FeatureSet::default()
        .with(OverlayFeature::Ids)
        .with(OverlayFeature::Score);
    let (drawn, _) = render(&catalog, &bad, target(), hidden);
    assert_eq!(
        points(&drawn),
        [(0.2, 0.5)],
        "a hidden template draws nothing"
    );
}

#[test]
fn a_recipe_with_everything_hidden_is_not_run() {
    let mut step = steps_dividing_by_width().remove(1);
    step["feature"] = json!("outlines");
    let document = catalog(vec![step], vec![], vec![], vec![]);
    let bad = scene(vec![entity("bad", [0.0, 0.0, 0.0, 0.2], None)], vec![]);
    let (drawn, problems) = render(
        &compiled(&document),
        &bad,
        target(),
        FeatureSet::default().with(OverlayFeature::Outlines),
    );
    assert!(drawn["points"].as_array().expect("points").is_empty() && problems.is_empty());
}
