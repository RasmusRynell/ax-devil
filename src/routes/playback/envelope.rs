//! The binary envelopes of the playback socket. The layout is documented with the constants in
//! `api_types::playback`, and `frontend/src/playback/protocol.ts` parses it.

use api_types::playback::{
    ENVELOPE_HEAD_BYTES, ENVELOPE_MAGIC, ENVELOPE_VERSION, FLAG_KEYFRAME, FLAG_LAST_FRAME,
    KIND_TIMELINE, KIND_VIDEO_FRAME, LAYOUT_I420, LAYOUT_NV12, LAYOUT_RGBA, MATRIX_BT601,
    MATRIX_BT709, VIDEO_FRAME_HEADER_BYTES,
};
use bytes::{BufMut, Bytes, BytesMut};
use foundation::{FrameId, Matrix, MediaTime, PixelFormat, Timeline, VideoFrame};

/// The fixed head every envelope starts with. Lane 0 is the only lane of a video.
fn head(out: &mut BytesMut, kind: u8, payload_at: u16, id: FrameId, flags: u32) {
    out.put_u32_le(ENVELOPE_MAGIC);
    out.put_u8(ENVELOPE_VERSION);
    out.put_u8(kind);
    out.put_u16_le(payload_at);
    out.put_u32_le(0);
    out.put_u64_le(id.sequence_id);
    out.put_i64_le(id.timestamp.as_us());
    out.put_u32_le(flags);
}

/// The Timeline envelope: the time of every frame in microseconds, as `f64`, by sequence id. Every time below
/// 2^53 is exact, so the receiver reads the values without conversion.
pub(super) fn timeline(timeline: &Timeline) -> Bytes {
    let times = timeline.as_slice();
    let mut out = BytesMut::with_capacity(usize::from(ENVELOPE_HEAD_BYTES) + times.len() * 8);
    let id = FrameId {
        sequence_id: 0,
        timestamp: MediaTime::ZERO,
    };
    head(&mut out, KIND_TIMELINE, ENVELOPE_HEAD_BYTES, id, 0);
    for time in times {
        out.put_f64_le(time.as_us() as f64);
    }
    out.freeze()
}

/// The VideoFrame envelope. The planes are copied once, from the frame's data into the message; `last` marks the
/// final frame of the video.
pub(super) fn video_frame(frame: &VideoFrame, last: bool) -> Bytes {
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

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::{ColorDesc, FrameLayout, Plane};

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
            &[68, 0],                              // header_len: where the planes start
            &[0, 0, 0, 0],                         // lane
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
            &[0; 4],  // lane
            &[0; 8],  // sequence id
            &[0; 8],  // timestamp
            &[0; 4],  // flags
            &0f64.to_le_bytes(),
            &33_333f64.to_le_bytes(),
            &66_667f64.to_le_bytes(),
        ]
        .concat();
        assert_eq!(message.as_ref(), expected);
    }
}
