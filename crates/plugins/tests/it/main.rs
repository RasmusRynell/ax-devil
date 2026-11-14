//! The plugin host against real plugin processes, one binary; `adversarial` is its own binary.

// Shared with `adversarial.rs`, which is a separate binary.
#[path = "../common/mod.rs"]
mod common;
mod derived;
mod fixtures;
mod kinds;
mod manifest;
mod registry;
