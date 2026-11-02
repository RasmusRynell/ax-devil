//! The process that runs the render page: started once per attempt, killed when dropped.

use super::{Graphics, RenderError};
use crate::desktop;
use std::{
    collections::VecDeque,
    io::{BufRead, BufReader, Write},
    path::Path,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};
use tempfile::TempDir;

/// How many of the page process's last output lines an error quotes.
const TAIL_LINES: usize = 12;
/// How long a page that was asked to quit gets, before it is killed, and how long its scratch folder is retried.
const QUIT_GRACE: Duration = Duration::from_secs(3);

/// What the page needs to find the backend: the loopback port and the secret its `Hello` repeats.
pub(super) struct Handshake {
    pub port: u16,
    pub secret: String,
}

/// Starts a page process that will connect to the backend described by the handshake. The first argument is
/// the graphics to start it with.
pub(super) type Spawn = Box<dyn FnMut(Graphics, &Handshake) -> Result<Page, RenderError> + Send>;

/// A running render page process. Dropping it kills the process and removes its scratch folder. On unix the process
/// leads a process group of its own (see [`own_group`]), and the kill reaches the group: the `electron` in
/// `node_modules/.bin` is a Node wrapper that starts the real Electron, which starts its helpers.
pub(super) struct Page {
    child: Child,
    tail: Arc<Mutex<VecDeque<String>>>,
    // Removed after the process is dead.
    scratch: Option<TempDir>,
}

impl Page {
    /// Takes over `child`, whose error output (when piped) is read in the background for the error messages.
    pub(super) fn new(mut child: Child, scratch: Option<TempDir>) -> Self {
        let tail = Arc::new(Mutex::new(VecDeque::new()));
        if let Some(stderr) = child.stderr.take() {
            let tail = Arc::clone(&tail);
            thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    tracing::debug!("render page: {line}");
                    if let Ok(mut lines) = tail.lock() {
                        if lines.len() == TAIL_LINES {
                            lines.pop_front();
                        }
                        lines.push_back(line);
                    }
                }
            });
        }
        Self {
            child,
            tail,
            scratch,
        }
    }

    /// Ends the process and removes its scratch folder; dropping the page does too. A process that reads its
    /// handshake from stdin is asked to quit by closing it, which lets Electron stop its own helper processes; the
    /// others, and one that is hung, are killed. Whatever is left of the process group is killed either way.
    pub(super) fn kill(&mut self) {
        if self.child.stdin.take().is_some() {
            let deadline = Instant::now() + QUIT_GRACE;
            while matches!(self.child.try_wait(), Ok(None)) && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(10));
            }
        }
        kill_group(&mut self.child);
        let _ = self.child.wait();
        if let Some(scratch) = self.scratch.take() {
            remove_when_quiet(scratch);
        }
    }

    /// How the process ended, if it has, with the last of what it said.
    pub(super) fn exited(&mut self) -> Option<String> {
        let status = self.child.try_wait().ok().flatten()?;
        Some(format!(
            "the render page process ended ({status}){}",
            self.said()
        ))
    }

    /// The last lines the process wrote, as the end of an error message; empty when it wrote none.
    pub(super) fn said(&self) -> String {
        let lines = self
            .tail
            .lock()
            .map(|lines| lines.iter().cloned().collect::<Vec<_>>());
        match lines {
            Ok(lines) if !lines.is_empty() => format!(": {}", lines.join(" | ")),
            _ => String::new(),
        }
    }
}

/// Makes `command`'s process lead a group of its own, so [`kill_group`] reaches the processes it starts too.
pub(super) fn own_group(command: &mut Command) -> &mut Command {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    command
}

/// Kills the process and, on unix, every process left in its group. Safe after the process exited.
fn kill_group(child: &mut Child) {
    #[cfg(unix)]
    {
        use rustix::process::{Pid, Signal, kill_process_group};
        // The group id is the child's pid (`own_group`). An error means the group is already gone, or the process
        // does not lead one.
        let _ = kill_process_group(Pid::from_child(child), Signal::KILL);
    }
    let _ = child.kill();
}

/// Removes the folder. Electron's helper processes can still be writing into it for a moment after the main process
/// is gone, which makes the first attempts fail.
fn remove_when_quiet(folder: TempDir) {
    let path = folder.keep();
    let deadline = Instant::now() + QUIT_GRACE;
    while std::fs::remove_dir_all(&path).is_err() && path.exists() {
        if Instant::now() >= deadline {
            tracing::warn!("Cannot remove the render page's folder {}", path.display());
            return;
        }
        thread::sleep(Duration::from_millis(20));
    }
}

impl Drop for Page {
    fn drop(&mut self) {
        self.kill();
    }
}

/// The spawner of the real page: Electron from `desktop`, running `render.cjs` offscreen with no display.
pub(super) fn electron(desktop: &Path) -> Result<Spawn, RenderError> {
    let electron = desktop::electron(desktop).map_err(RenderError::new)?;
    desktop::render_page(desktop).map_err(RenderError::new)?;
    let script = desktop.join("render.cjs");
    Ok(Box::new(move |graphics, handshake| {
        let scratch = tempfile::Builder::new()
            .prefix("ax-devil-render-")
            .tempdir()
            .map_err(|error| {
                RenderError::new(format!("Cannot make a folder for the render page: {error}"))
            })?;
        let mut command = Command::new(&electron);
        // Shells hosted in an Electron app export this, which would start Electron as plain Node and crash it at once.
        own_group(&mut command)
            .env_remove("ELECTRON_RUN_AS_NODE")
            .arg(&script)
            .arg("--ozone-platform=headless")
            .args(graphics.flags())
            .arg(format!("--user-data-dir={}", scratch.path().display()))
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        let mut child = command.spawn().map_err(|error| {
            RenderError::new(format!(
                "Cannot start Electron for the render page: {error}"
            ))
        })?;
        // The port and secret travel on stdin, not the command line, which other users can read. The pipe stays
        // open: the page exits when it closes, so it does not outlive a backend that died.
        let line = format!(
            "{{\"port\":{},\"secret\":\"{}\"}}\n",
            handshake.port, handshake.secret
        );
        let sent = child
            .stdin
            .as_mut()
            .map(|stdin| stdin.write_all(line.as_bytes()));
        let page = Page::new(child, Some(scratch));
        match sent {
            Some(Ok(())) => Ok(page),
            _ => Err(RenderError::new("Cannot talk to the render page process")),
        }
    }))
}
