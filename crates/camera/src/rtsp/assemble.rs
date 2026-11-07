//! Rebuilds Annex B access units from RTP packets (RFC 6184 and RFC 7798 payloads).
//!
//! Retina's own depacketizers do the same but hand out no RTP header extension, and the capture time lives there.
//! A frame that lost packets, broke a fragment sequence or outgrew its limit is discarded whole, never emitted in
//! part.

use std::sync::Arc;

use bytes::{BufMut, Bytes, BytesMut};

use super::packet::Packet;
use crate::live::{CaptureTime, CodecParams, VideoCodec};

const START_CODE: [u8; 4] = [0, 0, 0, 1];
/// The most bytes one access unit may grow to: far above a 4K keyframe, finite against a stream that never ends one.
const MAX_ACCESS_UNIT: usize = 16 << 20;

/// A video frame, before it is stamped with its connection's epoch.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Frame {
    pub(crate) keyframe: bool,
    pub(crate) capture: Option<CaptureTime>,
    pub(crate) data: Bytes,
}

/// What a NAL unit is, as far as assembly cares.
#[derive(Clone, Copy, Default)]
enum Kind {
    /// A slice that lets decoding start (IDR, or IRAP for H.265).
    Keyframe,
    Vps,
    Sps,
    Pps,
    #[default]
    Other,
}

impl Kind {
    fn of(codec: VideoCodec, first: u8) -> Self {
        match codec {
            VideoCodec::H264 => match first & 0x1F {
                5 => Self::Keyframe,
                7 => Self::Sps,
                8 => Self::Pps,
                _ => Self::Other,
            },
            VideoCodec::H265 => match (first >> 1) & 0x3F {
                16..=21 => Self::Keyframe,
                32 => Self::Vps,
                33 => Self::Sps,
                34 => Self::Pps,
                _ => Self::Other,
            },
        }
    }
}

/// The parameter sets a decoder needs, as the stream last sent them.
#[derive(Default)]
struct Sets {
    vps: Option<Bytes>,
    sps: Option<Bytes>,
    pps: Option<Bytes>,
}

impl Sets {
    fn set(&mut self, kind: &Kind, nal: &[u8]) {
        let slot = match kind {
            Kind::Vps => &mut self.vps,
            Kind::Sps => &mut self.sps,
            Kind::Pps => &mut self.pps,
            Kind::Keyframe | Kind::Other => return,
        };
        *slot = Some(Bytes::copy_from_slice(nal));
    }

    fn merge(&mut self, newer: &Sets) {
        for (slot, new) in [
            (&mut self.vps, &newer.vps),
            (&mut self.sps, &newer.sps),
            (&mut self.pps, &newer.pps),
        ] {
            if new.is_some() {
                slot.clone_from(new);
            }
        }
    }

    /// Whether every set the codec needs is here (H.265 also needs a VPS).
    fn complete(&self, codec: VideoCodec) -> bool {
        self.sps.is_some()
            && self.pps.is_some()
            && (codec == VideoCodec::H264 || self.vps.is_some())
    }

    fn annex_b(&self) -> BytesMut {
        let mut out = BytesMut::new();
        for nal in [&self.vps, &self.sps, &self.pps].into_iter().flatten() {
            out.put_slice(&START_CODE);
            out.put_slice(nal);
        }
        out
    }
}

/// A frame under assembly.
#[derive(Default)]
struct Pending {
    timestamp: Option<u32>,
    capture: Option<CaptureTime>,
    data: BytesMut,
    keyframe: bool,
    /// The parameter sets in this frame itself.
    sets: Sets,
    /// Inside a fragmented NAL unit: its start was seen, its end not yet.
    in_fragment: bool,
    /// Where the latest NAL unit begins in `data` (after its start code), and what it is.
    nal_start: usize,
    nal_kind: Kind,
    /// Packets went missing, a fragment sequence broke, or the frame outgrew its limit: it is thrown away.
    damaged: bool,
}

