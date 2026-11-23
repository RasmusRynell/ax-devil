//! Measure production Scene ingestion of the largest hostile mixed list fitting one plugin line.
// A measurement program reports its timings on stdout.
#![allow(clippy::print_stdout)]

use std::{fmt::Write as _, hint::black_box, time::Instant};

const LIMIT: usize = 16 << 20;

fn payload(count: usize) -> String {
    let mut text = String::from(
        r#"{"time":{"start":{"frame":0},"end":{"frame":0}},"entities":[{"id":"a","observations":["#,
    );
    for i in 0..count {
        if i != 0 {
            text.push(',');
        }
        // Every second arrival crosses all earlier dual-clock observations and frame-only sightings.
        let extra = if i % 2 == 0 { ",\"us\":0" } else { "" };
        write!(
            text,
            r#"{{"time":{{"frame":{}{extra}}},"geometry":{{"box":[0,0,1,1]}}}}"#,
            count - i
        )
        .expect("writing to a String cannot fail");
    }
    text.push_str("]}]}");
    text
}

fn main() {
    // The plugin wrapper and newline count toward the line budget; the parser receives its Scene value.
    let overhead = "{\"sample\":}\n".len();
    let (mut low, mut high) = (0, LIMIT / 40);
    while low + 1 < high {
        let mid = (low + high) / 2;
        if payload(mid).len() + overhead <= LIMIT {
            low = mid;
        } else {
            high = mid;
        }
    }
    let text = payload(low);
    assert!(text.len() + overhead <= LIMIT && payload(low + 1).len() + overhead > LIMIT);
    println!(
        "{low} observations, {} bytes including plugin wrapper/newline",
        text.len() + overhead
    );
    for run in 1..=3 {
        let start = Instant::now();
        let scene =
            scene::serial::scene_from_json_slice(black_box(text.as_bytes())).expect("valid input");
        let elapsed = start.elapsed();
        assert_eq!(scene.entities()[0].observations().len(), low);
        println!(
            "run {run}: JSON decode and Scene ordering {:.3} ms",
            elapsed.as_secs_f64() * 1000.0
        );
        black_box(scene);
    }
}
