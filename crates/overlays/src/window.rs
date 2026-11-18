//! The sample-window rule: which samples a data window needs, and in which order they are sent.
//!
//! The backend alone decides which samples a viewer holds, so this set is what it sends and keeps; the frontend
//! holds what it is sent. `fixtures/sample-window/cases.json` pins the rule (read by the tests below only).

use playback::{Direction, Rank, rank};
use std::collections::HashMap;

/// The map cell of a frame a dense file covers with nothing.
pub const EMPTY_CELL: u32 = 0xFFFF_FFFE;

/// The map cell of a frame nothing belongs to.
pub const NONE_CELL: u32 = 0xFFFF_FFFF;

/// The first map cell that is not a sample index: [`EMPTY_CELL`] and [`NONE_CELL`] are the last two values.
pub const FIRST_NON_SAMPLE: u32 = EMPTY_CELL;

/// Frames of a map chunk; a run never spans two.
pub const RUN_FRAMES: u16 = 256;

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
