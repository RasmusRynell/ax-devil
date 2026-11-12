use super::*;
use crate::Geometry;
use crate::{
    BoundingBox, ClassType, Classification, Confidence, EntityId, Fields, GeographicCoordinates,
    Image, ImageVelocity, Measurements, Name, ObservationTime, Point, Polygon, SceneTime, Score,
};
use bytes::Bytes;
use foundation::MediaTime;

fn entity(observation: Observation) -> Entity {
    let mut entity = Entity::new(EntityId::from(7_i64));
    entity.push_observation(observation);
    entity
}

fn observation(time: impl Into<ObservationTime>) -> Observation {
    let bounds = BoundingBox::new(0.125, 0.25, 0.375, 0.75).expect("a box");
    Observation::new(time, Geometry::Box(bounds))
}

fn class(name: &'static str, score: Option<f64>, confidence: Option<f64>) -> Classification {
    Classification {
        score: score.map(|s| Score::new(s).expect("finite")),
        confidence: confidence.map(|c| Confidence::new(c).expect("finite")),
        ..Classification::new(ClassType::from_static(name))
    }
}

fn color(rgb: &[i64; 3], score: Option<f64>, name: Option<&'static str>) -> Value {
    let mut fields = vec![pair("rgb", Value::List(rgb.map(Value::Int).to_vec()))];
    fields.extend(score.map(|score| pair("score", Value::Float(score))));
    fields.extend(name.map(|name| pair("name", Value::Str(name.into()))));
    Value::Map(fields)
}

fn pair(key: &'static str, value: Value) -> (Name, Value) {
    (key.into(), value)
}

/// `group[/origin] path = value`, one line per field.
fn show(fields: &[InspectionField]) -> Vec<String> {
    fields
        .iter()
        .map(|field| {
            let origin = field.origin.map_or(String::new(), |o| format!("/{o:?}"));
            let value = match &field.value {
                Leaf::Text(text) => format!("{text:?}"),
                other => format!("{other:?}"),
            };
            format!(
                "{:?}{origin} {} = {value}",
                field.group,
                field.path.join(".")
            )
        })
        .collect()
}

