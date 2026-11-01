//! Catalog compilation: the built-in catalogs compile, and a broken catalog is rejected with every problem named at
//! the place in the document it is about.

use render_catalog::{FeatureSet, OverlayFeature, compile, ir};
use serde_json::{Value, json};

fn point(x: Value, enabled: Value) -> Value {
    json!({"primitive": "point", "enabled": enabled, "fields": {"position": {"x": x, "y": 0}}})
}

fn call(name: &str, args: Value) -> Value {
    json!({"call": name, "args": args})
}

fn px(value: f64) -> Value {
    json!({"value": value, "unit": "px"})
}

/// A small valid catalog: two fallbacks, two classification recipes (one for two types) and a relation recipe, all
/// drawing through one template.
fn catalog() -> Value {
    let recipe = |id: &str, kind: &str, types: Option<Value>| {
        let mut selector = json!({"kind": kind});
        if let Some(types) = types {
            selector["types"] = types;
        }
        json!({"id": id, "selector": selector, "steps": [{"template": "marker"}]})
    };
    json!({
        "metadata": {"id": "test", "name": "Test", "schema_version": 3},
        "templates": {
            "marker": {
                "parameters": {"x": {"type": "number", "default": 0.25}},
                "values": {"origin": {"ref": ["parameters", "x"]}},
                "steps": [point(json!({"ref": ["values", "origin"]}), json!(true))],
            },
        },
        "recipes": {
            "fallbacks": [recipe("motion", "unclassified", None), recipe("generic", "classified", None)],
            "classifications": [
                recipe("vehicle", "classification", Some(json!(["vehicle", "car"]))),
                recipe("human", "classification", Some(json!(["human"]))),
            ],
            "relations": [recipe("has_part", "relation", Some(json!(["has_part"])))],
        },
    })
}

/// The document with `templates.main` set to `main` and the unclassified fallback drawing it.
fn with_main(main: Value) -> Value {
    let mut document = catalog();
    document["templates"]["main"] = main;
    document["recipes"]["fallbacks"][0]["steps"] = json!([{"template": "main"}]);
    document
}

/// Compiles `document`, expecting it to fail, and returns each problem as `(code, location, message)` after checking
/// that every location points into the document.
fn problems(document: &Value) -> Vec<(&'static str, String, String)> {
    let Err(problems) = compile(&document.to_string()) else {
        panic!("compiled: {document:#}")
    };
    for problem in &problems {
        assert!(
            document.pointer(&problem.location).is_some(),
            "{problem:?} points outside the document"
        );
    }
    problems
        .into_iter()
        .map(|problem| (problem.code, problem.location, problem.message))
        .collect()
}

/// The single problem compiling `document` reports.
fn problem(document: &Value) -> (&'static str, String, String) {
    let mut found = problems(document);
    assert_eq!(found.len(), 1, "{found:#?}");
    found.remove(0)
}

#[test]
fn built_in_catalogs_compile_with_their_routes_and_features() {
    let root = concat!(env!("CARGO_MANIFEST_DIR"), "/../../assets/catalogs");
    for name in [
        "standard", "minimal", "chunky", "glass", "tracking", "classic",
    ] {
        let source = std::fs::read_to_string(format!("{root}/{name}.json"))
            .expect("built-in catalog is readable");
        let catalog = compile(&source).unwrap_or_else(|problems| panic!("{name}: {problems:#?}"));
        assert_eq!(catalog.metadata().id, format!("built-in.{name}"));
        let routing = &catalog.ir().routing;
        for class in ["human", "car", "vehicle", "head"] {
            assert!(
                routing.classifications.contains_key(class),
                "{name} routes {class}"
            );
        }
        assert!(routing.relations.contains_key("has_part"));
        assert_ne!(routing.unclassified, routing.classified);
        let features = catalog.features();
        assert!(
            features.contains(OverlayFeature::Outlines)
                && features.contains(OverlayFeature::Relations)
        );
    }
}

#[test]
fn hiding_a_feature_hides_the_templates_tagged_with_it() {
    let mut document = with_main(
        json!({"steps": [{"template": "marker", "feature": "ids"}, point(json!(0.5), json!(true))]}),
    );
    document["templates"]["marker"]["feature"] = json!("outlines");
    let catalog = compile(&document.to_string()).expect("compiles");
    let recipe = &catalog.ir().recipes[catalog.ir().routing.unclassified];
    let ir::Action::Call(main) = &recipe.program.steps[0].action else {
        panic!("calls main")
    };
    let ir::Action::Call(marker) = &main.program.steps[0].action else {
        panic!("calls marker")
    };
    let outlines = FeatureSet::default().with(OverlayFeature::Outlines);
    let ids = FeatureSet::default().with(OverlayFeature::Ids);
    assert!(!marker.program.draws(outlines));
    assert!(
        main.program.draws(outlines.with(OverlayFeature::Ids)),
        "the plain point stays"
    );
    assert!(main.program.draws(ids));
    assert_eq!(catalog.features(), outlines.with(OverlayFeature::Ids));
}