/// Builds the access units of one H.264 or H.265 stream.
///
/// The RTP marker bit ends a frame; a changed timestamp also does, in case the marker was lost. A frame that lost
/// packets is discarded entirely, and so is everything after it until a keyframe that arrived intact, because the
/// frames in between would decode wrongly.
pub(crate) struct VideoAssembler {
    /// What the units are, shared with every access unit sent.
    pub(crate) params: Arc<CodecParams>,
    pending: Pending,
    /// The parameter sets last seen, described or in-band, to give every keyframe that lacks them.
    sets: Sets,
    waiting_for_keyframe: bool,
    limit: usize,
    dropped: u64,
}

impl VideoAssembler {
    pub(crate) fn new(params: Arc<CodecParams>) -> Self {
        let mut sets = Sets::default();
        for nal in split_annex_b(&params.parameter_sets) {
            sets.set(&Kind::of(params.codec, nal[0]), nal);
        }
        Self {
            params,
            pending: Pending::default(),
            sets,
            waiting_for_keyframe: false,
            limit: MAX_ACCESS_UNIT,
            dropped: 0,
        }
    }

    /// How many frames were discarded since the last call.
    pub(crate) fn take_dropped(&mut self) -> u64 {
        std::mem::take(&mut self.dropped)
    }

    /// Adds one packet; appends each finished frame to `out`.
    pub(crate) fn push(&mut self, packet: &Packet, out: &mut Vec<Frame>) {
        if packet.loss > 0 {
            // The frame in progress lost packets, and so may the one this packet starts.
            self.pending.damaged = true;
        }
        if self
            .pending
            .timestamp
            .is_some_and(|t| t != packet.timestamp)
        {
            self.finish(out);
        }
        if packet.loss > 0 {
            self.pending.damaged = true;
        }
        self.pending.timestamp = Some(packet.timestamp);
        if packet.capture.is_some() {
            self.pending.capture = packet.capture;
        }
        if !self.pending.damaged {
            self.depacketize(&packet.payload);
            if self.pending.data.len() > self.limit {
                self.pending.damaged = true;
                self.pending.data = BytesMut::new();
            }
        }
        if packet.marker {
            self.finish(out);
        }
    }

    fn finish(&mut self, out: &mut Vec<Frame>) {
        let mut pending = std::mem::take(&mut self.pending);
        pending.damaged |= pending.in_fragment;
        if pending.damaged {
            self.dropped += 1;
            self.waiting_for_keyframe = true;
            return;
        }
        if pending.data.is_empty() {
            return;
        }
        if self.waiting_for_keyframe && !pending.keyframe {
            self.dropped += 1;
            return;
        }
        self.waiting_for_keyframe = false;
        let codec = self.params.codec;
        self.sets.merge(&pending.sets);
        let mut data = pending.data;
        if pending.keyframe && !pending.sets.complete(codec) && self.sets.complete(codec) {
            let mut with = self.sets.annex_b();
            with.put(data);
            data = with;
        }
        out.push(Frame {
            keyframe: pending.keyframe,
            capture: pending.capture,
            data: data.freeze(),
        });
    }

    /// Appends a whole NAL unit (single or aggregated), which also ends any fragmented one.
    fn whole(&mut self, nal: &[u8]) {
        self.pending.damaged |= self.pending.in_fragment;
        self.pending.in_fragment = false;
        if !nal.is_empty() {
            self.nal(nal);
            self.end_nal();
        }
    }

    /// Begins a NAL unit with `nal`, its first bytes (all of it, or a fragmented one's header).
    fn nal(&mut self, nal: &[u8]) {
        let kind = Kind::of(self.params.codec, nal[0]);
        self.pending.keyframe |= matches!(kind, Kind::Keyframe);
        self.pending.data.put_slice(&START_CODE);
        self.pending.nal_start = self.pending.data.len();
        self.pending.nal_kind = kind;
        self.pending.data.put_slice(nal);
    }

    /// The latest NAL unit is complete: a parameter set is kept as reassembled.
    fn end_nal(&mut self) {
        let pending = &mut self.pending;
        pending
            .sets
            .set(&pending.nal_kind, &pending.data[pending.nal_start..]);
    }

