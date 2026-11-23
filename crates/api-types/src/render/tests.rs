use super::*;
use serde_json::json;

#[test]
fn pixels_messages_have_the_layout_the_page_writes() {
    let pixels = Pixels {
        seq: 7,
        output: Output::I420,
        width: 4,
        height: 2,
        stride0: 4,
        stride1: 2,
        data: Bytes::from_static(&[1; 12]),
    };
    let message = pixels_message(&pixels);
    assert_eq!(&message[0..4], b"AXPX");
    assert_eq!(&message[8..16], &7_u64.to_le_bytes());
    assert_eq!(&message[28..32], &2_u32.to_le_bytes());
    let parsed = parse_pixels(&message).unwrap();
    assert_eq!(
        (parsed.seq, parsed.output, parsed.stride1),
        (7, Output::I420, 2)
    );
    assert_eq!(parsed.data, pixels.data);
    assert!(parse_pixels(&message.slice(..31)).is_none());
    let mut other = message.to_vec();
    other[4] = 9;
    assert!(parse_pixels(&Bytes::from(other)).is_none());
}

#[test]
fn render_messages_use_the_names_the_page_parses() {
    let render = HostMessage::Render {
        seq: 3,
        cells: vec![4, -1, -2],
        kinds: vec![0, 0, 0],
    };
    assert_eq!(
        serde_json::to_value(&render).unwrap(),
        json!({"type": "Render", "seq": 3, "cells": [4, -1, -2], "kinds": [0, 0, 0]})
    );
    let hello = json!({"type": "Hello", "protocol": 1, "secret": "ab"});
    assert_eq!(
        serde_json::from_value::<PageMessage>(hello).unwrap(),
        PageMessage::Hello {
            protocol: RENDER_PROTOCOL,
            secret: "ab".into()
        }
    );
}
