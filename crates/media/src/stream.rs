//! Live video: Annex B access units of one H.264 or H.265 stream to transport-ready frames.
//!
//! Unlike the offline [`Decoder`](crate::Decoder) there is no index: the caller names every unit's [`FrameId`] and
//! gets a frame with that id back. The codec runs in low-delay mode, so a unit yields its frame at once, and a
//! picture that cannot be trusted is never shown: after damage nothing is delivered until the next keyframe.

use crate::convert::Converter;
use ffmpeg_next::codec::{self, Flags, threading};
use ffmpeg_next::decoder;
use ffmpeg_next::error::EAGAIN;
use ffmpeg_next::frame::Video;
use ffmpeg_next::util::log::{self, Level};
use ffmpeg_next::{Error, Packet};
use foundation::{ErrorCode, FrameFailure, FrameId, UserError, VideoFrame};
use std::collections::VecDeque;
use tracing::debug;

/// Units sent without a frame back that are remembered; a stream that never delivers does not grow this.
const MAX_PENDING: usize = 32;

/// The video codec of a live stream.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StreamCodec {
    /// H.264 / AVC.
    H264,
    /// H.265 / HEVC.
    H265,
}

impl StreamCodec {
    fn id(self) -> codec::Id {
        match self {
            Self::H264 => codec::Id::H264,
            Self::H265 => codec::Id::HEVC,
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::H264 => "H.264",
            Self::H265 => "H.265",
        }
    }

    /// The type field of the NAL unit whose first byte is `header`.
    fn nal_type(self, header: u8) -> u8 {
        match self {
            Self::H264 => header & 0x1f,
            Self::H265 => (header >> 1) & 0x3f,
        }
    }

    /// Whether NAL units of `nal_type` hold parameter sets (H.264 SPS, PPS; H.265 VPS, SPS, PPS).
    fn is_parameter_set(self, nal_type: u8) -> bool {
        match self {
            Self::H264 => matches!(nal_type, 7 | 8),
            Self::H265 => matches!(nal_type, 32..=34),
        }
    }

    /// Whether NAL units of `nal_type` hold a picture decoding can start at (H.264 IDR; H.265 BLA, IDR, CRA).
    fn is_random_access(self, nal_type: u8) -> bool {
        match self {
            Self::H264 => nal_type == 5,
            Self::H265 => matches!(nal_type, 16..=21),
        }
    }
}

/// What an access unit says about itself before it is decoded.
struct Unit {
    keyframe: bool,
    /// The unit's parameter sets in order, without start codes.
    parameter_sets: Vec<Vec<u8>>,
}

impl Unit {
    fn parse(codec: StreamCodec, data: &[u8]) -> Self {
        let mut unit = Unit {
            keyframe: false,
            parameter_sets: Vec::new(),
        };
        for nal in nal_units(data) {
            let Some(&header) = nal.first() else { continue };
            let nal_type = codec.nal_type(header);
            unit.keyframe |= codec.is_random_access(nal_type);
            if codec.is_parameter_set(nal_type) {
                unit.parameter_sets.push(nal.to_vec());
            }
        }
        unit
    }
}

/// `nal` without the zero bytes at its end, which belong to the next unit's four-byte start code.
fn trim_zeros(nal: &[u8]) -> &[u8] {
    let kept = nal
        .iter()
        .rposition(|&byte| byte != 0)
        .map_or(0, |at| at + 1);
    &nal[..kept]
}

/// The NAL units of Annex B `data`, without start codes or the zero bytes that precede the next start code.
fn nal_units(data: &[u8]) -> Vec<&[u8]> {
    let starts: Vec<usize> = data
        .windows(3)
        .enumerate()
        .filter(|(_, window)| *window == [0, 0, 1])
        .map(|(at, _)| at)
        .collect();
    starts
        .iter()
        .enumerate()
        .map(|(n, &start)| {
            let end = starts.get(n + 1).copied().unwrap_or(data.len());
            trim_zeros(&data[start + 3..end])
        })
        .collect()
}

