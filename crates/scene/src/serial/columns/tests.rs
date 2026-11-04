use std::{collections::HashSet, fs, path::PathBuf};

use super::*;
use crate::{
    BoundingBox, ClassRule, ClassType, EntityId, EntityRelation, FilterChoice, FilterOption,
    SceneTime, TimeSlice, Value, serial::from_json_str,
};

fn oracle() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/oracle")
}

fn scenes(file: &str) -> Vec<Scene> {
    let text = fs::read_to_string(oracle().join(file)).expect("oracle file");
    from_json_str(&text).expect("oracle scenes load")
}

/// The policy of the goldens. Bits: 0 humans, 1 vehicles, 2 heads, 3 unmapped classes, 4 unclassified. Animals and
/// crowds are selected by no option. `frontend/src/present/columns.test.ts` reads these bits.
fn policy() -> FilterPolicy {
    let option = |id: &'static str, types: &[&'static str], include_empty: bool| FilterOption {
        id: id.into(),
        label: id.into(),
        default_enabled: true,
        rule: ClassRule {
            types: types.iter().map(|&name| name.into()).collect(),
            include_empty,
            exclude: false,
        },
    };
    FilterPolicy {
        name: "golden",
        options: vec![
            option("humans", &["human", "person", "static_person"], false),
            option(
                "vehicles",
                &["vehicle_other", "car", "bus", "truck", "bike", "bicycle"],
                false,
            ),
            option("heads", &["head"], false),
            option("unmapped", &["?"], false),
            option("unclassified", &[], true),
        ],
    }
}

fn words(bytes: &[u8]) -> Vec<u32> {
    let (words, _) = bytes.as_chunks::<4>();
    words.iter().copied().map(u32::from_le_bytes).collect()
}

fn header(item: &[u8]) -> Vec<u32> {
    words(&item[..HEADER_BYTES])
}

/// The `u32` words of `section`, `length` of them.
fn section(item: &[u8], section: Section, length: usize) -> Vec<u32> {
    let at = header(item)[COUNTS + section as usize] as usize;
    words(&item[at..at + length * 4])
}

/// The item's strings, in id order.
fn strings(item: &[u8]) -> Vec<String> {
    let head = header(item);
    let offsets = section(item, Section::StringOffsets, head[8] as usize + 1);
    let at = head[COUNTS + Section::StringBytes as usize] as usize;
    offsets
        .windows(2)
        .map(|pair| {
            let bytes = &item[at + pair[0] as usize..at + pair[1] as usize];
            String::from_utf8(bytes.to_vec()).expect("UTF-8")
        })
        .collect()
}

/// An entity with an observation of the named classes.
fn entity(id: &str, classifications: Vec<Classification>) -> Entity {
    let bounds = BoundingBox::new(0.1, 0.1, 0.2, 0.2).expect("a box");
    let mut observation = Observation::new(SceneTime::Frame(0), Geometry::Box(bounds));
    observation.classifications = classifications;
    let mut entity = Entity::new(EntityId::new(id).expect("an id"));
    entity.push_observation(observation);
    entity
}

fn scene_of(entities: Vec<Entity>) -> Scene {
    Scene::new(TimeSlice::at(SceneTime::Frame(0)))
        .with_entities(entities)
        .expect("a scene")
}

/// The keep rule over class mask words, as the frontend evaluates it.
fn keeps_by_words(mask: &[u32], enabled: &[u32], options: usize) -> bool {
    let overlap = mask.iter().zip(enabled).any(|(m, e)| m & e != 0);
    let only_enabled = mask.iter().zip(enabled).all(|(m, e)| m & !e == 0);
    options == 0 || overlap || (enabled.iter().any(|&e| e != 0) && only_enabled)
}

#[test]
fn the_class_mask_applies_the_filter_policys_rule_with_bit_operations() {
    let policy = policy();
    let count = policy.options.len();
    let mut entities = 0;
    for file in [
        "sheets/classes.json",
        "sheets/history.json",
        "mot/gt.scenes.json",
    ] {
        for scene in scenes(file) {
            for entity in scene
                .entities()
                .iter()
                .filter(|e| e.latest_observation().is_some())
            {
                let mask = class_mask(&policy, entity.latest_observation().expect("observed"));
                for enabled in 0..1_u32 << count {
                    let choice = FilterChoice {
                        overrides: policy
                            .options
                            .iter()
                            .enumerate()
                            .map(|(bit, option)| (option.id.to_string(), enabled >> bit & 1 == 1))
                            .collect(),
                    };
                    let by_bits = keeps_by_words(&mask, &[enabled], count);
                    assert_eq!(
                        by_bits,
                        policy.keeps(&choice, entity),
                        "{} {enabled:b}",
                        entity.id()
                    );
                }
                entities += 1;
            }
        }
    }
    assert!(entities > 20);

    // Every class of the latest observation counts, not only the primary one.
    let history = &scenes("sheets/history.json")[0];
    let ranked = history
        .entity("ranked")
        .and_then(Entity::latest_observation);
    assert_eq!(class_mask(&policy, ranked.expect("observed")), [0b00111]);
}

