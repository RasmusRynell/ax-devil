//! The real Electron render page, one binary: its first test sets the process environment, so every test here waits
//! for it in `common::export_kit::can_export`.

// Shared with the `it` binary.
#[path = "../it/common/mod.rs"]
mod common;

mod catalog_check;
mod export;
mod export_compositing;