    /// One fragment of a NAL unit whose header is `header`: the start begins the unit, the others must follow it.
    fn fragment(&mut self, (start, end): (bool, bool), header: &[u8], body: &[u8]) {
        if start {
            self.pending.damaged |= self.pending.in_fragment;
            self.nal(header);
            self.pending.in_fragment = true;
        } else if !self.pending.in_fragment {
            self.pending.damaged = true;
            return;
        }
        self.pending.data.put_slice(body);
        if end {
            self.pending.in_fragment = false;
            self.end_nal();
        }
    }

    fn depacketize(&mut self, payload: &[u8]) {
        match self.params.codec {
            VideoCodec::H264 => self.h264(payload),
            VideoCodec::H265 => self.h265(payload),
        }
    }

    /// RFC 6184: a single NAL unit, STAP-A (24) or FU-A (28).
    fn h264(&mut self, payload: &[u8]) {
        let Some(&header) = payload.first() else {
            return;
        };
        match header & 0x1F {
            1..=23 => self.whole(payload),
            24 => self.aggregated(&payload[1..]),
            28 if payload.len() >= 2 => {
                let fu = payload[1];
                let nal_header = (header & 0xE0) | (fu & 0x1F);
                self.fragment(
                    (fu & 0x80 != 0, fu & 0x40 != 0),
                    &[nal_header],
                    &payload[2..],
                );
            }
            _ => {}
        }
    }

    /// RFC 7798: a single NAL unit, an aggregation packet (48) or a fragmentation unit (49).
    fn h265(&mut self, payload: &[u8]) {
        let Some(&header) = payload.first() else {
            return;
        };
        match (header >> 1) & 0x3F {
            0..=47 => self.whole(payload),
            48 => self.aggregated(&payload[2.min(payload.len())..]),
            49 if payload.len() >= 3 => {
                let fu = payload[2];
                let nal_header = [(header & 0x81) | ((fu & 0x3F) << 1), payload[1]];
                self.fragment((fu & 0x80 != 0, fu & 0x40 != 0), &nal_header, &payload[3..]);
            }
            _ => {}
        }
    }

    /// The NAL units of a STAP-A or aggregation packet: each is preceded by its size in two bytes.
    fn aggregated(&mut self, mut rest: &[u8]) {
        while let [high, low, tail @ ..] = rest {
            let size = usize::from(u16::from_be_bytes([*high, *low]));
            let Some((nal, after)) = tail.split_at_checked(size) else {
                self.pending.damaged = true;
                return;
            };
            self.whole(nal);
            rest = after;
        }
    }
}