#[test]
fn fields_are_grouped_in_display_order_and_debug_fields_say_who_produced_them() {
    let mut primary = class("car", Some(0.9), Some(0.8));
    primary.attributes = vec![
        pair("color", Value::Str("red".into())),
        pair("blank", Value::Str("".into())),
        pair(
            "colors",
            Value::List(vec![
                color(&[200, 10, 0], Some(0.75), Some("red")),
                color(&[0, 0, 255], None, None),
            ]),
        ),
        pair(
            "parts",
            Value::List(vec![Value::Map(vec![pair("n", Value::Int(2))])]),
        ),
        // Colour-shaped but with fields of its own: nothing is dropped for being read as a colour.
        pair(
            "tagged",
            Value::List(vec![Value::Map(vec![
                pair(
                    "rgb",
                    Value::List(vec![Value::Int(1), Value::Int(2), Value::Int(3)]),
                ),
                pair("area", Value::Int(5)),
                pair("id", Value::Str("x".into())),
            ])]),
        ),
    ];
    let mut other = class("truck", Some(-0.5), None);
    other.attributes = vec![pair("axles", Value::Int(3))];
    let both = ObservationTime::new(Some(4), Some(MediaTime(1500))).expect("a time");
    let mut observation = observation(both);
    observation.classifications = vec![other, primary];
    observation.measurements = Some(Box::new(Measurements {
        image_velocity: Some(ImageVelocity { vx: 0.5, vy: -1.0 }),
        geographic: Some(GeographicCoordinates {
            latitude: 55.0,
            longitude: 13.0,
            elevation: None,
        }),
        ..Measurements::default()
    }));
    let source = vec![
        pair(
            "raw",
            Value::Map(vec![pair(
                "k",
                Value::List(vec![Value::Int(1), Value::Bool(true)]),
            )]),
        ),
        pair("none", Value::Null),
    ];
    let debug = vec![
        pair("latency", Value::Float(2.5)),
        pair("empty", Value::Map(Vec::new())),
        pair("list", Value::List(Vec::new())),
        pair("text", Value::Str("".into())),
    ];
    let mut entity = entity(observation.with_details(source, debug).expect("disjoint"));
    entity.motion_state = Some(MotionState::Moving);
    entity.end_reason = Some("lost".into());
    entity.images.push(Image {
        timestamp: MediaTime(900),
        bounding_box: BoundingBox::new(0.5, 0.0, 0.75, 0.5).expect("a box"),
        data: Bytes::from_static(&[1, 2, 3]),
    });

    let (fields, cut) = entity_fields(&entity, MAX_FIELDS);

    assert!(!cut);
    assert_eq!(
        show(&fields),
        [
            "Classification type = \"car\"",
            "Classification score = Float(0.9)",
            "Classification confidence = Float(0.8)",
            "Classification attributes.color = \"red\"",
            "Classification attributes.colors.red = Float(0.75)",
            "Classification attributes.colors.1 = Null",
            "Classification attributes.parts.0.n = Int(2)",
            "Classification attributes.tagged.0.rgb.0 = Int(1)",
            "Classification attributes.tagged.0.rgb.1 = Int(2)",
            "Classification attributes.tagged.0.rgb.2 = Int(3)",
            "Classification attributes.tagged.0.area = Int(5)",
            "Classification attributes.tagged.0.id = \"x\"",
            "Classification candidates.car.score = Float(0.9)",
            "Classification candidates.car.confidence = Float(0.8)",
            "Classification candidates.truck.score = Float(-0.5)",
            "Classification candidates.truck.attributes.axles = Int(3)",
            "Motion state = \"moving\"",
            "Motion velocity_in_image_space.vx = Float(0.5)",
            "Motion velocity_in_image_space.vy = Float(-1.0)",
            "Geometry x = Float(0.125)",
            "Geometry y = Float(0.25)",
            "Geometry width = Float(0.25)",
            "Geometry height = Float(0.5)",
            "Geometry geographical_coordinates.latitude = Float(55.0)",
            "Geometry geographical_coordinates.longitude = Float(13.0)",
            "Debug/Source raw.k.0 = Int(1)",
            "Debug/Source raw.k.1 = Bool(true)",
            "Debug/Source none = Null",
            "Debug/Decoder latency = Float(2.5)",
            "Debug/Decoder empty = \"{}\"",
            "Debug/Decoder list = \"[]\"",
            "Debug/Decoder text = \"\"",
            "Raw end_reason = \"lost\"",
            "Raw frame_number = Int(4)",
            "Raw timestamp = \"1970-01-01T00:00:00.001500+00:00\"",
            "Images 0.timestamp = \"1970-01-01T00:00:00.000900+00:00\"",
            "Images 0.bounding_box.x = Float(0.5)",
            "Images 0.bounding_box.y = Float(0.0)",
            "Images 0.bounding_box.width = Float(0.25)",
            "Images 0.bounding_box.height = Float(0.5)",
            "Images 0.data = \"3 bytes\"",
        ]
    );
    let marks: Vec<_> = fields
        .iter()
        .filter(|field| field.text.is_some() || field.fraction.is_some() || field.color.is_some())
        .map(|field| {
            (
                field.path.join("."),
                field.text.as_deref(),
                field.fraction,
                field.color.map(|color| [color.r, color.g, color.b]),
            )
        })
        .collect();
    assert_eq!(
        marks,
        [
            ("score".into(), Some("0.90"), Some(0.9), None),
            ("confidence".into(), Some("0.80"), Some(0.8), None),
            (
                "attributes.colors.red".into(),
                None,
                Some(0.75),
                Some([200, 10, 0])
            ),
            ("attributes.colors.1".into(), None, None, Some([0, 0, 255])),
            ("candidates.car.score".into(), Some("0.90"), Some(0.9), None),
            (
                "candidates.car.confidence".into(),
                Some("0.80"),
                Some(0.8),
                None
            ),
            ("candidates.truck.score".into(), Some("-0.50"), None, None),
        ]
    );
}

#[test]
fn queries_address_fields_by_group_and_path_and_colours_add_their_channels() {
    let mut primary = class("car", Some(0.9), None);
    primary.attributes = vec![pair(
        "colors",
        Value::List(vec![
            color(&[200, 10, 0], Some(0.75), Some("red")),
            color(&[190, 20, 5], Some(0.25), Some("red")),
        ]),
    )];
    let mut observation = observation(SceneTime::Frame(4));
    observation.classifications = vec![primary];
    let debug = vec![pair("vendor", Value::Map(vec![pair("id", Value::Int(7))]))];
    let observation = observation.with_details(Vec::new(), debug).expect("ok");

    // A path names a key by its trailing parts, in any case; `colors.red` does not name the channels below it.
    let paths = [
        "Classification.Score",
        "colors.red",
        "r",
        "g",
        "b",
        "vendor.id",
    ]
    .map(str::to_lowercase);
    let budget = &mut Budget {
        leaves: 1000,
        bytes: 10_000,
    };
    let items: Vec<_> = query_leaves(&entity(observation), &paths, budget)
        .expect("within budget")
        .into_iter()
        .map(|(key, value)| match value {
            Leaf::Int(whole) => format!("{key} = {whole}"),
            Leaf::Float(number) => format!("{key} = {number}"),
            other => format!("{key} = {other:?}"),
        })
        .collect();

    assert_eq!(
        items,
        [
            "classification.score = 0.9",
            "classification.attributes.colors.red = 0.75",
            "classification.attributes.colors.red.rgb.r = 200",
            "classification.attributes.colors.red.rgb.g = 10",
            "classification.attributes.colors.red.rgb.b = 0",
            "classification.attributes.colors.red = 0.25",
            "classification.attributes.colors.red.rgb.r = 190",
            "classification.attributes.colors.red.rgb.g = 20",
            "classification.attributes.colors.red.rgb.b = 5",
            "debug.vendor.id = 7",
        ]
    );
}

