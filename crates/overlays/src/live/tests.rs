//! A live lane over a real live track: events once, eviction after the kept frames, and an empty Scene that clears.

use std::{
    path::Path,
    sync::{Arc, LazyLock, mpsc},
    thread,
    time::{Duration, Instant},
};

use foundation::{CancelToken, FrameId, Interrupt, MediaTime};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Form, Framing, Input, OnEqualKey, Record, SceneSink,
    Settings, Spec, Values,
};
use overlay_sync::{LIVE_TOLERANCE, MatchPolicy, Tolerance};
use scene::{
    BoundingBox, Coverage, Entity, EntityId, Event, FilterPolicy, Geometry, MatchBasis,
    Observation, Operation, Scene, SceneTime, TimeSlice,
};
use scene_store::{Feed, LiveTrack, TrackRead};

use super::{LIVE_KEEP_FRAMES, LiveLane, LiveOut};
use crate::{LanePolicy, window::NONE_CELL};

static SPEC: LazyLock<Spec> = LazyLock::new(|| Spec {
    id: "LIVE_LANE".into(),
    plugin: "test".into(),
    name: "Live lane".into(),
    extensions: Vec::new(),
    framing: Framing::Whole,
    bases: vec![MatchBasis::Time],
    default_basis: MatchBasis::Time,
    options: Form::default(),
    version: 1,
});

/// What a record asks for: a Scene with a box, with a Delete event, or with nothing.
fn scene_of(line: &str) -> Scene {
    let mut parts = line.split(' ');
    let time = SceneTime::Timestamp(MediaTime(parts.next().unwrap().parse().unwrap()));
    let scene = Scene::new(TimeSlice::at(time));
    match parts.next() {
        Some("events") => scene
            .with_events(vec![Event::new(
                TimeSlice::at(time),
                Operation::Delete {
                    entity: EntityId::from(1u64),
                },
            )])
            .unwrap(),
        Some("empty") => scene,
        _ => {
            let mut entity = Entity::new(EntityId::from(1u64));
            entity.push_observation(Observation::new(
                time,
                Geometry::Box(BoundingBox::new(0.1, 0.1, 0.2, 0.2).unwrap()),
            ));
            scene.with_entities(vec![entity]).unwrap()
        }
    }
}

struct Lines(Vec<&'static str>, usize);

impl Input for Lines {
    fn path(&self) -> Option<&Path> {
        None
    }

    fn next(&mut self, _: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        self.1 += 1;
        Ok(self.0.get(self.1 - 1).map(|line| Record {
            bytes: line.as_bytes(),
            position: self.1 as u64,
            capture_time: None,
        }))
    }
}

struct Decode;

impl Decoder for Decode {
    fn spec(&self) -> &Spec {
        &SPEC
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        _: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        while let Some(record) = input.next(cancel)? {
            let line = std::str::from_utf8(record.bytes).unwrap();
            let _ = sink.sample(scene_of(line), OnEqualKey::Merge);
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: FilterPolicy {
                name: "",
                options: Vec::new(),
            },
            metadata: Vec::new(),
            coverage: Coverage::Sparse,
        })
    }
}

/// Gives the one session, then no more.
struct Once(Option<Lines>);

impl Feed for Once {
    fn next_input(&mut self, _: &CancelToken) -> Option<Box<dyn Input + Send>> {
        Some(Box::new(self.0.take()?))
    }
}

