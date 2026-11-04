//! A live track over a feed of two sessions.

use std::{
    collections::VecDeque,
    path::Path,
    sync::{Arc, Mutex, Weak, mpsc},
    time::{Duration, Instant},
};

use foundation::{CancelToken, Interrupt, MediaTime};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Input, OnEqualKey, Record, SceneSink, Settings, Spec,
    Values,
};
use scene::{Coverage, Fields, FilterPolicy, SceneTime};

use super::{SPEC, entities};
use crate::{Decided, Feed, LiveTrack, TrackRead, TrackSnapshot};

/// Gives each record as the timestamp, in microseconds, of a one-entity Scene.
struct Stamps;

impl Decoder for Stamps {
    fn spec(&self) -> &Spec {
        &SPEC
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        while let Some(record) = input.next(cancel)? {
            let text = std::str::from_utf8(record.bytes).expect("text");
            let time = SceneTime::Timestamp(MediaTime(text.parse().expect("microseconds")));
            let _ = sink.sample(entities(time, 1), OnEqualKey::Merge);
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter: FilterPolicy {
                name: "",
                options: Vec::new(),
            },
            metadata: Fields::new(),
            coverage: Coverage::Sparse,
        })
    }
}

struct Lines {
    lines: Vec<&'static str>,
    next: usize,
}

impl Input for Lines {
    fn path(&self) -> Option<&Path> {
        None
    }

    fn next(
        &mut self,
        _cancel: &CancelToken,
    ) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        self.next += 1;
        Ok(self.lines.get(self.next - 1).map(|line| Record {
            bytes: line.as_bytes(),
            position: self.next as u64,
            capture_time: None,
        }))
    }
}

/// Offers each session when the test says so, then no more once the test lets go.
struct Sessions {
    go: mpsc::Receiver<()>,
    inputs: VecDeque<Vec<&'static str>>,
}

impl Feed for Sessions {
    fn next_input(&mut self, _cancel: &CancelToken) -> Option<Box<dyn Input + Send>> {
        self.go.recv().ok()?;
        let lines = self.inputs.pop_front()?;
        Some(Box::new(Lines { lines, next: 0 }))
    }
}

fn times(track: &LiveTrack, snapshot: &TrackSnapshot) -> Vec<i64> {
    (0..snapshot.keys.len())
        .map(|index| {
            match track
                .scene_in(snapshot, index)
                .expect("held")
                .time()
                .start()
            {
                SceneTime::Timestamp(time) => time.0,
                SceneTime::Frame(_) => panic!("a live sample is keyed by time"),
            }
        })
        .collect()
}

#[test]
fn a_live_track_keeps_samples_in_time_order_and_drops_its_oldest_half_at_once() {
    let (go, wait) = mpsc::channel();
    let feed = Sessions {
        go: wait,
        inputs: VecDeque::from([
            vec!["10", "20", "20", "15", "30", "40", "50", "60"],
            vec!["5", "70"],
        ]),
    };
    let values = SPEC.options.validate(&Settings::new()).expect("no options");
    let track = LiveTrack::start(Arc::new(Stamps), values, Box::new(feed), 4).expect("starts");
    // Every publication, in order; the listener runs on the track's thread.
    let seen: Arc<Mutex<Vec<Arc<TrackSnapshot>>>> = Arc::default();
    let listener: Arc<dyn Fn() + Send + Sync> = {
        let (seen, track) = (seen.clone(), Arc::downgrade(&track));
        Arc::new(move || {
            if let Some(track) = Weak::upgrade(&track) {
                seen.lock().expect("seen").push(track.snapshot());
            }
        })
    };
    track.on_change(Arc::downgrade(&listener));
    go.send(()).expect("the feed waits");
    go.send(()).expect("the feed waits");
    drop(go);

    let deadline = Instant::now() + Duration::from_secs(10);
    while track.snapshot().keys.len() != 4
        || track.snapshot().decided
            != (Decided::Below {
                frame: None,
                time: Some(MediaTime(70)),
            })
    {
        assert!(
            Instant::now() < deadline,
            "the second session never arrived"
        );
        std::thread::sleep(Duration::from_millis(2));
    }

    // The sample open when a session ended was published then; the key repeated in the first was merged, and the two
    // that came after a later one were dropped and counted, the second session's restart among them.
    let now = track.snapshot();
    assert_eq!(times(&track, &now), [40, 50, 60, 70]);
    assert!(
        now.warnings
            .iter()
            .any(|warning| warning.problem.starts_with("Dropped 2 live samples")),
        "{:?}",
        now.warnings
    );
    assert!(track.item_in(&now, 0).is_ok());

    // The oldest half went in one step: one renumbering, from four samples to two.
    let seen = seen.lock().expect("seen");
    let renumbered: Vec<_> = seen
        .windows(2)
        .filter(|pair| pair[0].generation != pair[1].generation)
        .map(|pair| (pair[0].keys.len(), pair[1].keys.len()))
        .collect();
    assert_eq!(renumbered, [(4, 2)]);
    // A snapshot of the earlier numbering still reads its own samples.
    let earlier = seen
        .iter()
        .find(|snapshot| snapshot.keys.len() == 4)
        .expect("four samples were published");
    assert_eq!(times(&track, earlier), [10, 20, 30, 40]);
}

