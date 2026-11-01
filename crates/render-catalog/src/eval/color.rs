//! Color operations: ramps between stops and stable palette picks.

use super::value::{finite, number};
use super::{Fail, invalid};
use crate::ir::Value;

/// The color of `value` on a ramp of stops: the first color at or below the first stop, the last above the last stop,
/// else a blend of the two stops around it with channels rounded half up.
pub(super) fn ramp(value: &Value, stops: &Value) -> Result<Value, Fail> {
    let stops = stops.as_list().unwrap_or_default();
    let value = finite(number(value))?;
    let channels = |stop: &Value| -> [f64; 3] {
        let color = stop.field(1).as_list().unwrap_or_default();
        [0, 1, 2].map(|index| color.get(index).map_or(0.0, number))
    };
    let at = |stop: &Value| number(stop.field(0));
    let Some((first, last)) = stops.first().zip(stops.last()) else {
        return Ok(rgb([0.0; 3]));
    };
    if value <= at(first) {
        return Ok(rgb(channels(first)));
    }
    for pair in stops.windows(2) {
        let (low, high) = (at(&pair[0]), at(&pair[1]));
        if value > high {
            continue;
        }
        // Weighted normalization avoids overflow when finite endpoints straddle zero.
        let scale = low.abs().max(high.abs()).max(1.0);
        let t = (value / scale - low / scale) / (high / scale - low / scale);
        if !t.is_finite() {
            return Err(invalid("Color stops must be strictly increasing."));
        }
        let (from, to) = (channels(&pair[0]), channels(&pair[1]));
        return Ok(rgb(
            [0, 1, 2].map(|i| (from[i] * (1.0 - t) + to[i] * t + 0.5).floor())
        ));
    }
    Ok(rgb(channels(last)))
}

fn rgb(channels: [f64; 3]) -> Value {
    Value::List(
        channels
            .map(|channel| Value::Int(channel as i64))
            .to_vec()
            .into(),
    )
}

/// The palette color the CRC-32 of the text's UTF-8 bytes selects, the same in every run (the reference hashes the
/// same bytes, so a track keeps its color).
pub(super) fn pick(text: &Value, palette: &Value) -> Value {
    let colors = palette.as_list().unwrap_or_default();
    let hash = crc32fast::hash(text.as_str().unwrap_or_default().as_bytes());
    let index = usize::try_from(hash)
        .ok()
        .and_then(|hash| hash.checked_rem(colors.len()));
    // A palette is never empty by its type; black stands in for a placeholder that is not drawn.
    index.map_or_else(|| rgb([0.0; 3]), |index| colors[index].clone())
}
