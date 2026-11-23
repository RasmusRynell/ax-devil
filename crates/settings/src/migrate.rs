//! Upgrades an older settings file to the current format, one version at a time.

use serde_json::{Map, Value};

/// Upgrades the whole document (everything except `version`) by one version.
pub(crate) type Migration = fn(&mut Map<String, Value>) -> Result<(), String>;

/// `MIGRATIONS[i]` upgrades version `i + 1` to `i + 2`. Append only; never edit a released step.
pub(crate) const MIGRATIONS: &[Migration] = &[];

/// The format version this build reads and writes.
pub const VERSION: u64 = MIGRATIONS.len() as u64 + 1;

/// Runs the steps of `chain` that take a document at version `from` up to the end of the chain, in order.
pub(crate) fn migrate(
    doc: &mut Map<String, Value>,
    from: u64,
    chain: &[Migration],
) -> Result<(), String> {
    for (index, step) in chain.iter().enumerate().skip(from as usize - 1) {
        step(doc).map_err(|reason| format!("v{} to v{}: {reason}", index + 1, index + 2))?;
    }
    Ok(())
}
