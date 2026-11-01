//! The Sample (kind 4) and FrameMap (kind 5) envelopes: the data a lane draws from. The layouts are documented with
//! the constants in `api_types::playback`, and `frontend/src/playback/protocol.ts` parses them.

use super::envelope::head;
use api_types::playback::{
    FRAME_MAP_HEADER_BYTES, KIND_FRAME_MAP, KIND_SAMPLE, SAMPLE_HEADER_BYTES,
};
use bytes::{BufMut, Bytes, BytesMut};
use foundation::{FrameId, MediaTime};
use overlays::{MapRun, SampleItem};

/// The head of an item: `sequence_id` is its first frame or sample, and the time is reserved.
fn item_head(out: &mut BytesMut, kind: u8, header_len: u16, sequence_id: u64) {
    let id = FrameId {
        sequence_id,
        timestamp: MediaTime::ZERO,
    };
    head(out, kind, header_len, id, 0);
}

/// The Sample envelope of `sample`. The item starts at a multiple of 8, so the receiver views its columns in place.
pub(super) fn sample(sample: &SampleItem) -> Bytes {
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
pub(super) fn frame_map(run: &MapRun) -> Bytes {
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
    use api_types::playback::{FRAME_MAP_EMPTY, FRAME_MAP_NONE};
    use std::{fs, path::Path};

    fn golden(name: &str, bytes: &[u8]) {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fixtures/wire")
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
            Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/oracle/items/classes.bin"),
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
