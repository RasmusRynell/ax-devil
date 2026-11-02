//! Encoders of the binary envelopes of the playback socket. The layouts are documented with the constants in
//! [`crate::playback`], and `frontend/src/playback/protocol.ts` parses them.

use super::{
    ENVELOPE_MAGIC, ENVELOPE_VERSION, FLAG_KEYFRAME, FLAG_LAST_FRAME, FRAME_MAP_HEADER_BYTES,
    KIND_FRAME_MAP, KIND_SAMPLE, KIND_TIMELINE, KIND_VIDEO_FRAME, LAYOUT_I420, LAYOUT_NV12,
    LAYOUT_RGBA, MATRIX_BT601, MATRIX_BT709, SAMPLE_HEADER_BYTES, TIMELINE_HEADER_BYTES,
    VIDEO_FRAME_HEADER_BYTES,
};
use bytes::{BufMut, Bytes, BytesMut};
use foundation::{FrameId, Matrix, MediaTime, PixelFormat, Timeline, VideoFrame};
use overlays::{MapRun, SampleItem};

/// The fixed head every envelope starts with.
fn head(out: &mut BytesMut, kind: u8, payload_at: u16, id: FrameId, flags: u32) {
    out.put_u32_le(ENVELOPE_MAGIC);
    out.put_u8(ENVELOPE_VERSION);
    out.put_u8(kind);
    out.put_u16_le(payload_at);
    out.put_u64_le(id.sequence_id);
    out.put_i64_le(id.timestamp.as_us());
    out.put_u32_le(flags);
}

/// The Timeline envelope: the time of every frame in microseconds, as `f64`, by sequence id. Every time below
/// 2^53 is exact, so the receiver reads the values without conversion.
pub fn timeline(timeline: &Timeline) -> Bytes {
    let times = timeline.as_slice();
    let mut out = BytesMut::with_capacity(usize::from(TIMELINE_HEADER_BYTES) + times.len() * 8);
    let id = FrameId {
        sequence_id: 0,
        timestamp: MediaTime::ZERO,
    };
    head(&mut out, KIND_TIMELINE, TIMELINE_HEADER_BYTES, id, 0);
    out.put_u32_le(0);
    for time in times {
        out.put_f64_le(time.as_us() as f64);
    }
    out.freeze()
}

/// The VideoFrame envelope. The planes are copied once, from the frame's data into the message; `last` marks the
/// final frame of the video.
pub fn video_frame(frame: &VideoFrame, last: bool) -> Bytes {
    let layout = frame.layout();
    let format = match layout.format {
        PixelFormat::I420 => LAYOUT_I420,
        PixelFormat::Nv12 => LAYOUT_NV12,
        PixelFormat::Rgba => LAYOUT_RGBA,
    };
    let matrix = match layout.color.matrix {
        Matrix::Bt601 => MATRIX_BT601,
        Matrix::Bt709 => MATRIX_BT709,
    };
    let flags = if frame.is_keyframe() {
        FLAG_KEYFRAME
    } else {
        0
    } | if last { FLAG_LAST_FRAME } else { 0 };
    let mut out =
        BytesMut::with_capacity(usize::from(VIDEO_FRAME_HEADER_BYTES) + frame.data().len());
    head(
        &mut out,
        KIND_VIDEO_FRAME,
        VIDEO_FRAME_HEADER_BYTES,
        frame.id(),
        flags,
    );
    out.put_u32_le(layout.width);
    out.put_u32_le(layout.height);
    out.put_u8(format);
    out.put_u8(matrix);
    out.put_u8(u8::from(layout.color.full_range));
    out.put_u8(0);
    for plane in &layout.planes {
        out.put_u32_le(plane.stride);
    }
    for plane in &layout.planes {
        out.put_u32_le(plane.len);
    }
    out.extend_from_slice(frame.data());
    out.freeze()
}

/// The head of an item: `sequence_id` is its first frame or sample, and the time is reserved.
fn item_head(out: &mut BytesMut, kind: u8, header_len: u16, sequence_id: u64) {
    let id = FrameId {
        sequence_id,
        timestamp: MediaTime::ZERO,
    };
    head(out, kind, header_len, id, 0);
}

/// The Sample envelope of `sample`. The item starts at a multiple of 8, so the receiver views its columns in place.
pub fn sample(sample: &SampleItem) -> Bytes {
    debug_assert_eq!(
        sample.item.len() % 8,
        0,
        "an item is a whole number of words"
    );
    let mut out = BytesMut::with_capacity(usize::from(SAMPLE_HEADER_BYTES) + sample.item.len());
    item_head(&mut out, KIND_SAMPLE, SAMPLE_HEADER_BYTES, sample.sample);
    out.put_u16_le(sample.source);
    out.put_u16_le(0);
    out.put_u32_le(sample.data_revision);
    out.put_u32_le(0);
    out.extend_from_slice(&sample.item);
    out.freeze()
}

