use foundation::{CancelToken, Interrupt};
use scene::{Coverage, Fields, Scene};

use crate::{DecodeError, DecodeSummary, RecordSceneDecoder, SkippedRecords};

/// Lines between two checks of the cancel token.
const CANCEL_EVERY: usize = 4096;

/// Decodes a file of one record per line: lines are split on `\n`, trimmed, blank ones skipped, and a failing
/// record is skipped and named in the warning as `line N: <reason>`.
pub fn decode_lines(
    decoder: &dyn RecordSceneDecoder,
    input: &[u8],
    emit: &mut dyn FnMut(Scene),
    cancel: &CancelToken,
) -> Result<DecodeSummary, Interrupt<DecodeError>> {
    let mut skipped = SkippedRecords::new("lines", "Those lines are not drawn.");
    let mut records = 0;
    let mut start = 0;
    let ends = memchr::memchr_iter(b'\n', input).chain(std::iter::once(input.len()));
    for (index, end) in ends.enumerate() {
        let line = input[start..end].trim_ascii();
        start = end + 1;
        if index % CANCEL_EVERY == 0 {
            cancel.check()?;
        }
        if line.is_empty() {
            continue;
        }
        records += 1;
        match decoder.decode_record(line) {
            Ok(Some(scene)) => emit(scene),
            Ok(None) => {}
            Err(reason) => skipped.add(format!("line {}: {reason}", index + 1)),
        }
    }
    Ok(DecodeSummary {
        warnings: skipped.warning(records).into_iter().collect(),
        filter: decoder.filter_policy(),
        metadata: Fields::new(),
        coverage: Coverage::Sparse,
    })
}
