//! The lane file is the plugin wire: its bytes are part of a derived lane's identity.

use crate::write_lane;

use super::*;

#[test]
fn a_lane_file_is_the_header_and_one_sample_line_per_scene() {
    let fixture = Fixture::new(Shape::Frames {
        frames: 2,
        boxes: 1,
    });
    let track = fixture.open();
    let mut bytes = Vec::new();
    write_lane(&*track, None, u64::MAX, &mut bytes, &CancelToken::new()).expect("written");

    let sample = |frame: u32| {
        format!(
            r#"{{"sample":{{"time":{{"start":{{"frame":{frame}}},"end":{{"frame":{frame}}}}},"entities":[{{"id":"0","observations":[{{"time":{{"frame":{frame}}},"geometry":{{"box":[0.0,0.25,0.125,0.5]}},"classifications":[{{"type":"person","score":null,"confidence":null,"attributes":{{"class_id":1,"source":"test"}}}}]}}],"end_reason":null,"motion_state":null}}],"events":[],"relations":[],"debug":{{}}}}}}"#
        )
    };
    let expected = [
        r#"{"lane":{"basis":"frame","coverage":"dense","metadata":{"rows":3}}}"#.to_owned(),
        sample(0),
        sample(1),
    ]
    .join("\n");
    assert_eq!(
        String::from_utf8(bytes).expect("text"),
        format!("{expected}\n")
    );
}
