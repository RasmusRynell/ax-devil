//! Frame cost of rendering: boxes with a class, score and track id through the Standard and Tracking catalogs.
//!
//! Run with `scripts/cargo bench -p render-catalog`. The design target is under 4 ms for 100 boxes through Standard.

// A bench reports its numbers on stdout; the application lints do not apply to it.
#![allow(clippy::print_stdout)]

use std::hint::black_box;
use std::time::{Duration, Instant};

use drawing::DrawingInstructions;
use render_catalog::{CompiledCatalog, FeatureSet, RenderContext, compile};
use scene::{
    BoundingBox, ClassType, Classification, Entity, EntityId, Geometry, Observation, Scene,
    SceneTime, SceneView, Score, TimeSlice,
};

const CATALOGS: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../assets/catalogs");

/// A frame of `count` boxes spread over the image, each with a person class, a score and a numeric track id.
fn frame(count: usize) -> Scene {
    let entities = (0..count)
        .map(|index| {
            let (column, row) = ((index % 40) as f64, (index / 40) as f64);
            let (left, top) = (column * 0.024, 0.02 + row * 0.05);
            let bounds = BoundingBox::new(left, top, left + 0.02, top + 0.04).expect("a box");
            let mut observation = Observation::new(SceneTime::Frame(0), Geometry::Box(bounds));
            observation.classifications.push(Classification {
                score: Score::new(0.5 + (index % 50) as f64 / 100.0).ok(),
                ..Classification::new(ClassType::new("person").expect("a class"))
            });
            let mut entity = Entity::new(EntityId::from(index as i64 * 7 + 1));
            entity.push_observation(observation);
            entity
        })
        .collect();
    Scene::new(TimeSlice::at(SceneTime::Frame(0)))
        .with_entities(entities)
        .expect("unique ids")
}

fn catalog(name: &str) -> CompiledCatalog {
    let source =
        std::fs::read_to_string(format!("{CATALOGS}/{name}.json")).expect("a built-in catalog");
    compile(&source).expect("it compiles")
}

fn measure(catalog: &CompiledCatalog, scene: &Scene) -> (Duration, Duration, usize) {
    let view = SceneView::all(scene);
    let context = RenderContext {
        width: 1280.0,
        height: 720.0,
    };
    let run = || {
        let mut out = DrawingInstructions::default();
        catalog.render(
            &view,
            &context,
            FeatureSet::default(),
            &mut out,
            &mut |_| {},
        );
        black_box(out)
    };
    for _ in 0..20 {
        run();
    }
    let mut times: Vec<Duration> = (0..200)
        .map(|_| {
            let start = Instant::now();
            let out = run();
            let elapsed = start.elapsed();
            black_box(out);
            elapsed
        })
        .collect();
    times.sort();
    let rows = run().labels.len();
    (times[times.len() / 2], times[times.len() - 1], rows)
}

fn main() {
    for name in ["standard", "tracking"] {
        let compiled = catalog(name);
        for count in [100, 650] {
            let (median, worst, labels) = measure(&compiled, &frame(count));
            println!(
                "{name:<9} {count:>4} boxes: median {:>7.3} ms, worst {:>7.3} ms ({labels} labels)",
                median.as_secs_f64() * 1e3,
                worst.as_secs_f64() * 1e3
            );
        }
    }
}