#[test]
fn a_polygon_lists_its_bounds_and_point_count_and_a_timestamp_is_a_datetime() {
    let points = [(0.1, 0.1), (0.5, 0.2), (0.3, 0.9)]
        .map(|(x, y)| Point::new(x, y).expect("a point"))
        .to_vec();
    let mut observation = observation(SceneTime::Timestamp(MediaTime(1500)));
    observation.geometry = Geometry::Polygon(Polygon::new(points).expect("a polygon"));

    let (fields, _) = entity_fields(&entity(observation), MAX_FIELDS);

    let shown = show(&fields);
    assert!(
        shown.contains(&"Geometry points = Int(3)".to_owned()),
        "{shown:?}"
    );
    assert_eq!(
        shown.last().map(String::as_str),
        Some("Raw timestamp = \"1970-01-01T00:00:00.001500+00:00\"")
    );
    assert!(
        shown[0].starts_with("Geometry x = "),
        "no class, so geometry is first"
    );
}

#[test]
fn a_search_collects_only_what_it_names_and_refuses_to_spend_more_than_its_budget() {
    // One long parent key above many scalar children: every leaf's key would repeat the parent's.
    let parent = "p".repeat(65_536);
    let children = (0..5_000).map(|n| Value::Map(vec![pair("id", Value::Int(n))]));
    let debug = vec![(Name::Owned(parent), Value::List(children.collect()))];
    let observation = observation(SceneTime::Frame(0))
        .with_details(Vec::new(), debug)
        .expect("ok");
    let entity = entity(observation);
    let search = |path: &str, leaves, bytes| {
        query_leaves(&entity, &[path.to_owned()], &mut Budget { leaves, bytes })
    };

    // Nothing is collected for a path no leaf has, so no byte is spent.
    assert_eq!(search("nothing", 1_000_000, 0), Ok(Vec::new()));
    // Each match costs its whole key, and the search is refused rather than cut.
    assert_eq!(
        search("7.id", 1_000_000, 1_000_000).map(|found| found.len()),
        Ok(1)
    );
    assert_eq!(search("id", 1_000_000, 1_000_000), Err(OverBudget));
    // Walking unwanted leaves is bounded too.
    assert_eq!(search("nothing", 100, 0), Err(OverBudget));
}

#[test]
fn an_inspection_lists_no_more_bytes_of_paths_than_its_budget_however_long_a_key_over_many_leaves()
{
    let parent = "p".repeat(3 * 1024 * 1024);
    let children = (0..4_096).map(Value::Int).collect();
    let debug = vec![(Name::Owned(parent), Value::List(children))];
    let observation = observation(SceneTime::Frame(0))
        .with_details(Vec::new(), debug)
        .expect("ok");

    let (fields, cut) = entity_fields(&entity(observation), MAX_FIELDS);

    let listed = fields
        .iter()
        .filter(|f| f.group == FieldGroup::Debug)
        .count();
    assert_eq!(
        (listed, cut),
        (2, true),
        "two copies of the key fit, the rest are left out"
    );
}

#[test]
fn a_scalar_text_beyond_the_budget_is_left_out_and_a_search_for_it_is_refused() {
    let long = "x".repeat(MAX_INSPECTION_BYTES + 1);
    let debug = vec![
        pair("long", Value::Str(long.into())),
        pair("short", Value::Int(1)),
    ];
    let observation = observation(SceneTime::Frame(0))
        .with_details(Vec::new(), debug)
        .expect("ok");
    let entity = entity(observation);

    let (fields, cut) = entity_fields(&entity, MAX_FIELDS);
    let debug: Vec<_> = fields
        .iter()
        .filter(|f| f.group == FieldGroup::Debug)
        .collect();
    assert!(cut && debug.len() == 1 && debug[0].path == ["short"]);

    let search = |path: &str| {
        let budget = &mut Budget {
            leaves: 100,
            bytes: 1_000,
        };
        query_leaves(&entity, &[path.to_owned()], budget).map(|found| found.len())
    };
    assert_eq!(search("long"), Err(OverBudget));
    assert_eq!(search("short"), Ok(1));
}