#[test]
fn every_independent_problem_is_reported_at_once() {
    let mut document =
        with_main(json!({"steps": [point(json!({"ref": ["values", "missing"]}), json!(true))]}));
    document["metadata"]["schema_version"] = json!(2);
    document["recipes"]["classifications"][1]["steps"] = json!([{"template": "nowhere"}]);
    let found = problems(&document);
    let codes: Vec<&str> = found.iter().map(|(code, _, _)| *code).collect();
    assert_eq!(
        codes,
        [
            "unsupported_version",
            "unknown_reference",
            "unknown_template"
        ],
        "{found:#?}"
    );
}

#[test]
fn broken_expressions_are_rejected_even_in_disabled_steps() {
    let cases = [
        (json!({"ref": ["values", "missing"]}), "unknown_reference"),
        (json!({"ref": ["nowhere", "x"]}), "unknown_reference"),
        (
            json!({"ref": ["parameters", "x"], "extra": 1}),
            "invalid_structure",
        ),
        (call("missing", json!({})), "unknown_operation"),
        (call("div", json!({"numerator": 1})), "invalid_structure"),
        (
            call("div", json!({"numerator": "wrong", "denominator": 2})),
            "type_mismatch",
        ),
        (call("add", json!({"values": [true, 1]})), "type_mismatch"),
        (
            call("mul", json!({"values": [px(2.0), px(3.0)]})),
            "type_mismatch",
        ),
        (
            call("div", json!({"numerator": 1, "denominator": px(2.0)})),
            "type_mismatch",
        ),
        (
            call("if", json!({"condition": true, "then": 1, "else": "text"})),
            "type_mismatch",
        ),
    ];
    for (expression, code) in cases {
        let (found, location, message) = problem(&with_main(
            json!({"steps": [point(expression.clone(), json!(false))]}),
        ));
        assert_eq!(found, code, "{expression}: {message}");
        assert!(
            location.starts_with("/templates/main/steps/0/fields/position/x"),
            "{expression}: {location}"
        );
    }
}

#[test]
fn nullable_values_need_a_guard_before_non_null_use() {
    let parameters = json!({"x": {"type": "number", "nullable": true, "default": null}});
    let x = json!({"ref": ["parameters", "x"]});
    let unguarded =
        with_main(json!({"parameters": parameters, "steps": [point(x.clone(), json!(true))]}));
    assert_eq!(problem(&unguarded).0, "type_mismatch");
    let guard = call("is_present", json!({"value": x}));
    let guarded = with_main(json!({"parameters": parameters, "steps": [point(x, guard)]}));
    assert!(compile(&guarded.to_string()).is_ok());
}

#[test]
fn cycles_and_limits_are_rejected() {
    let recursive = with_main(json!({"steps": [{"template": "main"}]}));
    assert_eq!(problem(&recursive).0, "template_cycle");
    let values = json!({"a": {"ref": ["values", "b"]}, "b": {"ref": ["values", "a"]}});
    assert_eq!(
        problem(&with_main(json!({"values": values, "steps": []}))).0,
        "value_cycle"
    );

    let mut document = with_main(json!({"steps": [{"template": "level14"}]}));
    document["templates"]["leaf"] = json!({"steps": [point(json!(0), json!(true))]});
    let mut previous = "leaf".to_string();
    for level in 0..15 {
        document["templates"][format!("level{level}")] =
            json!({"steps": [{"template": previous}, {"template": previous}]});
        previous = format!("level{level}");
    }
    let (code, _, message) = problems(&document).remove(0);
    assert_eq!(
        (code, message.as_str()),
        (
            "execution_limit",
            "Worst-case expanded execution limit exceeded."
        )
    );

    let mut deep = json!(0);
    for _ in 0..70 {
        deep = json!([deep]);
    }
    assert_eq!(
        problem(&with_main(json!({"values": {"deep": deep}, "steps": []}))).0,
        "execution_limit"
    );
    let large = format!("{{\"padding\": \"{}\"}}", "x".repeat(2_000_001));
    let found = compile(&large).expect_err("too large");
    assert_eq!(
        (found[0].code, found[0].location.as_str()),
        ("execution_limit", "")
    );
}

#[test]
fn recipes_route_every_class_to_one_recipe_with_exactly_two_fallbacks() {
    let mut document = catalog();
    document["recipes"]["classifications"][1]["selector"]["types"] = json!(["human", "car"]);
    let found = problem(&document);
    assert_eq!(found.1, "/recipes/classifications/1/selector/types");
    assert_eq!(found.2, "Duplicate classifications selector: car.");

    let mut document = catalog();
    let duplicate = json!({"id": "again", "selector": {"kind": "classified"}, "steps": []});
    document["recipes"]["fallbacks"]
        .as_array_mut()
        .expect("fallbacks")
        .push(duplicate);
    assert_eq!(
        problem(&document).2,
        "Duplicate fallbacks selector: classified."
    );

    let mut document = catalog();
    document["recipes"]["fallbacks"]
        .as_array_mut()
        .expect("fallbacks")
        .remove(1);
    assert_eq!(problem(&document).1, "/recipes/fallbacks");

    let mut document = catalog();
    document["recipes"]["classifications"][0]["id"] = json!("motion");
    assert_eq!(problem(&document).1, "/recipes/classifications/0/id");

    let mut document = catalog();
    document["recipes"]["fallbacks"][1]["bindings"] = json!({"color": {
        "source": "primary_classification_attribute", "attribute": "color", "value_shape": "number", "picker": "value"
    }});
    document["recipes"]["fallbacks"][1]["values"] =
        json!({"bad": {"ref": ["bindings", "color", "score"]}});
    assert_eq!(problem(&document).0, "unknown_reference");
}

