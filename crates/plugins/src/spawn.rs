//! Starting a plugin in a process group of its own, with an environment that carries no credentials, and
//! killing that group.

use std::{
    ffi::{OsStr, OsString},
    path::Path,
    process::{Child, Command, Stdio},
};

/// The only variables a plugin inherits from the app, each only when set. The manifest's `env` adds names;
/// nothing else passes, so no `AX_DEVIL_*`, server password, or cloud or proxy credential reaches it.
const PASSED_ENV: &[&str] = &[
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    #[cfg(windows)]
    "SYSTEMROOT",
];

/// The plugin process: `program args...`, working in `folder` (also its `TMPDIR`), with stdin, stdout
/// and stderr piped.
pub(crate) fn command(
    program: &OsStr,
    args: &[OsString],
    extra_env: &[String],
    folder: &Path,
) -> Command {
    let mut process = Command::new(program);
    process
        .args(args)
        .env_clear()
        .current_dir(folder)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for name in PASSED_ENV
        .iter()
        .copied()
        .chain(extra_env.iter().map(String::as_str))
    {
        if let Some(value) = std::env::var_os(name) {
            process.env(name, value);
        }
    }
    process.env("TMPDIR", folder);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // The child leads a group of its own, so the kill below reaches the processes it started too.
        process.process_group(0);
    }
    process
}

/// Kills the plugin and everything it started. Safe to call after the child exited or was killed.
pub(crate) fn kill_group(child: &mut Child) {
    #[cfg(unix)]
    {
        use rustix::process::{Pid, Signal, kill_process_group};
        // The group id is the child's pid (`process_group(0)`). An error means the group is already gone.
        let _ = kill_process_group(Pid::from_child(child), Signal::KILL);
    }
    // Without process groups only the child itself can be reached.
    let _ = child.kill();
}
