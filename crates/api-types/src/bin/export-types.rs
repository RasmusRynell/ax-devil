//! Writes the Rust API types to frontend/src/generated/api.ts and the API version to desktop/generated/api.json,
//! only when they changed.

use ts_rs::TS;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let text = format!(
        "// Generated from crates/api-types by make; do not edit.\n{}\n",
        api_types::AppInfo::export_to_string()?
    );
    write_if_changed("frontend/src/generated/api.ts", &text)?;
    // The desktop shell and the development supervisor are CommonJS and read the version from JSON.
    let version = format!("{{ \"apiVersion\": {} }}\n", api_types::API_VERSION);
    write_if_changed("desktop/generated/api.json", &version)?;
    Ok(())
}

fn write_if_changed(path: &str, text: &str) -> std::io::Result<()> {
    let destination = format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"));
    if std::fs::read_to_string(&destination).ok().as_deref() == Some(text) {
        return Ok(());
    }
    if let Some(folder) = std::path::Path::new(&destination).parent() {
        std::fs::create_dir_all(folder)?;
    }
    std::fs::write(destination, text)
}