#[test]
fn document_level_problems_name_their_place() {
    for version in [json!(2), json!(99), json!(true)] {
        let mut document = catalog();
        document["metadata"]["schema_version"] = version;
        assert_eq!(problem(&document).0, "unsupported_version");
    }
    let mut document = catalog();
    document["metadata"]["id"] = json!(123);
    assert_eq!(problem(&document).1, "/metadata/id");
    document = catalog();
    document["recipes"]["fallbacks"][0]["bindings"] = json!([]);
    assert_eq!(problem(&document).1, "/recipes/fallbacks/0/bindings");

    let found =
        compile(r#"{"metadata": {}, "templates": {"a": 1, "a": 2}}"#).expect_err("duplicate key");
    assert_eq!(
        (found[0].code, found[0].location.as_str()),
        ("duplicate_definition", "/templates/a")
    );
    assert_eq!(compile("{").expect_err("not JSON")[0].code, "invalid_json");
}

#[test]
fn template_inputs_must_match_the_parameters() {
    let child = json!({"parameters": {"x": {"type": "number", "required": true, "nullable": true}}, "steps": []});
    let mut document = with_main(json!({"steps": [{"template": "child"}]}));
    document["templates"]["child"] = child;
    assert!(
        problem(&document)
            .2
            .starts_with("Missing required template inputs: x")
    );
    document["templates"]["main"] =
        json!({"steps": [{"template": "child", "inputs": {"x": 1, "extra": 1}}]});
    assert_eq!(problem(&document).1, "/templates/main/steps/0/inputs/extra");
    document["templates"]["main"] =
        json!({"values": {"caller": 1}, "steps": [{"template": "child", "inputs": {"x": 1}}]});
    document["templates"]["child"]["steps"] =
        json!([point(json!({"ref": ["values", "caller"]}), json!(true))]);
    assert_eq!(problem(&document).0, "unknown_reference");
}

#[test]
fn values_outside_their_bounds_are_rejected_where_they_are_written() {
    let styled = |alpha: Value| {
        json!({"steps": [{
            "primitive": "box",
            "fields": {"geometry": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}},
            "style": {"fill": {"color": [255, 0, 0], "alpha": alpha}},
        }]})
    };
    let (_, location, message) = problem(&with_main(styled(json!(300))));
    assert!(
        location.starts_with("/templates/main/steps/0/style"),
        "{location}"
    );
    assert_eq!(message, "alpha must be at least 0 and at most 255.");
    assert!(compile(&with_main(styled(json!(255))).to_string()).is_ok());

    let default =
        json!({"parameters": {"color": {"type": "rgb", "default": [0, 0, 300]}}, "steps": []});
    let found = problem(&with_main(default));
    assert_eq!(found.1, "/templates/main/parameters/color/default");
    assert_eq!(found.2, "color item must be at least 0 and at most 255.");

    let declaration =
        json!({"type": "length", "exclusive_minimum": 2, "maximum": 40, "default": px(8.0)});
    let label = json!({"parameters": {"size": declaration}, "steps": [{
        "primitive": "text",
        "fields": {"position": {"x": 0.5, "y": 0.5}, "text": "x", "anchor": "center"},
        "style": {"text": {"color": [255, 255, 255], "size": {"ref": ["parameters", "size"]}}},
    }]});
    let mut document =
        with_main(json!({"steps": [{"template": "label", "inputs": {"size": px(1.0)}}]}));
    document["templates"]["label"] = label;
    let found = problem(&document);
    assert_eq!(found.1, "/templates/main/steps/0/inputs/size");
    assert_eq!(found.2, "length must be greater than 2 and at most 40.");

    let color = json!({"type": "rgb", "minimum": 0, "default": [0, 0, 0]});
    let bounded_rgb = json!({"parameters": {"color": color}, "steps": []});
    assert!(
        problem(&with_main(bounded_rgb))
            .2
            .starts_with("A rgb parameter cannot declare minimum")
    );

    let stops = json!([{"at": 1, "color": [0, 0, 0]}, {"at": 0, "color": [255, 255, 255]}]);
    let unused = json!({"values": {"unused": call("color_ramp", json!({"value": 0.5, "stops": stops}))}, "steps": []});
    assert_eq!(
        problem(&with_main(unused)).2,
        "Color stops must be strictly increasing."
    );
}