/// Gives its records, then what the test sends, and holds the session open until the track is dropped.
struct Open(Lines, mpsc::Receiver<&'static str>);

impl Input for Open {
    fn path(&self) -> Option<&Path> {
        None
    }

    fn next(&mut self, cancel: &CancelToken) -> Result<Option<Record<'_>>, Interrupt<DecodeError>> {
        if self.0.next < self.0.lines.len() {
            return self.0.next(cancel);
        }
        while !cancel.is_cancelled() {
            if let Ok(line) = self.1.recv_timeout(Duration::from_millis(2)) {
                self.0.lines = vec![line];
                self.0.next = 0;
                return self.0.next(cancel);
            }
        }
        Err(Interrupt::Cancelled)
    }
}

struct Held(Option<Open>);

impl Feed for Held {
    fn next_input(&mut self, _cancel: &CancelToken) -> Option<Box<dyn Input + Send>> {
        Some(Box::new(self.0.take()?))
    }
}

#[test]
fn the_open_sample_is_readable_and_renumbers_the_track_when_more_joins_it_afterwards() {
    let (more, later) = mpsc::channel();
    let lines = Lines {
        lines: vec!["10", "20"],
        next: 0,
    };
    let feed = Held(Some(Open(lines, later)));
    let values = SPEC.options.validate(&Settings::new()).expect("no options");
    let track = LiveTrack::start(Arc::new(Stamps), values, Box::new(feed), 4).expect("starts");
    let wait_for = |generation: u32, keys: usize| {
        let deadline = Instant::now() + Duration::from_secs(10);
        while (track.snapshot().generation, track.snapshot().keys.len()) != (generation, keys)
            || track.snapshot().decided
                != (Decided::Below {
                    frame: None,
                    time: Some(MediaTime(20)),
                })
        {
            assert!(Instant::now() < deadline, "the samples never arrived");
            std::thread::sleep(Duration::from_millis(2));
        }
    };
    wait_for(0, 1);

    // Sample 20 is open: published readers lack it, a deadline reader has it after the published one.
    let published = track.snapshot();
    assert_eq!(times(&track, &published), [10]);
    let with_open = track.snapshot_with_open();
    assert_eq!(times(&track, &with_open), [10, 20]);
    assert_eq!(with_open.generation, published.generation);
    assert!(track.item_in(&with_open, 1).is_ok());

    // A Scene joining the sample after a reader took it renumbers the track; the reader's snapshot keeps its own.
    more.send("20").expect("the session waits");
    wait_for(1, 1);

    // Nobody took it since, so a second join leaves the numbering alone.
    more.send("20").expect("the session waits");
    std::thread::sleep(Duration::from_millis(50));
    assert_eq!(track.snapshot().generation, 1);
    let renumbered = track.snapshot_with_open();
    assert_eq!(times(&track, &renumbered), [10, 20]);
    assert_eq!(times(&track, &with_open), [10, 20]);
    let joined = track.scene_in(&renumbered, 1).expect("held");
    assert_eq!(joined.entities()[0].observations().len(), 3);
    assert_eq!(
        track.scene_in(&with_open, 1).expect("held").entities()[0]
            .observations()
            .len(),
        1
    );
}
