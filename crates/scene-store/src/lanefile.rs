//! Lane files: a track written as JSON Lines, the input a plugin program reads.

use std::{io::Write, ops::Range, slice};

use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use scene::{Coverage, MatchBasis, Scene, serial};
use serde_json::json;
use sha2::{Digest, Sha256};

use crate::TrackRead;

/// Writes `track` to `out` as a lane file: a line `{"lane":{"basis":..,"coverage":..,"metadata":{..}}}`, then one
/// `{"sample": <Scene JSON>}` line per sample of `range` (every sample when `None`), in the track's order. Returns the
/// SHA-256 of the bytes written. The track is read as published now, so a caller that needs every sample waits for the
/// ingest to end first. Fails when the file would pass `max_bytes`.
pub fn write_lane(
    track: &dyn TrackRead,
    range: Option<Range<u32>>,
    max_bytes: u64,
    out: &mut dyn Write,
    cancel: &CancelToken,
) -> Result<[u8; 32], Interrupt<UserError>> {
    let snapshot = track.snapshot();
    let count = snapshot.keys.len();
    let range = range.map_or(0..count, |range| {
        let end = usize::try_from(range.end).map_or(count, |end| end.min(count));
        usize::try_from(range.start).map_or(end, |start| start.min(end))..end
    });
    let mut lines = Lines {
        out,
        hash: Sha256::new(),
        written: 0,
        max_bytes,
    };
    let metadata: serde_json::Map<_, _> = track
        .metadata()
        .iter()
        .map(|(key, value)| {
            let value = serde_json::to_value(value).unwrap_or(serde_json::Value::Null);
            (key.as_ref().to_owned(), value)
        })
        .collect();
    let basis = match track.default_basis() {
        MatchBasis::Frame => "frame",
        MatchBasis::Time => "time",
    };
    let coverage = match track.coverage() {
        Coverage::Sparse => "sparse",
        Coverage::Dense => "dense",
    };
    let header = json!({"lane": {"basis": basis, "coverage": coverage, "metadata": metadata}});
    lines.push(&header.to_string())?;
    for index in range {
        cancel.check()?;
        let scene = track
            .scene_in(&snapshot, index)
            .map_err(Interrupt::Failed)?;
        let body = scene_json(&scene).map_err(Interrupt::Failed)?;
        lines.push(&format!("{{\"sample\":{body}}}"))?;
    }
    Ok(lines.hash.finalize().into())
}

/// The lines written so far, counted and hashed.
struct Lines<'a> {
    out: &'a mut dyn Write,
    hash: Sha256,
    written: u64,
    max_bytes: u64,
}

impl Lines<'_> {
    fn push(&mut self, line: &str) -> Result<(), Interrupt<UserError>> {
        self.written += line.len() as u64 + 1;
        if self.written > self.max_bytes {
            return Err(Interrupt::Failed(UserError::new(
                ErrorCode::InvalidRequest,
                format!("The lane is larger than {} bytes", self.max_bytes),
            )));
        }
        for part in [line.as_bytes(), b"\n"] {
            self.hash.update(part);
            self.out.write_all(part).map_err(|error| {
                Interrupt::Failed(UserError::new(
                    ErrorCode::SaveFailed,
                    format!("Cannot write the lane: {error}"),
                ))
            })?;
        }
        Ok(())
    }
}

/// The JSON of one Scene: the element `scene::serial` writes inside its document of Scenes.
fn scene_json(scene: &Scene) -> Result<String, UserError> {
    let internal = |reason: String| UserError::new(ErrorCode::Internal, reason);
    let document = serial::to_json_string(slice::from_ref(scene))
        .map_err(|error| internal(format!("Cannot write a Scene: {error}")))?;
    document
        .split_once("\"scenes\":[")
        .and_then(|(_, rest)| rest.strip_suffix("]}"))
        .map(str::to_owned)
        .ok_or_else(|| internal("Cannot write a Scene: unexpected document shape".into()))
}
