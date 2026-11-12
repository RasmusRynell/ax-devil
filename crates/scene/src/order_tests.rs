use foundation::MediaTime;

use crate::{
    BoundingBox, Entity, EntityId, Geometry, Observation, ObservationTime, Scene, SceneTime,
    TimeSlice,
};

fn time(frame: Option<i64>, us: Option<i64>) -> ObservationTime {
    ObservationTime::new(frame, us.map(MediaTime)).unwrap()
}

// Literal reference procedure from Python Entity.add_observation, including its asymmetric clock choice.
fn reference(times: &[ObservationTime]) -> Vec<usize> {
    let mut order: Vec<usize> = Vec::new();
    for (i, incoming) in times.iter().enumerate() {
        let mut at = order.len();
        while at > 0 {
            let previous = times[order[at - 1]];
            let greater = if let Some(timestamp) = incoming.timestamp() {
                previous.timestamp().is_some_and(|value| value > timestamp)
            } else {
                previous
                    .frame()
                    .zip(incoming.frame())
                    .is_some_and(|(a, b)| a > b)
            };
            if !greater {
                break;
            }
            at -= 1;
        }
        order.insert(at, i);
    }
    order
}

fn production(times: &[ObservationTime]) -> Vec<usize> {
    let mut entity = Entity::new(EntityId::new("a").unwrap());
    for (i, &time) in times.iter().enumerate() {
        entity.push_observation(Observation::new(
            time,
            Geometry::Box(BoundingBox::new(i as f64, 0.0, i as f64 + 1.0, 1.0).unwrap()),
        ));
    }
    let scene = Scene::new(TimeSlice::at(SceneTime::Frame(0)))
        .with_entities(vec![entity])
        .unwrap();
    scene.entities()[0]
        .observations()
        .iter()
        .map(|o| o.geometry.bounds().left() as usize)
        .collect()
}

#[test]
fn a_frame_only_arrival_stays_after_a_dual_clock_observation_with_an_earlier_frame() {
    assert_eq!(
        production(&[time(Some(1), Some(100)), time(Some(2), None)]),
        [0, 1]
    );
}

#[test]
fn an_incomparable_arrival_stays_after_the_timestamp_observation() {
    assert_eq!(
        production(&[time(None, Some(100)), time(Some(3), None)]),
        [0, 1]
    );
}

#[test]
fn equal_frames_keep_arrival_order_even_when_the_previous_observation_has_a_timestamp() {
    assert_eq!(
        production(&[time(Some(2), Some(100)), time(Some(2), None)]),
        [0, 1]
    );
}

#[test]
fn observation_order_matches_python_for_random_clock_shapes_and_ties() {
    let mut seed = 0x8c3a_7f21_91bd_650e_u64;
    let mut next = || {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        seed
    };
    for _ in 0..10_000 {
        let length = (next() % 40) as usize;
        let times: Vec<_> = (0..length)
            .map(|_| {
                let shape = next() % 3;
                let frame = next() as i64 % 4;
                let us = next() as i64 % 4;
                time((shape != 1).then_some(frame), (shape != 0).then_some(us))
            })
            .collect();
        assert_eq!(production(&times), reference(&times), "{times:?}");
    }
}
