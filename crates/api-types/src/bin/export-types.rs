//! Writes the Rust API types to frontend/src/generated/api.ts, only when they changed.

use ts_rs::TS;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let text = format!(
        "// Generated from crates/api-types by make; do not edit.\n{}\n",
        api_types::AppInfo::export_to_string()?
    );
    let destination = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../frontend/src/generated/api.ts"
    );
    if std::fs::read_to_string(destination).ok().as_deref() != Some(&text) {
        std::fs::write(destination, text)?;
    }
    Ok(())
}
