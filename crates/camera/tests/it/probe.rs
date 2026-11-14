//! The `probe` example as a user runs it: what it refuses, and what it says, before any camera is asked.

#![allow(clippy::print_stderr)] // the skip message is the test's only way to speak

use std::{path::PathBuf, process::Command};

/// The probe as `cargo test` built it, or `None` when this run did not build examples.
fn probe() -> Option<PathBuf> {
    let deps = std::env::current_exe().ok()?;
    let exe = deps
        .parent()?
        .parent()?
        .join("examples")
        .join(format!("probe{}", std::env::consts::EXE_SUFFIX));
    if exe.is_file() {
        return Some(exe);
    }
    eprintln!("skipped: the probe example was not built; `make test-backend` builds it");
    None
}

/// Runs the probe with `args` and the camera named by `host` (none when empty) and returns whether it succeeded and
/// what it printed.
fn run(exe: &PathBuf, host: &str, args: &[&str]) -> (bool, String) {
    let mut command = Command::new(exe);
    command.args(args);
    for name in [
        "AX_DEVIL_TARGET_ADDR",
        "AX_DEVIL_TARGET_USER",
        "AX_DEVIL_TARGET_PASS",
        "AX_DEVIL_TARGET_PROTOCOL",
    ] {
        command.env_remove(name);
    }
    if !host.is_empty() {
        command
            .env("AX_DEVIL_TARGET_ADDR", host)
            .env("AX_DEVIL_TARGET_PROTOCOL", "http");
    }
    let output = command.output().unwrap();
    (
        output.status.success(),
        String::from_utf8_lossy(&output.stdout).into_owned(),
    )
}

#[test]
fn the_probe_refuses_a_missing_camera_and_a_wrong_command_line_with_a_message_on_stdout() {
    let Some(exe) = probe() else { return };

    let (ok, printed) = run(&exe, "", &["info"]);
    assert!(!ok && printed.starts_with("error: "), "{printed}");

    for (args, usage) in [
        (&[][..], "usage: probe info | snapshot FILE"),
        (&["nope"], "usage: probe info | snapshot FILE"),
        (&["snapshot"], "usage: probe snapshot FILE"),
        (&["stream", "soon"], "usage: probe stream [SECONDS]"),
        (
            &["mqtt", "soon"],
            "usage: probe mqtt [SECONDS] [DATA_SOURCE]",
        ),
    ] {
        let (ok, printed) = run(&exe, "127.0.0.1", args);
        assert!(!ok && printed.contains(usage), "{args:?}: {printed}");
    }
}
