//! What waits for the decoder when messages arrive faster than it reads.

use bytes::Bytes;
use foundation::CancelToken;
use scene_store::Feed;

use crate::live_sources::{
    bridge::{Doc, Waiting, sessions},
    overlay::{Arrival, Effects, Intake, QUEUE},
};

fn arrival(n: u8) -> Arrival {
    Arrival::Doc {
        epoch: 1,
        doc: Doc {
            bytes: Bytes::from(vec![n]),
            capture: None,
            topic: Some("topic".to_owned()),
            stamp: None,
        },
        time_us: Some(i64::from(n) * 1000),
    }
}

/// Opens the first input, reads document 1, pushes 2 and 3 meanwhile, and reads whatever the decoder gets next.
fn reads_after_a_burst(waiting: Waiting) -> Vec<Vec<u8>> {
    let (offers, mut feed) = sessions();
    let mut intake = Intake::new(offers, QUEUE, waiting);
    let cancel = CancelToken::new();
    let mut input = feed.next_input(&cancel).expect("the first session");
    let mut read = |count: usize| {
        (0..count)
            .map(|_| input.next(&cancel).unwrap().unwrap().bytes.to_vec())
            .collect::<Vec<_>>()
    };

    assert_eq!(intake.take(arrival(1), None), Effects::default());
    assert_eq!(read(1), [[1]]);
    // A replaced message is no loss of the lane: no gap, nothing behind.
    assert_eq!(intake.take(arrival(2), None), Effects::default());
    assert_eq!(intake.take(arrival(3), None), Effects::default());
    let expected = if waiting == Waiting::Newest { 1 } else { 2 };
    read(expected)
}

#[test]
fn mqtt_decodes_only_the_newest_waiting_message_and_datahub_every_one() {
    assert_eq!(reads_after_a_burst(Waiting::Newest), [[3]]);
    assert_eq!(reads_after_a_burst(Waiting::Queue), [[2], [3]]);
}