#[test]
fn a_path_with_a_sigma_is_refused_for_a_key_too_long_to_lowercase_whole() {
    let debug = vec![(
        Name::Owned("p".repeat(3 * 1024 * 1024)),
        Value::List((0..4_096).map(Value::Int).collect()),
    )];
    let observation = observation(SceneTime::Frame(0))
        .with_details(Vec::new(), debug)
        .expect("ok");
    let budget = &mut Budget {
        leaves: 100_000,
        bytes: 1_000_000,
    };

    let refused = query_leaves(&entity(observation), &["σ".to_owned()], budget);

    assert_eq!(refused.map(|found| found.len()), Err(OverBudget));
}

#[test]
fn a_refusal_stays_when_the_leaf_that_caused_it_is_also_a_text_beyond_the_budget() {
    let key = Name::Owned("p".repeat(query::MAX_SIGMA_KEY_BYTES + 1));
    let text = vec![pair("t", Value::Str("x".repeat(5_000).into()))];
    let observation = observation(SceneTime::Frame(0))
        .with_details(Vec::new(), vec![(key, Value::Map(text))])
        .expect("ok");
    let budget = &mut Budget {
        leaves: 100,
        bytes: 1_000,
    };

    let refused = query_leaves(&entity(observation), &["σ".to_owned()], budget);

    assert_eq!(refused.map(|found| found.len()), Err(OverBudget));
}

#[test]
fn a_final_sigma_lowercases_with_its_neighbours_as_the_key_does() {
    let debug = vec![pair("AΣ", Value::Int(7))];
    let observation = observation(SceneTime::Frame(0))
        .with_details(Vec::new(), debug)
        .expect("ok");
    let entity = entity(observation);
    let found = |path: &str| {
        let budget = &mut Budget {
            leaves: 100,
            bytes: 1000,
        };
        query_leaves(&entity, &[path.to_owned()], budget).map(|found| found.len())
    };

    assert_eq!(found("debug.aς"), Ok(1));
    assert_eq!(found("debug.aσ"), Ok(0));
}

#[test]
fn an_integer_reaches_the_wire_exactly_and_is_not_a_float() {
    let source: Fields = vec![
        pair("big", Value::Int(i64::MAX)),
        pair("int", Value::Int(3)),
        pair("float", Value::Float(3.0)),
    ];
    let entity = entity(
        observation(SceneTime::Frame(0))
            .with_details(source, Vec::new())
            .expect("ok"),
    );

    let (fields, _) = entity_fields(&entity, MAX_FIELDS);

    let wire: Vec<_> = fields
        .iter()
        .filter(|field| field.group == FieldGroup::Debug)
        .map(|field| serde_json::to_string(&field.value).expect("json"))
        .collect();
    assert_eq!(
        wire,
        [
            r#"{"kind":"int","value":"9223372036854775807"}"#,
            r#"{"kind":"int","value":"3"}"#,
            r#"{"kind":"float","value":3.0}"#,
        ]
    );
}

#[test]
fn a_text_is_kept_whole_and_only_the_field_limit_says_it_cut() {
    let long = "é".repeat(10_000);
    let source: Fields = vec![
        pair("a", Value::Float(f64::NAN)),
        (Name::Owned("b".repeat(10_000)), Value::Int(1)),
        pair("long", Value::Str(long.clone().into())),
    ];
    let entity = entity(
        observation(SceneTime::Frame(0))
            .with_details(source, Vec::new())
            .expect("ok"),
    );

    let (all, cut) = entity_fields(&entity, MAX_FIELDS);
    let debug: Vec<_> = all
        .iter()
        .filter(|f| f.group == FieldGroup::Debug)
        .collect();

    assert!(!cut, "nothing was left out");
    assert_eq!(
        debug[0].value,
        Leaf::Text("NaN".into()),
        "a number the wire cannot carry"
    );
    assert_eq!(debug[1].path[0].chars().count(), 10_000);
    assert_eq!(debug[2].value, Leaf::Text(long));

    let (few, cut) = entity_fields(&entity, 2);
    assert!(cut && few.len() == 2 && few[0].group == FieldGroup::Geometry);
    let (exact, _) = entity_fields(&entity, all.len());
    assert_eq!(exact, all);
    let plain = self::entity(observation(SceneTime::Frame(0)));
    let (fields, cut) = entity_fields(&plain, 5);
    assert!(
        !cut && fields.len() == 5,
        "exactly the limit is not a cut: {fields:?}"
    );
}
