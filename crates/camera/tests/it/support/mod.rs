//! Fakes of a camera for the integration tests. Each test file uses part of them, hence the allow.
#![allow(dead_code)] // Shared by several test files; none of them uses everything.

pub mod digest;
pub mod fake_analytics;
pub mod fake_broker;
pub mod fake_datahub;
pub mod fake_rtsp;
pub mod fake_vapix;
pub mod log;
pub mod tls_server;