/// Annex B data of `nals`, each behind a four-byte start code.
fn annex_b<'a>(nals: impl IntoIterator<Item = &'a [u8]>) -> Vec<u8> {
    let mut data = Vec::new();
    for nal in nals {
        data.extend_from_slice(&[0, 0, 0, 1]);
        data.extend_from_slice(nal);
    }
    data
}

fn open_codec(codec: StreamCodec) -> Result<decoder::Video, UserError> {
    let unsupported = |reason: String| UserError::new(ErrorCode::UnsupportedFormat, reason);
    let found = decoder::find(codec.id())
        .ok_or_else(|| unsupported(format!("this FFmpeg has no {} decoder", codec.name())))?;
    let mut context = codec::context::Context::new_with_codec(found);
    // Low delay: no frames held back for reordering, and slice threads, which wait for no later unit.
    context.set_flags(Flags::LOW_DELAY);
    context.set_threading(threading::Config {
        kind: threading::Type::Slice,
        count: 0,
    });
    context.decoder().video().map_err(|error| {
        unsupported(format!(
            "the {} decoder could not be opened: {error}",
            codec.name()
        ))
    })
}

/// A codec for one live stream. `Send` but not `Sync`: it belongs to exactly one worker.
///
/// Units must arrive in decode order, which with the low-delay codec is also presentation order. Until the first
/// keyframe, and again after damage, units are skipped.
pub struct StreamDecoder {
    codec: StreamCodec,
    video: decoder::Video,
    converter: Converter,
    /// The stream's parameter sets; a keyframe that carries other ones reopens the codec. A keyframe that carries
    /// none gets these in front of it: parameter sets alone are not a picture, so the codec only takes them with one.
    parameter_sets: Vec<Vec<u8>>,
    /// Waiting for a keyframe: at the start, and after damage.
    awaiting_keyframe: bool,
    /// Ids of units sent whose frames did not come back yet, oldest first. Frames are matched by sequence id.
    pending: VecDeque<FrameId>,
}

impl StreamDecoder {
    /// Opens a decoder for `codec`, primed with `parameter_sets`: Annex B NAL units with the stream's parameter
    /// sets (H.264 SPS and PPS; H.265 VPS, SPS and PPS), as the camera sends at the start of a keyframe or an RTSP
    /// session description carries them, with or without a picture. Fails when this FFmpeg has no decoder for
    /// `codec` or `parameter_sets` holds none.
    pub fn open(codec: StreamCodec, parameter_sets: &[u8]) -> Result<Self, UserError> {
        log::set_level(Level::Quiet);
        let sets = Unit::parse(codec, parameter_sets).parameter_sets;
        if sets.is_empty() {
            return Err(UserError::new(
                ErrorCode::UnsupportedFormat,
                format!("the {} stream has no parameter sets", codec.name()),
            ));
        }
        let video = open_codec(codec)?;
        Ok(Self {
            codec,
            video,
            converter: Converter::default(),
            parameter_sets: sets,
            awaiting_keyframe: true,
            pending: VecDeque::new(),
        })
    }

    /// Decodes the access unit `unit`, which is the picture `id`.
    ///
    /// `Ok(None)` when no picture comes out: the unit was skipped while waiting for a keyframe, or holds no
    /// picture. A unit the codec cannot decode is an `Err` carrying its sequence id; the decoder stays usable and
    /// skips units until the next keyframe. A keyframe whose parameter sets differ from the current ones reopens
    /// the codec, which also ends a wait for a keyframe.
    pub fn decode(&mut self, unit: &[u8], id: FrameId) -> Result<Option<VideoFrame>, FrameFailure> {
        let parsed = Unit::parse(self.codec, unit);
        let (keyframe, carries_sets) = (parsed.keyframe, !parsed.parameter_sets.is_empty());
        if keyframe {
            if carries_sets && parsed.parameter_sets != self.parameter_sets {
                self.reopen(parsed.parameter_sets)
                    .map_err(|error| failure(id, error))?;
            }
            self.awaiting_keyframe = false;
        } else if self.awaiting_keyframe {
            return Ok(None);
        }
        let Ok(pts) = i64::try_from(id.sequence_id) else {
            return Err(self.damaged(id, "its sequence id is out of range".to_string()));
        };
        let mut packet = if keyframe && !carries_sets {
            let mut data = annex_b(self.parameter_sets.iter().map(Vec::as_slice));
            data.extend_from_slice(unit);
            Packet::copy(&data)
        } else {
            Packet::copy(unit)
        };
        packet.set_pts(Some(pts));
        packet.set_dts(Some(pts));
        if let Err(error) = self.video.send_packet(&packet) {
            return Err(self.damaged(id, format!("the codec rejected it: {error}")));
        }
        self.pending.push_back(id);
        while self.pending.len() > MAX_PENDING {
            self.pending.pop_front();
        }
        self.receive(id)
    }