#[test]
fn a_policy_of_more_than_32_options_gets_several_mask_words_per_row_that_filter_like_the_policy() {
    const OPTIONS: usize = 70;
    let mut policy = policy();
    policy.options = (0..OPTIONS)
        .map(|index| {
            let mut option = policy.options[0].clone();
            option.id = format!("c{index}").into();
            option.rule.types = vec![format!("c{index}").into()];
            option
        })
        .collect();
    let classes = |names: &[&str]| {
        let to_class =
            |name: &str| Classification::new(ClassType::new(name.to_string()).expect("a class"));
        names.iter().map(|name| to_class(name)).collect::<Vec<_>>()
    };
    let scene = scene_of(vec![
        entity("first", classes(&["c0"])),
        entity("last", classes(&["c69"])),
        entity("both", classes(&["c31", "c32", "c64"])),
        entity("unlisted", classes(&["other"])),
        entity("none", Vec::new()),
    ]);

    let item = encode_sample_item(&scene, &policy).expect("encodes");
    let head = header(&item);
    assert_eq!(head[7], 3);
    let masks = section(&item, Section::ClassMask, 5 * 3);
    assert_eq!(masks[..9], [1, 0, 0, 0, 0, 1 << 5, 1 << 31, 1, 1]);
    assert_eq!(masks[9..], [0; 6]);

    let rows = scene.entities();
    for enabled in [
        vec![],
        vec![0],
        vec![69],
        vec![0, 69],
        vec![32, 64],
        (0..70).collect(),
    ] {
        let mut words = [0_u32; 3];
        let mut choice = FilterChoice::default();
        for index in 0..OPTIONS {
            let on = enabled.contains(&index);
            choice.overrides.insert(format!("c{index}"), on);
            words[index / 32] |= u32::from(on) << (index % 32);
        }
        for (row, entity) in rows.iter().enumerate() {
            assert_eq!(
                keeps_by_words(&masks[row * 3..row * 3 + 3], &words, OPTIONS),
                policy.keeps(&choice, entity),
                "{} with {enabled:?}",
                entity.id()
            );
        }
    }
}

#[test]
fn strings_sit_in_three_regions_with_one_id_per_distinct_string() {
    // An entity named like a motion state and one named like a class share the id of that string.
    let mut moving = entity("moving", classes_of("car"));
    moving.motion_state = Some(MotionState::Stationary);
    let scene = scene_of(vec![
        moving,
        entity("car", classes_of("car")),
        entity("p", Vec::new()),
    ]);

    let item = encode_sample_item(&scene, &policy()).expect("encodes");
    let head = header(&item);
    let (count, states, vocab) = (head[8] as usize, head[9] as usize, head[10] as usize);
    let table = strings(&item);
    assert_eq!(table[..states], ["moving", "stationary", "unknown"]);
    assert_eq!(table[states..vocab], ["car"]);
    assert_eq!(table[vocab..], ["p"]);
    assert_eq!(count, table.len());
    assert_eq!(table.iter().collect::<HashSet<_>>().len(), count);
    assert_eq!(section(&item, Section::EntityId, 3), [0, 3, 4]);
    assert_eq!(section(&item, Section::Class, 3), [3, 3, u32::MAX]);
    assert_eq!(item[head[COUNTS + Section::Motion as usize] as usize], 2);
}

#[test]
fn relations_ship_sorted_by_kind_source_and_target_whatever_order_the_scene_holds_them() {
    let relation = |kind: &str, from: &str, to: &str| {
        EntityRelation::new(
            kind.to_owned(),
            EntityId::new(from).expect("an id"),
            EntityId::new(to).expect("an id"),
        )
        .expect("a relation")
    };
    let scene = scene_of(vec![
        entity("a", Vec::new()),
        entity("b", Vec::new()),
        entity("c", Vec::new()),
    ])
    .with_relations([
        relation("has_part", "b", "a"),
        relation("attached", "c", "a"),
        relation("has_part", "a", "c"),
        relation("has_part", "a", "b"),
    ]);

    let item = encode_sample_item(&scene, &policy()).expect("encodes");
    let table = strings(&item);
    let kinds: Vec<&str> = section(&item, Section::RelKind, 4)
        .into_iter()
        .map(|id| table[id as usize].as_str())
        .collect();
    assert_eq!(kinds, ["attached", "has_part", "has_part", "has_part"]);
    assert_eq!(section(&item, Section::RelSource, 4), [2, 0, 0, 1]);
    assert_eq!(section(&item, Section::RelTarget, 4), [0, 1, 2, 0]);
}

