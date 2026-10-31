//! A decoder whose token is cancelled. The interruption of FFmpeg's own waiting is tested with the code that sets
//! it up, in `src/source.rs`.

#![cfg(unix)]

mod common;

use common::{fixture, index_of};
use foundation::CancelToken;

#[test]
fn a_cancelled_decoder_is_at_the_end_of_the_stream() {
    let path = fixture("cfr_h264_gop10_bf2.mp4");
    let index = index_of(&path);
    let cancel = CancelToken::new();
    let mut decoder = media::Decoder::open(&path, index.clone(), 2, cancel.clone()).unwrap();
    assert!(decoder.next_frame().unwrap().is_some());

    cancel.cancel();

    assert!(decoder.next_frame().unwrap().is_none());
    assert_eq!(decoder.position(), None);
    assert!(decoder.seek_to_keyframe(10).is_ok());
    assert_eq!(decoder.position(), None);
}