/// Gives its records, then what the test sends, and holds the session open until the track is dropped: the last
/// sample stays open.
struct Open(Lines, mpsc::Receiver<&'static str>);

impl Input for Open {
    fn path(&self) -> Option<&Path> {
        None
    }

    fn next(&mut self, cancel: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        if self.0.1 < self.0.0.len() {
            return self.0.next(cancel);
        }
        while !cancel.is_cancelled() {
            if let Ok(line) = self.1.recv_timeout(Duration::from_millis(2)) {
                self.0 = Lines(vec![line], 0);
                return self.0.next(cancel);
            }
        }
        Err(Interrupt::Cancelled)
    }
}

struct Held(Option<Open>);

impl Feed for Held {
    fn next_input(&mut self, _: &CancelToken) -> Option<Box<dyn Input + Send>> {
        Some(Box::new(self.0.take()?))
    }
}

/// A track holding the samples the lines make, all published.
fn track_of(lines: &[&'static str]) -> Arc<LiveTrack> {
    let values = SPEC.options.validate(&Settings::new()).unwrap();
    let feed = Once(Some(Lines(lines.to_vec(), 0)));
    let track = LiveTrack::start(Arc::new(Decode), values, Box::new(feed), 100).unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    while track.snapshot().keys.len() != lines.len() {
        assert!(Instant::now() < deadline, "the samples never arrived");
        thread::sleep(Duration::from_millis(2));
    }
    track
}

/// A lane that has seen none of the samples the track holds, as if it had attached before they arrived.
fn lane_over(track: &Arc<LiveTrack>) -> LiveLane {
    let mut lane = LiveLane::new(track.clone(), 0, 0, 1);
    lane.reported = None;
    lane
}

fn frame(n: u64, at: i64) -> FrameId {
    FrameId {
        sequence_id: n,
        timestamp: MediaTime(at),
    }
}

fn release(lane: &mut LiveLane, track: &LiveTrack, frame: FrameId) -> LiveOut {
    let policy = LanePolicy {
        subscribed: true,
        ..LanePolicy::default()
    };
    lane.release(frame, &track.snapshot(), &policy)
}

#[test]
fn a_sample_reports_its_events_once_the_first_time_it_is_shown() {
    let track = track_of(&["0 events", "100000", "200000 events", "300000"]);
    let mut lane = lane_over(&track);

    let first = release(&mut lane, &track, frame(0, 0));
    let reported = first.events.expect("sample 0 has events");
    assert_eq!(reported.samples.len(), 1);
    assert_eq!(reported.samples[0].time, MediaTime(0));
    assert_eq!(first.items.len(), 1);

    // The same sample again, retained or tolerated: nothing new, no resend.
    let again = release(&mut lane, &track, frame(1, 40_000));
    assert_eq!((again.events, again.items.len()), (None, 0));

    // Samples 1 and 2 fall between these frames; sample 2 is shown and sample 1 has none.
    let skipped = release(&mut lane, &track, frame(2, 200_000));
    let reported = skipped.events.expect("sample 2 has events");
    assert_eq!(reported.samples.len(), 1);
    assert_eq!(reported.samples[0].time, MediaTime(200_000));
    assert_eq!(skipped.map.samples, [2]);

    // Nothing is reported twice, even for a sample later shown again.
    assert_eq!(release(&mut lane, &track, frame(3, 210_000)).events, None);
}

#[test]
fn a_lane_that_attaches_late_reports_only_what_arrives_after() {
    let track = track_of(&["0 events", "100000 events"]);
    let mut lane = LiveLane::new(track.clone(), 0, 0, 1);
    // The retained samples were reported to the viewers that were there, or are history for this one.
    assert_eq!(release(&mut lane, &track, frame(0, 100_000)).events, None);
}

#[test]
fn a_sample_outside_the_tolerance_still_reports_its_events() {
    let track = track_of(&["0 events", "100000"]);
    let mut lane = lane_over(&track);

    // The next frame comes long after both samples: nothing is drawn, yet the event was never reported.
    let late = release(&mut lane, &track, frame(0, 5_000_000));
    assert_eq!(late.map.samples, [NONE_CELL]);
    let reported = late.events.expect("sample 0 has events");
    assert_eq!(reported.samples[0].time, MediaTime(0));
    assert_eq!(release(&mut lane, &track, frame(1, 5_040_000)).events, None);
}

#[test]
fn a_sample_is_evicted_when_none_of_the_last_frames_refers_to_it() {
    let track = track_of(&["0", "100000"]);
    let mut lane = lane_over(&track);
    assert_eq!(release(&mut lane, &track, frame(0, 0)).map.samples, [0]);

    let mut evictions = Vec::new();
    for n in 0..LIVE_KEEP_FRAMES as u64 {
        let out = release(&mut lane, &track, frame(n + 1, 100_000 + n as i64 * 1_000));
        assert_eq!(out.map.samples, [1]);
        evictions.push(out.evict);
    }
    // Frame 0 still counts for sample 0 until the keep window has moved past it.
    let (last, before) = evictions.split_last().unwrap();
    assert!(before.iter().all(Vec::is_empty), "{before:?}");
    assert_eq!(last, &[0]);
    assert!(
        release(&mut lane, &track, frame(99, 110_000))
            .evict
            .is_empty()
    );
}

#[test]
fn an_empty_scene_clears_what_an_earlier_sample_drew() {
    let track = track_of(&["0", "100000 empty"]);
    let mut lane = lane_over(&track);
    assert_eq!(release(&mut lane, &track, frame(0, 0)).map.samples, [0]);

    // Before the empty sample's time the future sample never shows early.
    let early = release(&mut lane, &track, frame(1, 99_000));
    assert_eq!(early.map.samples, [0]);

    // Sticky would keep sample 0 here if the empty sample were ignored.
    let out = release(&mut lane, &track, frame(2, 105_000));
    assert_eq!(out.map.samples, [1]);
    assert_eq!(out.items.len(), 1);
    assert_eq!(out.items[0].sample, 1);
}

#[test]
fn sticky_keeps_only_a_sample_that_matched_a_frame() {
    let track = track_of(&["1000000", "1075000", "1100000"]);
    let mut lane = lane_over(&track);
    let policy = LanePolicy {
        matching: MatchPolicy {
            tolerance: Tolerance::PreviousWithin(LIVE_TOLERANCE),
            ..MatchPolicy::default()
        },
        subscribed: true,
    };
    let mut cell = |n: u64, at: i64| {
        let out = lane.release(frame(n, at), &track.snapshot(), &policy);
        (out.map.samples[0], out.map.kinds[0])
    };

    // The first frame comes 40 ms after the only sample at that time: it never matched one, so it is stale.
    assert_eq!(cell(0, 1_040_000), (NONE_CELL, 0));
    // Sample 1 is 5 ms back and admitted.
    assert_eq!(cell(1, 1_080_000), (1, 1));
    // Sample 2 misses the tolerance, so it is discarded and sample 1 stays.
    assert_eq!(cell(2, 1_200_000), (1, 2));
    // The sticky timeout counts from the admitted sample.
    assert_eq!(cell(3, 3_200_000), (NONE_CELL, 0));
}

#[test]
fn events_that_join_a_sample_after_a_deadline_frame_showed_it_open_are_reported() {
    let (more, later) = mpsc::channel();
    let feed = Held(Some(Open(Lines(vec!["0", "100000 events"], 0), later)));
    let values = SPEC.options.validate(&Settings::new()).unwrap();
    let track = LiveTrack::start(Arc::new(Decode), values, Box::new(feed), 100).unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    while track.snapshot().keys.len() != 1 {
        assert!(Instant::now() < deadline, "the first sample never arrived");
        thread::sleep(Duration::from_millis(2));
    }
    let mut lane = lane_over(&track);
    let policy = LanePolicy {
        subscribed: true,
        ..LanePolicy::default()
    };

    // The frame cannot wait for the sample at 100 ms to close, and reports the event it has.
    let first = lane.release(frame(0, 100_000), &track.snapshot_with_open(), &policy);
    assert_eq!(
        first
            .events
            .expect("the open sample has an event")
            .samples
            .len(),
        1
    );

    // More joins it: the track is numbered anew, and the new event is reported, only it.
    more.send("100000 events").unwrap();
    while track.snapshot().generation != 1 {
        assert!(Instant::now() < deadline, "the second record never arrived");
        thread::sleep(Duration::from_millis(2));
    }
    let second = lane.release(frame(1, 140_000), &track.snapshot_with_open(), &policy);
    let reported = second.events.expect("the joined event is reported").samples;
    assert_eq!((reported.len(), reported[0].events.len()), (1, 1));
    assert_eq!(reported[0].time, MediaTime(100_000));
    assert_eq!(
        lane.release(frame(2, 180_000), &track.snapshot_with_open(), &policy)
            .events,
        None
    );
}