    /// Takes the newest frame the codec has ready and delivers it with the id its unit was given.
    fn receive(&mut self, id: FrameId) -> Result<Option<VideoFrame>, FrameFailure> {
        let mut newest: Option<Video> = None;
        loop {
            let mut frame = Video::empty();
            match self.video.receive_frame(&mut frame) {
                Ok(()) if frame.is_corrupt() || frame.has_decode_errors() => {
                    return Err(self.damaged(id, "its data is damaged".to_string()));
                }
                Ok(()) => newest = Some(frame),
                Err(Error::Other { errno }) if errno == EAGAIN => break,
                Err(error) => return Err(self.damaged(id, format!("the codec failed: {error}"))),
            }
        }
        let Some(frame) = newest else {
            return Ok(None);
        };
        let frame_id = frame
            .pts()
            .and_then(|pts| {
                self.pending
                    .iter()
                    .position(|pending| pending.sequence_id as i64 == pts)
            })
            .map_or(id, |at| {
                let found = self.pending[at];
                self.pending.drain(..=at);
                found
            });
        self.converter
            .convert(&frame, frame_id, frame.is_key())
            .map(Some)
            .map_err(|reason| failure(frame_id, UserError::new(ErrorCode::DecodeFailed, reason)))
    }

    /// Replaces the codec for a stream whose parameters changed.
    fn reopen(&mut self, parameter_sets: Vec<Vec<u8>>) -> Result<(), UserError> {
        debug!(
            codec = self.codec.name(),
            "the stream's parameter sets changed; reopening the decoder"
        );
        self.video = open_codec(self.codec)?;
        self.parameter_sets = parameter_sets;
        self.pending.clear();
        Ok(())
    }

    /// Drops what the codec holds and waits for the next keyframe; describes why unit `id` is lost.
    fn damaged(&mut self, id: FrameId, reason: String) -> FrameFailure {
        self.video.flush();
        self.pending.clear();
        self.awaiting_keyframe = true;
        failure(id, UserError::new(ErrorCode::DecodeFailed, reason))
    }
}

