//! Bakes the API version into the crate: a hash of the generated TypeScript that is committed under
//! frontend/src/generated, so a change to the wire types changes it with no edit of a stored version.

#[path = "contract_version.rs"]
mod contract_version;

fn main() {
    let generated = std::path::Path::new(
        &std::env::var("CARGO_MANIFEST_DIR").expect("cargo sets the manifest dir"),
    )
    .join("../../frontend/src/generated");
    println!("cargo:rerun-if-changed={}", generated.display());
    let version = contract_version::contract_version(&generated)
        .unwrap_or_else(|error| panic!("cannot hash {}: {error}", generated.display()));
    println!("cargo:rustc-env=AX_DEVIL_API_VERSION={version}");
}