/// The NAL units of `blob`, which is written with four-byte start codes.
fn split_annex_b(blob: &[u8]) -> Vec<&[u8]> {
    let mut starts: Vec<usize> = (0..blob.len().saturating_sub(3))
        .filter(|&i| blob[i..i + 4] == START_CODE)
        .collect();
    starts.push(blob.len());
    starts
        .windows(2)
        .map(|w| &blob[w[0] + 4..w[1]])
        .filter(|nal| !nal.is_empty())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const SPS: &[u8] = &[0x67, 0x4d, 0x00, 0x1f];
    const PPS: &[u8] = &[0x68, 0xee, 0x3c, 0x80];

    fn params(codec: VideoCodec, parameter_sets: &[u8]) -> Arc<CodecParams> {
        Arc::new(CodecParams {
            codec,
            parameter_sets: Bytes::copy_from_slice(parameter_sets),
        })
    }

    fn packet(timestamp: u32, marker: bool, payload: &[u8]) -> Packet {
        Packet {
            marker,
            timestamp,
            loss: 0,
            capture: None,
            payload: Bytes::copy_from_slice(payload),
        }
    }

    fn annex_b(nals: &[&[u8]]) -> Vec<u8> {
        nals.iter()
            .flat_map(|nal| START_CODE.iter().chain(nal.iter()).copied())
            .collect()
    }

    fn run(assembler: &mut VideoAssembler, packets: &[Packet]) -> Vec<Frame> {
        let mut out = Vec::new();
        for packet in packets {
            assembler.push(packet, &mut out);
        }
        out
    }

    #[test]
    fn h264_stap_a_and_fu_a_become_one_keyframe() {
        let mut stap = vec![24, 0, SPS.len() as u8];
        stap.extend(SPS);
        stap.extend([0, PPS.len() as u8]);
        stap.extend(PPS);
        // An IDR slice (0x65, then 4 bytes) in two FU-A fragments.
        let mut a = packet(90, false, &stap);
        a.capture = Some(CaptureTime(7));
        let frames = run(
            &mut VideoAssembler::new(params(VideoCodec::H264, b"")),
            &[
                a,
                packet(90, false, &[0x7C, 0x85, 1, 2]),
                packet(90, true, &[0x7C, 0x45, 3, 4]),
            ],
        );
        assert_eq!(
            frames,
            [Frame {
                keyframe: true,
                capture: Some(CaptureTime(7)),
                data: Bytes::from(annex_b(&[SPS, PPS, &[0x65, 1, 2, 3, 4]])),
            }]
        );
    }

    #[test]
    fn a_keyframe_without_parameters_gets_the_described_ones_and_a_p_frame_does_not() {
        let sets = annex_b(&[SPS, PPS]);
        let mut assembler = VideoAssembler::new(params(VideoCodec::H264, &sets));
        let frames = run(
            &mut assembler,
            &[packet(1, true, &[0x65, 9]), packet(2, true, &[0x41, 8])],
        );
        assert_eq!(
            frames[0].data,
            Bytes::from([sets.clone(), annex_b(&[&[0x65, 9]])].concat())
        );
        assert_eq!(frames[1].data, Bytes::from(annex_b(&[&[0x41, 8]])));
        assert!(!frames[1].keyframe);
    }

    #[test]
    fn a_changed_timestamp_ends_a_frame_whose_marker_was_lost() {
        let frames = run(
            &mut VideoAssembler::new(params(VideoCodec::H264, b"")),
            &[
                packet(1, false, &[0x41, 1]),
                packet(2, false, &[0x41, 2]),
                packet(2, true, &[0x41, 3]),
            ],
        );
        assert_eq!(frames.len(), 2);
    }

    #[test]
    fn loss_discards_the_frame_and_waits_for_a_keyframe() {
        let mut assembler = VideoAssembler::new(params(VideoCodec::H264, b""));
        let mut lossy = packet(2, true, &[0x41, 2]);
        lossy.loss = 1;
        let frames = run(
            &mut assembler,
            &[
                packet(1, true, &[0x41, 1]),
                lossy,
                packet(3, true, &[0x41, 3]),
                packet(4, true, &[0x65, 4]),
                packet(5, true, &[0x41, 5]),
            ],
        );
        let kinds: Vec<bool> = frames.iter().map(|f| f.keyframe).collect();
        assert_eq!(kinds, [false, true, false]);
        assert_eq!(
            assembler.take_dropped(),
            2,
            "the lossy frame and the one waiting for a keyframe"
        );
    }

    #[test]
    fn loss_inside_a_multi_slice_keyframe_discards_the_whole_picture() {
        let mut assembler = VideoAssembler::new(params(VideoCodec::H264, b""));
        let mut second_slice = packet(4, true, &[0x65, 2]);
        second_slice.loss = 1;
        let frames = run(
            &mut assembler,
            &[
                packet(4, false, &[0x65, 1]),
                second_slice,
                packet(5, true, &[0x41, 5]),
                packet(6, false, &[0x65, 6]),
                packet(6, true, &[0x65, 7]),
            ],
        );
        assert_eq!(frames.len(), 1, "only the clean keyframe picture comes out");
        assert_eq!(
            frames[0].data,
            Bytes::from(annex_b(&[&[0x65, 6], &[0x65, 7]]))
        );
        assert_eq!(assembler.take_dropped(), 2);
    }

    #[test]
    fn a_fragment_without_its_start_damages_the_picture() {
        let mut assembler = VideoAssembler::new(params(VideoCodec::H264, b""));
        let frames = run(
            &mut assembler,
            &[
                packet(1, true, &[0x7C, 0x45, 9]),
                packet(2, false, &[0x7C, 0x85, 1]),
                packet(2, true, &[0x41, 2]),
            ],
        );
        assert!(
            frames.is_empty(),
            "a fragment that never ended, and one that never began"
        );
        assert_eq!(assembler.take_dropped(), 2);
    }

    #[test]
    fn an_oversized_access_unit_is_dropped_and_recovery_waits_for_a_keyframe() {
        let mut assembler = VideoAssembler::new(params(VideoCodec::H264, b""));
        assembler.limit = 64;
        let big = [&[0x41u8][..], &[0xAA; 100]].concat();
        let frames = run(
            &mut assembler,
            &[
                packet(1, false, &big),
                packet(1, true, &[0x41, 1]),
                packet(2, true, &[0x41, 2]),
                packet(3, true, &[0x65, 3]),
            ],
        );
        assert_eq!(frames.len(), 1);
        assert!(frames[0].keyframe);
        assert_eq!(assembler.take_dropped(), 2);
    }

    #[test]
    fn fragmented_parameter_sets_are_kept_whole_for_the_keyframe_after_a_loss() {
        // SPS and PPS as FU-A (type 28) fragments, each cut in two; the SDP described none.
        let fragments = |nal: &[u8], kind: u8| {
            let (a, b) = nal[1..].split_at(2);
            [
                [&[0x7C, 0x80 | kind][..], a].concat(),
                [&[0x7C, 0x40 | kind][..], b].concat(),
            ]
        };
        let [sps_a, sps_b] = fragments(SPS, 7);
        let [pps_a, pps_b] = fragments(PPS, 8);
        let mut lossy = packet(3, true, &[0x41, 3]);
        lossy.loss = 1;
        let frames = run(
            &mut VideoAssembler::new(params(VideoCodec::H264, b"")),
            &[
                packet(1, false, &sps_a),
                packet(1, false, &sps_b),
                packet(1, false, &pps_a),
                packet(1, false, &pps_b),
                packet(1, true, &[0x65, 1]),
                lossy,
                packet(4, true, &[0x65, 4]),
            ],
        );
        let expected = |index: u8| Bytes::from(annex_b(&[SPS, PPS, &[0x65, index]]));
        assert_eq!(frames.len(), 2, "the lossy frame is gone");
        assert_eq!(frames[0].data, expected(1));
        assert_eq!(
            frames[1].data,
            expected(4),
            "the recovery keyframe carries the whole sets"
        );
    }

    #[test]
    fn in_band_parameter_sets_are_kept_for_later_keyframes() {
        let mut stap = vec![24, 0, SPS.len() as u8];
        stap.extend(SPS);
        stap.extend([0, PPS.len() as u8]);
        stap.extend(PPS);
        let frames = run(
            &mut VideoAssembler::new(params(VideoCodec::H264, b"")),
            &[
                packet(1, false, &stap),
                packet(1, true, &[0x65, 1]),
                packet(2, true, &[0x41, 2]),
                packet(3, true, &[0x65, 3]),
            ],
        );
        assert_eq!(
            frames[0].data,
            Bytes::from(annex_b(&[SPS, PPS, &[0x65, 1]]))
        );
        assert_eq!(
            frames[2].data,
            Bytes::from(annex_b(&[SPS, PPS, &[0x65, 3]]))
        );
    }

    #[test]
    fn h265_aggregation_and_fragmentation_units_become_a_keyframe() {
        let (vps, sps, idr) = (
            [0x40u8, 1, 0xAA],
            [0x42u8, 1, 0xBB],
            [0x26u8, 1, 0xCC, 0xDD],
        );
        let mut ap = vec![48 << 1, 1];
        for nal in [&vps[..], &sps[..]] {
            ap.extend([0, nal.len() as u8]);
            ap.extend(nal);
        }
        // An IDR_W_RADL (type 19) in two fragmentation units (type 49): payload header 0x62 0x01, FU header.
        let frames = run(
            &mut VideoAssembler::new(params(VideoCodec::H265, b"")),
            &[
                packet(1, false, &ap),
                packet(1, false, &[0x62, 1, 0x80 | 19, 0xCC]),
                packet(1, true, &[0x62, 1, 0x40 | 19, 0xDD]),
            ],
        );
        assert_eq!(frames.len(), 1);
        assert!(frames[0].keyframe);
        assert_eq!(frames[0].data, Bytes::from(annex_b(&[&vps, &sps, &idr])));
    }
}