#[test]
fn speed_is_the_latest_observations_world_speed_and_nan_without_one() {
    let bounds = BoundingBox::new(0.1, 0.1, 0.2, 0.2).expect("a box");
    let mut moving = Entity::new(EntityId::new("moving").expect("an id"));
    for (frame, speed) in [(0, 1.5), (1, 2.5)] {
        let mut observation = Observation::new(SceneTime::Frame(frame), Geometry::Box(bounds));
        observation.measurements = Some(Box::new(crate::Measurements {
            world_velocity: Some(crate::WorldVelocity {
                pitch: 0.0,
                speed,
                yaw: 0.0,
            }),
            ..Default::default()
        }));
        moving.push_observation(observation);
    }
    let scene = scene_of(vec![moving, entity("still", Vec::new())]);

    let item = encode_sample_item(&scene, &policy()).expect("encodes");

    let at = header(&item)[COUNTS + Section::Speed as usize] as usize;
    let speed = |row: usize| {
        f64::from_le_bytes(
            item[at + row * 8..at + row * 8 + 8]
                .try_into()
                .expect("8 bytes"),
        )
    };
    assert_eq!(speed(0), 2.5);
    assert!(speed(1).is_nan());
}

fn classes_of(name: &str) -> Vec<Classification> {
    vec![Classification::new(
        ClassType::new(name.to_string()).expect("a class"),
    )]
}

#[test]
fn attributes_ship_as_one_column_per_name_and_kind_and_only_scalars_and_colour_lists() {
    let name = |text: &'static str| crate::Name::Borrowed(text);
    let colour = |red: i64| {
        Value::Map(vec![(
            name("rgb"),
            Value::List(vec![Value::Int(red), Value::Int(0), Value::Int(0)]),
        )])
    };
    let attributes = |fields: Vec<(&'static str, Value)>| {
        let mut classification = Classification::new(ClassType::from_static("car"));
        classification.attributes = fields
            .into_iter()
            .map(|(key, value)| (name(key), value))
            .collect();
        classification
    };
    let first = attributes(vec![
        ("size", Value::Int(3)),
        ("tags", Value::List(vec![Value::Str("a".into())])),
        ("meta", Value::Map(Vec::new())),
        ("none", Value::Null),
        ("shades", Value::List(Vec::new())),
        ("ok", Value::List(vec![colour(255)])),
        ("bad", Value::List(vec![colour(256)])),
    ]);
    let second = attributes(vec![("size", Value::Str("big".into()))]);
    let scene = scene_of(vec![entity("a", vec![first]), entity("b", vec![second])]);

    let item = encode_sample_item(&scene, &policy()).expect("encodes");

    // size (number), size (string), shades and ok (colour lists).
    assert_eq!(header(&item)[6], 4);
    assert_eq!(item.len() % 8, 0);
}

#[test]
fn a_sample_whose_dense_attribute_columns_exceed_the_item_is_refused_before_they_are_built() {
    // 30,000 entities with a distinct numeric attribute each would be 30,000 columns of 30,000 numbers (7.2 GB).
    let entities = (0..30_000)
        .map(|index| {
            let mut classification = Classification::new(ClassType::from_static("car"));
            classification.attributes = vec![(
                crate::Name::Owned(format!("attribute{index}")),
                Value::Int(1),
            )];
            entity(&format!("e{index}"), vec![classification])
        })
        .collect();

    let error = encode_sample_item(&scene_of(entities), &policy()).expect_err("too large");

    assert!(error.to_string().contains("too large"), "{error}");
}

fn golden(name: &str, scene: &Scene) {
    let item = encode_sample_item(scene, &policy()).expect("the oracle scene encodes");
    let path = oracle().join("items").join(format!("{name}.bin"));
    if std::env::var_os("UPDATE_GOLDENS").is_some() {
        fs::create_dir_all(path.parent().expect("a folder")).expect("items folder");
        fs::write(&path, &item).expect("writes the golden");
        return;
    }
    let expected = fs::read(&path).unwrap_or_default();
    assert!(
        expected == item,
        "{name}.bin is missing or stale; regenerate with UPDATE_GOLDENS=1 make test-backend TEST=sample_item_goldens"
    );
}

#[test]
fn sample_item_goldens_are_stable() {
    let mut names = Vec::new();
    for sheet in scenes_in("sheets") {
        golden(&sheet.0, &sheet.1);
        names.push(sheet.0);
    }
    for file in ["det", "gt"] {
        for (index, scene) in scenes(&format!("mot/{file}.scenes.json"))
            .iter()
            .enumerate()
        {
            let name = format!("mot_{file}_{index}");
            golden(&name, scene);
            names.push(name);
        }
    }
    let stored = fs::read_dir(oracle().join("items")).map_or(0, Iterator::count);
    assert_eq!(
        stored,
        names.len(),
        "a golden without a scene is left in fixtures/oracle/items"
    );
}

/// The sheets, by file stem, each with its one scene.
fn scenes_in(folder: &str) -> Vec<(String, Scene)> {
    let mut sheets: Vec<_> = fs::read_dir(oracle().join(folder))
        .expect("sheets folder")
        .map(|entry| entry.expect("entry").path())
        .collect();
    sheets.sort();
    sheets
        .iter()
        .map(|path| {
            let stem = path
                .file_stem()
                .expect("a stem")
                .to_string_lossy()
                .into_owned();
            let file = format!(
                "{folder}/{}",
                path.file_name().expect("a name").to_string_lossy()
            );
            (stem, scenes(&file).remove(0))
        })
        .collect()
}