fn failure(id: FrameId, error: UserError) -> FrameFailure {
    FrameFailure {
        sequence_id: id.sequence_id,
        error,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ffmpeg_next::format;
    use foundation::MediaTime;
    use std::path::Path;

    const GOP: usize = 5;

    /// The access units of a fixture in `fixtures/video`, as the raw demuxers split them: Annex B, in-band parameter
    /// sets in each keyframe's unit. The `stream_*` fixtures are flat, frame `n` with luma `7 * n`.
    fn units(name: &str) -> Vec<Vec<u8>> {
        log::set_level(Level::Quiet);
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../fixtures/video")
            .join(name);
        let mut input = format::input(&path).unwrap();
        input
            .packets()
            .map(|(_, packet)| packet.data().unwrap().to_vec())
            .collect()
    }

    /// The 64x48 stream of `codec`: 12 units, a keyframe every [`GOP`], no reordering.
    fn gop5(codec: StreamCodec) -> Vec<Vec<u8>> {
        units(match codec {
            StreamCodec::H264 => "stream_h264_gop5.h264",
            StreamCodec::H265 => "stream_hevc_gop5.hevc",
        })
    }

    fn id(n: usize) -> FrameId {
        FrameId {
            sequence_id: 100 + n as u64,
            timestamp: MediaTime::from_us(n as i64 * 40_000),
        }
    }

    fn assert_luma(frame: &VideoFrame, n: usize) {
        let expected = (7 * n % 256) as i32;
        let luma = i32::from(frame.plane(0)[0]);
        assert!(
            (luma - expected).abs() <= 4,
            "frame {n} has luma {luma}, expected about {expected}"
        );
    }

    #[test]
    fn units_come_back_at_once_as_frames_with_the_ids_they_were_given() {
        for codec in [StreamCodec::H264, StreamCodec::H265] {
            let units = gop5(codec);
            assert_eq!(units.len(), 12, "{codec:?}");
            let mut decoder = StreamDecoder::open(codec, &units[0]).expect("a decoder");
            for (n, unit) in units.iter().enumerate() {
                let frame = decoder
                    .decode(unit, id(n))
                    .expect("no failure")
                    .expect("a frame per unit");
                assert_eq!(frame.id(), id(n), "{codec:?}");
                assert_eq!(frame.is_keyframe(), n % GOP == 0, "{codec:?} frame {n}");
                assert_luma(&frame, n);
            }
        }
    }

    #[test]
    fn parameter_sets_without_a_picture_start_a_stream_whose_keyframes_carry_none() {
        for codec in [StreamCodec::H264, StreamCodec::H265] {
            let units = gop5(codec);
            let nals = nal_units(&units[0]);
            let (sets, pictures): (Vec<&[u8]>, Vec<&[u8]>) = nals
                .into_iter()
                .partition(|nal| codec.is_parameter_set(codec.nal_type(nal[0])));
            assert!(!sets.is_empty(), "{codec:?} keyframes carry parameter sets");
            let mut decoder = StreamDecoder::open(codec, &annex_b(sets)).expect("a decoder");
            let keyframe = annex_b(pictures);
            let frame = decoder
                .decode(&keyframe, id(0))
                .expect("no failure")
                .expect("a frame");
            assert_eq!(frame.id(), id(0), "{codec:?}");
            assert_luma(&frame, 0);
            let next = decoder.decode(&units[1], id(1)).unwrap().expect("a frame");
            assert_luma(&next, 1);
        }
    }

    #[test]
    fn damage_skips_units_until_the_next_keyframe() {
        let units = gop5(StreamCodec::H264);
        let mut decoder = StreamDecoder::open(StreamCodec::H264, &units[0]).expect("a decoder");
        for (n, unit) in units.iter().take(2).enumerate() {
            assert!(decoder.decode(unit, id(n)).unwrap().is_some());
        }
        // A slice header cut off after its first byte cannot be decoded.
        let broken = [0, 0, 0, 1, 0x41, 0xff];
        let failure = decoder
            .decode(&broken, id(2))
            .expect_err("the codec rejects it");
        assert_eq!(failure.sequence_id, id(2).sequence_id);
        for (n, unit) in units.iter().enumerate().take(GOP).skip(3) {
            assert!(
                decoder.decode(unit, id(n)).unwrap().is_none(),
                "frame {n} is skipped"
            );
        }
        let frame = decoder
            .decode(&units[GOP], id(GOP))
            .unwrap()
            .expect("the keyframe resyncs");
        assert_eq!(frame.id(), id(GOP));
        assert!(
            decoder
                .decode(&units[GOP + 1], id(GOP + 1))
                .unwrap()
                .is_some()
        );
    }

    #[test]
    fn a_keyframe_with_new_parameter_sets_reopens_the_decoder() {
        let small = gop5(StreamCodec::H264);
        let large = units("stream_h264_96x64.h264");
        let mut decoder = StreamDecoder::open(StreamCodec::H264, &small[0]).expect("a decoder");
        let first = decoder.decode(&small[0], id(0)).unwrap().expect("a frame");
        assert_eq!(first.layout().width, 64);
        let second = decoder.decode(&large[0], id(1)).unwrap().expect("a frame");
        assert_eq!((second.layout().width, second.layout().height), (96, 64));
        assert_eq!(second.id(), id(1));
    }
}
