//! The sample-window rule: which samples a data window needs, and in which order they are sent.
//!
//! The backend sends exactly this set and the frontend keeps exactly this set, so both sides implement the rule and
//! check it against `fixtures/sample-window/cases.json` (the TypeScript twin is
//! `frontend/src/playback/data/sampleWindow.ts`). A drift would leave a lane waiting for a sample that never comes.

use playback::{Direction, Rank, rank};
use std::collections::HashMap;

/// The first map cell that is not a sample index: `FRAME_MAP_EMPTY` (0xFFFF_FFFE) and `FRAME_MAP_NONE` (0xFFFF_FFFF)
/// of the playback wire.
pub(crate) const FIRST_NON_SAMPLE: u32 = 0xFFFF_FFFE;

/// The map cell of a frame nothing belongs to: `FRAME_MAP_NONE` of the playback wire.
pub(crate) const NONE_CELL: u32 = 0xFFFF_FFFF;

/// The samples the lanes' maps select inside a data window, most urgent first.
///
/// `maps[l][k]` is lane `l`'s map cell of frame `first + k`; the lanes are those subscribed on one ready source.
/// Each sample comes with its ranking frame: of the frames that map to it, the one with the least
/// [`playback::rank`] for `playhead` and `direction`. The order is ascending `(rank of that frame, sample index)`, so
/// a sample two lanes select at one frame ties on rank and the lower index goes first.
pub fn sample_window(
    maps: &[&[u32]],
    first: u64,
    playhead: u64,
    direction: Direction,
) -> Vec<(u64, u64)> {
    let mut best: HashMap<u32, (Rank, u64)> = HashMap::new();
    for map in maps {
        for (frame, &cell) in (first..).zip(map.iter()) {
            if cell >= FIRST_NON_SAMPLE {
                continue;
            }
            let candidate = (rank(direction, playhead, frame), frame);
            best.entry(cell)
                .and_modify(|held| *held = (*held).min(candidate))
                .or_insert(candidate);
        }
    }
    let mut ordered: Vec<(Rank, u32, u64)> = best
        .into_iter()
        .map(|(sample, (rank, frame))| (rank, sample, frame))
        .collect();
    ordered.sort_unstable();
    ordered
        .into_iter()
        .map(|(_, sample, frame)| (u64::from(sample), frame))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use overlay_sync::MatchKind;
    use serde_json::Value;

    fn numbers(value: &Value) -> Vec<u64> {
        value
            .as_array()
            .unwrap()
            .iter()
            .map(|n| n.as_u64().unwrap())
            .collect()
    }

    #[test]
    fn the_cells_are_the_ones_of_the_playback_wire() {
        assert_eq!(FIRST_NON_SAMPLE, api_types::playback::FRAME_MAP_EMPTY);
        assert_eq!(NONE_CELL, api_types::playback::FRAME_MAP_NONE);
        assert_eq!(
            u64::from(api_types::playback::FRAME_MAP_RUN_FRAMES),
            crate::pull::RUN_FRAMES
        );
        let kinds = [MatchKind::Exact, MatchKind::Tolerated, MatchKind::Retained];
        let wire = [
            api_types::playback::MATCH_EXACT,
            api_types::playback::MATCH_TOLERATED,
            api_types::playback::MATCH_RETAINED,
        ];
        assert_eq!(kinds.map(crate::data::kind_byte), wire);
    }

    #[test]
    fn matches_the_shared_fixture() {
        let fixture: Value =
            serde_json::from_str(include_str!("../../../fixtures/sample-window/cases.json"))
                .unwrap();
        let cases = fixture["cases"].as_array().unwrap();
        assert!(!cases.is_empty());
        for case in cases {
            let name = case["name"].as_str().unwrap();
            let direction = match case["direction"].as_str().unwrap() {
                "forward" => Direction::Forward,
                "backward" => Direction::Backward,
                other => panic!("{name}: unknown direction {other}"),
            };
            let lanes: Vec<Vec<u32>> = case["maps"]
                .as_array()
                .unwrap()
                .iter()
                .map(|map| {
                    numbers(map)
                        .into_iter()
                        .map(|cell| u32::try_from(cell).unwrap())
                        .collect()
                })
                .collect();
            let maps: Vec<&[u32]> = lanes.iter().map(Vec::as_slice).collect();
            let expected: Vec<(u64, u64)> = case["expected"]
                .as_array()
                .unwrap()
                .iter()
                .map(|pair| {
                    let pair = numbers(pair);
                    (pair[0], pair[1])
                })
                .collect();
            let found = sample_window(
                &maps,
                case["first"].as_u64().unwrap(),
                case["playhead"].as_u64().unwrap(),
                direction,
            );
            assert_eq!(found, expected, "{name}");
        }
    }
}