/// The FrameMap envelope of `run`.
pub fn frame_map(run: &MapRun) -> Bytes {
    debug_assert_eq!(run.samples.len(), run.kinds.len());
    let count = run.samples.len();
    let mut out = BytesMut::with_capacity(usize::from(FRAME_MAP_HEADER_BYTES) + count * 5);
    item_head(&mut out, KIND_FRAME_MAP, FRAME_MAP_HEADER_BYTES, run.first);
    out.put_u16_le(run.lane);
    out.put_u16_le(0);
    out.put_u32_le(run.policy_revision);
    out.put_u32_le(run.data_revision);
    out.put_u32_le(u32::try_from(count).unwrap_or(u32::MAX));
    out.put_u32_le(0);
    for sample in &run.samples {
        out.put_u32_le(*sample);
    }
    out.extend_from_slice(&run.kinds);
    out.freeze()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::playback::{FRAME_MAP_EMPTY, FRAME_MAP_NONE};
    use foundation::{ColorDesc, FrameLayout, Plane};
    use std::{fs, path::Path};

    /// A 2x2 I420 frame: Y plane 4 bytes with stride 2, then U and V of one byte each.
    fn frame() -> VideoFrame {
        let plane = |stride, len| Plane { stride, len };
        let layout = FrameLayout {
            width: 2,
            height: 2,
            format: PixelFormat::I420,
            color: ColorDesc {
                matrix: Matrix::Bt709,
                full_range: false,
            },
            planes: [plane(2, 4), plane(1, 1), plane(1, 1)],
        };
        let id = FrameId {
            sequence_id: 5,
            timestamp: MediaTime::from_us(1_234_567),
        };
        VideoFrame::new(id, true, layout, Bytes::from_static(&[1, 2, 3, 4, 5, 6])).unwrap()
    }

    #[test]
    fn a_video_frame_is_the_documented_head_the_video_header_and_the_planes() {
        let message = video_frame(&frame(), true);

        let expected: Vec<u8> = [
            &b"AXDV"[..],
            &[1, 1],                               // version, kind VideoFrame
            &[64, 0],                              // header_len: where the planes start
            &[5, 0, 0, 0, 0, 0, 0, 0],             // sequence id
            &[0x87, 0xD6, 0x12, 0, 0, 0, 0, 0],    // timestamp 1_234_567
            &[3, 0, 0, 0],                         // flags: keyframe and last frame
            &[2, 0, 0, 0, 2, 0, 0, 0],             // width, height
            &[1, 1, 0, 0],                         // I420, BT.709, limited range, pad
            &[2, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0], // strides
            &[4, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0], // plane lengths
            &[1, 2, 3, 4, 5, 6],                   // planes
        ]
        .concat();
        assert_eq!(message.as_ref(), expected);
    }

    #[test]
    fn a_timeline_is_the_head_and_every_time_as_a_float() {
        let times = [0, 33_333, 66_667].map(MediaTime::from_us);
        let message = timeline(&Timeline::new(times.to_vec()).unwrap());

        let expected: Vec<u8> = [
            &b"AXDV"[..],
            &[1, 3],  // version, kind Timeline
            &[32, 0], // header_len
            &[0; 8],  // sequence id
            &[0; 8],  // timestamp
            &[0; 4],  // flags
            &[0; 4],  // pad: the times start 8-byte aligned
            &0f64.to_le_bytes(),
            &33_333f64.to_le_bytes(),
            &66_667f64.to_le_bytes(),
        ]
        .concat();
        assert_eq!(message.as_ref(), expected);
    }

    fn golden(name: &str, bytes: &[u8]) {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../fixtures/wire")
            .join(name);
        if std::env::var_os("UPDATE_GOLDENS").is_some() {
            fs::create_dir_all(path.parent().expect("a folder")).expect("wire folder");
            fs::write(&path, bytes).expect("writes the golden");
            return;
        }
        let expected = fs::read(&path).unwrap_or_default();
        assert!(
            expected == bytes,
            "{name} is missing or stale; regenerate with UPDATE_GOLDENS=1 make tests-backend TEST=envelope_goldens"
        );
    }

    #[test]
    fn envelope_goldens_are_stable() {
        let item = fs::read(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/oracle/items/classes.bin"),
        )
        .expect("the oracle item");
        let sample = sample(&SampleItem {
            source: 1,
            data_revision: 7,
            sample: 42,
            frame: 0,
            serial: 0,
            item: item.into(),
        });
        golden("sample.bin", &sample);
        assert_eq!(sample.len() % 8, 0);

        let map = frame_map(&MapRun {
            lane: 2,
            policy_revision: 3,
            data_revision: 7,
            first: 256,
            samples: vec![0, 0, FRAME_MAP_NONE, FRAME_MAP_EMPTY, 9],
            kinds: vec![0, 1, 0, 0, 2],
            frame: 256,
        });
        golden("frame-map.bin", &map);
        assert_eq!(map.len(), 48 + 5 * 5);
    }
}
