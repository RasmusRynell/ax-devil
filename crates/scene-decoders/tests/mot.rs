//! MOT decoder behavior through the public registry.

use std::sync::LazyLock;

use foundation::{CancelToken, FrameSize, Interrupt};
use scene::{Classification, Coverage, Entity, Geometry, Scene, SceneTime, Score, Value};
use scene_decoders::{DecodeContext, DecodeOptions, DecodeSummary, file_decoder};

fn context(width: u32, height: u32) -> DecodeContext<'static> {
    static OPTIONS: LazyLock<DecodeOptions> = LazyLock::new(DecodeOptions::new);
    DecodeContext {
        options: &OPTIONS,
        frame_size: FrameSize { width, height },
    }
}

fn decode_with(text: &str, context: &DecodeContext<'_>) -> (Vec<Scene>, DecodeSummary) {
    let decoder = file_decoder("MOT_FILE").expect("MOT decoder is registered");
    let mut scenes = Vec::new();
    let summary = match decoder.decode_file(
        text.as_bytes(),
        context,
        &mut |scene| scenes.push(scene),
        &CancelToken::new(),
    ) {
        Ok(summary) => summary,
        Err(Interrupt::Failed(error)) => panic!("decode failed: {error}"),
        Err(Interrupt::Cancelled) => panic!("decode was cancelled"),
    };
    (scenes, summary)
}

fn decode(text: &str) -> (Vec<Scene>, DecodeSummary) {
    decode_with(text, &context(1920, 1080))
}

fn entity<'a>(scene: &'a Scene, id: &str) -> &'a Entity {
    scene
        .entities()
        .iter()
        .find(|entity| entity.id() == id)
        .unwrap_or_else(|| panic!("no entity {id}"))
}

fn first_class(entity: &Entity) -> &Classification {
    &entity.observations()[0].classifications[0]
}

fn attribute<'a>(class: &'a Classification, name: &str) -> Option<&'a Value> {
    class.attribute(name)
}

fn near(actual: f64, expected: f64) {
    assert!((actual - expected).abs() < 1e-9, "{actual} != {expected}");
}

#[test]
fn rows_are_grouped_into_ascending_frames_and_unsupported_rows_skipped() {
    let text = "2,-1,100,100,50,50,0.9\n\
                1,-1,100,100,50,50,0.9,-1,-1,-1\n\
                1,1,100,100,50,50,0,1,0.8\n\
                0,0,0,0,0,0\n\
                not,a,valid,row\n\
                  \n\
                a,b\n\
                c,d\n";
    let (scenes, summary) = decode(text);

    let shape: Vec<_> = scenes
        .iter()
        .map(|scene| (scene.time().start(), scene.entities().len()))
        .collect();
    assert_eq!(shape, [(SceneTime::Frame(0), 2), (SceneTime::Frame(1), 1)]);
    let [warning] = summary.warnings.as_slice() else {
        panic!("expected one warning, got {:?}", summary.warnings);
    };
    assert_eq!(warning.problem, "Skipped 4 of 7 rows");
    assert!(warning.more_reasons);
    let lines: Vec<_> = warning
        .reasons
        .iter()
        .map(|reason| reason.split(':').next().unwrap_or(""))
        .collect();
    assert_eq!(lines, ["line 4", "line 5", "line 7"]);
}

#[test]
fn rows_with_invalid_values_are_skipped() {
    let text = "0,1,0,0,10,10,1\n\
                1,1,0,0,-1,10,1\n\
                1,1,0,0,10,-1,1\n\
                1,1,x,0,10,10,1\n\
                1,1,0,0,10,10,nan\n\
                1,1,0,0,10,10,inf\n\
                1,1,1e999,0,10,10,1\n\
                1,1,0,0,10,10,1,1,0.5,-1,-1\n\
                1,1,0,0,10,10,1\n";
    let (scenes, summary) = decode(text);

    assert_eq!(scenes.len(), 1);
    assert_eq!(summary.warnings[0].problem, "Skipped 8 of 9 rows");
}

