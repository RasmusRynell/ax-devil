//! The envelopes the backend sends, read with the offsets of the documented layout.

use api_types::playback::{KIND_FRAME_MAP, KIND_SAMPLE, SAMPLE_HEADER_BYTES, ServerMessage};

/// One frame envelope, read with the offsets of the documented layout.
pub struct Frame {
    pub sequence_id: u64,
    pub timestamp_us: i64,
    pub flags: u32,
    pub width: u32,
    pub height: u32,
    pub layout: u8,
    pub plane_lengths: [u32; 3],
    pub planes: Vec<u8>,
}

impl Frame {
    /// The luma every sample of the Y plane has; fails when the picture is not flat.
    pub fn flat_luma(&self) -> u8 {
        let y = &self.planes[..self.plane_lengths[0] as usize];
        assert!(
            y.iter().all(|sample| *sample == y[0]),
            "frame {} is not flat",
            self.sequence_id
        );
        y[0]
    }
}

pub fn u32_at(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap())
}

pub fn u64_at(bytes: &[u8], at: usize) -> u64 {
    u64::from_le_bytes(bytes[at..at + 8].try_into().unwrap())
}

/// One Sample envelope: the head and the sample header, and what the item says about itself.
pub struct Sample {
    pub source: u16,
    pub revision: u32,
    /// The sample's index in its file.
    pub sample: u64,
    /// The entities of the item.
    pub rows: u32,
}

/// One FrameMap envelope: a run of a lane's map from frame to sample.
pub struct Map {
    pub lane: u16,
    pub policy: u32,
    pub data: u32,
    pub first: u64,
    /// A sample index per frame, or `FRAME_MAP_NONE` or `FRAME_MAP_EMPTY`.
    pub samples: Vec<u32>,
    pub kinds: Vec<u8>,
}

pub enum Event {
    Message(ServerMessage),
    Frame(Frame),
    Sample(Sample),
    Map(Map),
    Timeline(Vec<f64>),
    Closed,
}

/// The envelope head, checked: magic, version 1 and a payload that starts where `header_len` says.
pub fn parse_envelope(bytes: &[u8]) -> Event {
    assert_eq!(&bytes[..4], b"AXDV");
    assert_eq!(bytes[4], 1, "envelope version");
    let payload_at = u16::from_le_bytes([bytes[6], bytes[7]]) as usize;
    match bytes[5] {
        1 => {
            assert_eq!(payload_at, 64);
            let lengths = [u32_at(bytes, 52), u32_at(bytes, 56), u32_at(bytes, 60)];
            let planes = bytes[payload_at..].to_vec();
            assert_eq!(
                planes.len(),
                lengths.iter().sum::<u32>() as usize,
                "the message is exactly its planes"
            );
            Event::Frame(Frame {
                sequence_id: u64_at(bytes, 8),
                timestamp_us: u64_at(bytes, 16) as i64,
                flags: u32_at(bytes, 24),
                width: u32_at(bytes, 28),
                height: u32_at(bytes, 32),
                layout: bytes[36],
                plane_lengths: lengths,
                planes,
            })
        }
        KIND_SAMPLE => {
            assert_eq!(payload_at, usize::from(SAMPLE_HEADER_BYTES));
            assert_eq!(bytes.len() % 8, 0, "the item is a whole number of words");
            Event::Sample(Sample {
                source: u16::from_le_bytes([bytes[28], bytes[29]]),
                revision: u32_at(bytes, 32),
                sample: u64_at(bytes, 8),
                rows: u32_at(bytes, payload_at + 8),
            })
        }
        KIND_FRAME_MAP => {
            assert_eq!(payload_at, 48);
            let count = u32_at(bytes, 40) as usize;
            assert!((1..=256).contains(&count));
            assert_eq!(
                bytes.len(),
                48 + 5 * count,
                "the message is exactly its map"
            );
            Event::Map(Map {
                lane: u16::from_le_bytes([bytes[28], bytes[29]]),
                policy: u32_at(bytes, 32),
                data: u32_at(bytes, 36),
                first: u64_at(bytes, 8),
                samples: (0..count).map(|k| u32_at(bytes, 48 + 4 * k)).collect(),
                kinds: bytes[48 + 4 * count..].to_vec(),
            })
        }
        3 => {
            assert_eq!(payload_at, 32);
            Event::Timeline(
                bytes[payload_at..]
                    .as_chunks::<8>()
                    .0
                    .iter()
                    .map(|time| f64::from_le_bytes(*time))
                    .collect(),
            )
        }
        kind => panic!("unexpected envelope kind {kind}"),
    }
}