#[test]
fn ground_truth_boxes_are_normalized_by_the_host_frame_size() {
    let text = "1,1,0,0,64,48,1,1,0.8\n1,2,32,24,64,48,0,7,0.25\n";
    for width in [640, 1280] {
        let (scenes, summary) = decode_with(text, &context(width, 480));
        assert!(summary.warnings.is_empty());
        assert_eq!(summary.coverage, Coverage::Dense);
        let observation = &entity(&scenes[0], "2").observations()[0];
        let Geometry::Box(bounds) = &observation.geometry else {
            panic!("MOT boxes are boxes");
        };
        let (left, top, right, bottom) =
            (bounds.left(), bounds.top(), bounds.right(), bounds.bottom());
        let width = f64::from(width);
        near(left, 32.0 / width);
        near(top, 24.0 / 480.0);
        near(right, 96.0 / width);
        near(bottom, 72.0 / 480.0);
        assert_eq!(observation.time, SceneTime::Frame(0));
    }
}

#[test]
fn ground_truth_has_class_visibility_and_mark_but_no_score_or_confidence() {
    let (scenes, _) = decode("1,2,32,24,64,48,0,7,0.25\n");
    let class = first_class(entity(&scenes[0], "2"));

    assert_eq!(class.class_type, "static_person");
    assert_eq!((class.score, class.confidence), (None, None));
    assert_eq!(attribute(class, "gt_mark"), Some(&Value::Int(0)));
    assert_eq!(
        attribute(class, "visibility_ratio"),
        Some(&Value::Float(0.25))
    );
    assert_eq!(attribute(class, "source"), Some(&Value::Str("gt".into())));
}

#[test]
fn untracked_detections_are_separate_entities_and_scores_are_never_confidences() {
    let text = "1,-1,992.1,463.1,68.6,184.1,2.3092,-1,-1,-1\n\
                1,-1,767.4,462.1,45.4,125.1,-0.5,-1,-1,-1\n\
                1,-1,608.9,467.8,42.9,95,0.9\n";
    let (scenes, _) = decode(text);

    let entities = scenes[0].entities();
    assert_eq!(entities.len(), 3);
    let classes: Vec<_> = entities.iter().map(first_class).collect();
    assert_eq!(
        classes
            .iter()
            .map(|c| c.score.map(Score::value))
            .collect::<Vec<_>>(),
        [Some(2.3092), Some(-0.5), Some(0.9)]
    );
    assert!(
        classes
            .iter()
            .all(|c| c.confidence.is_none() && c.class_type == "person")
    );
    assert_eq!(entities[1].id(), "untracked:0:1");
}

#[test]
fn a_track_seen_twice_in_a_frame_is_one_entity() {
    let (scenes, _) = decode("3,5,0,0,10,10,0.5\n3,5,20,0,10,10,0.6\n3,6,0,0,10,10,0.5\n");

    assert_eq!(scenes[0].entities().len(), 2);
    assert_eq!(entity(&scenes[0], "5").observations().len(), 2);
}

#[test]
fn every_class_id_can_be_hidden_by_exactly_one_filter() {
    let (_, summary) = decode("");
    for class_id in (1..=13).chain([99]) {
        let (scenes, _) = decode(&format!("1,1,0,0,64,48,1,{class_id},1.0\n"));
        let names = [first_class(&scenes[0].entities()[0]).class_type.to_string()];
        let names: Vec<&str> = names.iter().map(String::as_str).collect();
        let hiding: Vec<_> = summary
            .filter
            .options
            .iter()
            .filter(|option| option.rule.matches(names.iter().copied()))
            .map(|option| option.id.to_string())
            .collect();
        assert_eq!(hiding.len(), 1, "class {class_id}: {hiding:?}");
    }
}

#[test]
fn a_cancelled_token_stops_the_decode() {
    let decoder = file_decoder("MOT_FILE").expect("MOT decoder is registered");
    let token = CancelToken::new();
    token.cancel();
    let text = "1,1,0,0,1,1,1\n".repeat(5000);
    let result = decoder.decode_file(text.as_bytes(), &context(1920, 1080), &mut |_| {}, &token);
    assert!(matches!(result, Err(Interrupt::Cancelled)));
}
