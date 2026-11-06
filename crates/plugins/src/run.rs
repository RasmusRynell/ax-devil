//! One plugin process: start it, feed it lines, hand its lines to a handler, and end it on every path.

use std::{
    ffi::{OsStr, OsString},
    io::{self, Read, Write},
    ops::ControlFlow,
    process::{Child, ExitStatus},
    sync::mpsc::{self, Receiver, RecvTimeoutError, SyncSender},
    thread,
    time::{Duration, Instant},
};

use foundation::CancelToken;
use tempfile::TempDir;

use crate::{
    lines::{LineBuffer, TooLong},
    spawn,
    stderr::StderrTail,
};

/// The longest stdout line, in bytes.
const MAX_LINE: usize = 16 << 20;
/// How often the run looks at cancellation, the clock and the process.
const POLL: Duration = Duration::from_millis(100);
/// How long output may keep arriving after the process exited and its group was killed.
const GRACE: Duration = Duration::from_secs(2);
/// Bytes read from stdout at a time, and chunks buffered ahead of the line reader.
const CHUNK: usize = 64 << 10;
const READ_AHEAD: usize = 8;
/// Lines queued for stdin: the request and one record, the most a plugin that answers in turn has outstanding.
const WRITE_AHEAD: usize = 2;

/// What to run.
pub(crate) struct Spec<'a> {
    /// The plugin's id, for thread names and logs.
    pub id: &'a str,
    pub program: &'a OsStr,
    pub args: &'a [OsString],
    pub env: &'a [String],
    pub idle_timeout: Duration,
}

/// What the lines of a run are given to.
pub(crate) trait LineHandler {
    /// Handles one line, without its `\n`.
    fn line(&mut self, number: usize, bytes: &[u8]) -> Next;
}

/// What the run does after a line.
pub(crate) enum Next {
    Continue,
    /// The plugin finished the answer it owed: [`Process::pump`] returns [`Pumped::Done`]. Lines already read behind
    /// it wait for the next pump.
    Done,
    /// The handler cannot take more: the run ends as [`End::Stopped`].
    SinkFull,
    /// The plugin reported an error or broke the protocol in a way that stops the run.
    Stop(String),
}

/// How a run ended.
pub(crate) enum End {
    /// Exit 0 after the last line.
    Complete,
    /// Stopped early or failed: what the user reads, with the end of stderr when there is one.
    Incomplete(String),
    /// The caller cancelled, or the sink could not take more.
    Stopped,
}

/// Why the loop stopped before the plugin finished by itself.
pub(crate) enum Stop {
    Failed(String),
    Stopped,
}

/// Why [`Process::pump`] returned.
pub(crate) enum Pumped {
    /// The handler said [`Next::Done`].
    Done,
    /// The plugin closed its output and exited, and everything it wrote was read.
    Ended,
    /// The run must stop; give the [`Stop`] to [`Process::finish`].
    Stop(Stop),
}

enum Chunk {
    Data(Vec<u8>),
    Failed(String),
}

/// Runs `process` to its end on one request line, handing its lines to `handler`. The plugin's whole process group
/// is killed whenever the run ends, and its working folder removed.
pub(crate) fn run(
    mut process: Process,
    request: String,
    handler: &mut dyn LineHandler,
    cancel: &CancelToken,
) -> End {
    process.send(request);
    process.close_input();
    let stop = match process.pump(handler, cancel) {
        Pumped::Stop(stop) => Some(stop),
        Pumped::Done | Pumped::Ended => None,
    };
    process.finish(stop)
}

/// A started plugin: the process, its working folder and the threads that feed and drain its pipes.
pub(crate) struct Process {
    child: Child,
    /// The working folder, removed when the process is dropped.
    _folder: TempDir,
    /// Lines for the plugin's stdin; `None` once closed.
    input: Option<SyncSender<Vec<u8>>>,
    chunks: Receiver<Chunk>,
    stderr_ended: Receiver<()>,
    tail: StderrTail,
    lines: LineBuffer,
    idle_timeout: Duration,
    last_output: Instant,
    last_poll: Instant,
    exited: Option<Instant>,
    /// Stdout reached its end and its last line was handed over.
    ended: bool,
}

impl Process {
    /// Starts the plugin in a private working folder, in a process group of its own.
    pub(crate) fn start(spec: &Spec<'_>) -> Result<Self, String> {
        let thread_error =
            |error: io::Error| format!("Cannot start a thread for the plugin: {error}");
        let folder = tempfile::Builder::new()
            .prefix("ax-devil-plugin-")
            .tempdir()
            .map_err(|error| format!("Cannot create a working folder for the plugin: {error}"))?;
        let mut child = spawn::command(spec.program, spec.args, spec.env, folder.path())
            .spawn()
            .map_err(|error| {
                format!(
                    "Cannot start the plugin ({}): {error}",
                    spec.program.to_string_lossy()
                )
            })?;
        let (Some(mut stdin), Some(mut stdout), Some(stderr)) =
            (child.stdin.take(), child.stdout.take(), child.stderr.take())
        else {
            spawn::kill_group(&mut child);
            let _ = child.wait();
            return Err("The plugin has no pipes".to_owned());
        };
        let (input, to_write) = mpsc::sync_channel::<Vec<u8>>(WRITE_AHEAD);
        let tail = StderrTail::default();
        let (stderr_done, stderr_ended) = mpsc::channel();
        let (sender, chunks) = mpsc::sync_channel(READ_AHEAD);
        let now = Instant::now();
        // From here a failure drops `process`, which kills the group.
        let process = Self {
            child,
            _folder: folder,
            input: Some(input),
            chunks,
            stderr_ended,
            tail: tail.clone(),
            lines: LineBuffer::new(MAX_LINE),
            idle_timeout: spec.idle_timeout,
            last_output: now,
            last_poll: now,
            exited: None,
            ended: false,
        };
        thread::Builder::new()
            .name(format!("plugin-stdin-{}", spec.id))
            .spawn(move || {
                // A plugin that does not read its input closes the pipe; that is its business.
                for line in to_write {
                    if stdin.write_all(&line).is_err()
                        || stdin.write_all(b"\n").is_err()
                        || stdin.flush().is_err()
                    {
                        break;
                    }
                }
            })
            .map_err(thread_error)?;
        tail.drain(stderr, spec.id.to_owned(), stderr_done);
        thread::Builder::new()
            .name(format!("plugin-stdout-{}", spec.id))
            .spawn(move || {
                loop {
                    let mut chunk = vec![0_u8; CHUNK];
                    let message = match stdout.read(&mut chunk) {
                        Ok(0) => break,
                        Ok(read) => {
                            chunk.truncate(read);
                            Chunk::Data(chunk)
                        }
                        Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                        Err(error) => Chunk::Failed(error.to_string()),
                    };
                    let failed = matches!(message, Chunk::Failed(_));
                    if sender.send(message).is_err() || failed {
                        break;
                    }
                }
            })
            .map_err(thread_error)?;
        Ok(process)
    }

    /// Queues one line (without its newline) for the plugin's stdin. A writer thread delivers it, so a plugin that
    /// does not read cannot block the host. A line beyond [`WRITE_AHEAD`] waiting is dropped, which only a plugin that
    /// stopped reading can cause, and the idle timeout ends it. Does nothing after [`close_input`](Self::close_input).
    pub(crate) fn send(&mut self, line: String) {
        if let Some(input) = &self.input {
            let _ = input.try_send(line.into_bytes());
        }
    }

    /// Ends the plugin's stdin once the lines sent are written.
    pub(crate) fn close_input(&mut self) {
        self.input = None;
    }

    /// Hands the plugin's output to `handler` until it says [`Next::Done`], the plugin ends or the run must stop. The
    /// idle timeout counts from the call, so call it only while an answer is owed.
    pub(crate) fn pump(&mut self, handler: &mut dyn LineHandler, cancel: &CancelToken) -> Pumped {
        self.last_output = Instant::now();
        // Lines a previous `Done` left behind come first.
        if let Some(pumped) = self.feed(handler, &[]) {
            return pumped;
        }
        loop {
            if cancel.is_cancelled() {
                return Pumped::Stop(Stop::Stopped);
            }
            match self.exited {
                None if self.last_output.elapsed() >= self.idle_timeout => {
                    return Pumped::Stop(Stop::Failed(format!(
                        "The plugin produced nothing for {} s",
                        self.idle_timeout.as_secs()
                    )));
                }
                Some(at) if self.ended || at.elapsed() >= GRACE => return Pumped::Ended,
                _ => {}
            }
            if self.ended {
                thread::sleep(Duration::from_millis(10));
            } else {
                match self.chunks.recv_timeout(POLL) {
                    Ok(Chunk::Data(chunk)) => {
                        self.last_output = Instant::now();
                        if let Some(pumped) = self.feed(handler, &chunk) {
                            return pumped;
                        }
                    }
                    Ok(Chunk::Failed(error)) => {
                        return Pumped::Stop(Stop::Failed(format!(
                            "Reading the plugin's output failed: {error}"
                        )));
                    }
                    Err(RecvTimeoutError::Timeout) => {}
                    Err(RecvTimeoutError::Disconnected) => {
                        self.ended = true;
                        self.last_output = Instant::now();
                        let flow = self
                            .lines
                            .finish(|number, bytes| line_flow(handler, number, bytes));
                        if let Some(pumped) = pumped_for(flow) {
                            return pumped;
                        }
                    }
                }
            }
            if self.exited.is_none() && (self.ended || self.last_poll.elapsed() >= POLL) {
                self.last_poll = Instant::now();
                match self.child.try_wait() {
                    Ok(Some(_)) => {
                        self.exited = Some(self.last_poll);
                        spawn::kill_group(&mut self.child);
                    }
                    Ok(None) => {}
                    Err(error) => {
                        return Pumped::Stop(Stop::Failed(format!(
                            "Cannot wait for the plugin: {error}"
                        )));
                    }
                }
            }
        }
    }

    /// Gives a chunk of stdout to the line reader and the handler; `Some` when the pump must return.
    fn feed(&mut self, handler: &mut dyn LineHandler, chunk: &[u8]) -> Option<Pumped> {
        let number = self.lines.next_number();
        match self
            .lines
            .push(chunk, |number, bytes| line_flow(handler, number, bytes))
        {
            Err(TooLong) => Some(Pumped::Stop(Stop::Failed(format!(
                "Line {number} of the plugin's output is longer than 16 MiB"
            )))),
            Ok(flow) => pumped_for(flow),
        }
    }

    /// Ends the process whatever its state: kills its group, waits for it, drains stderr and removes the working
    /// folder. `stop` is what [`pump`](Self::pump) asked for, or `None` when the plugin ended by itself.
    pub(crate) fn finish(mut self, stop: Option<Stop>) -> End {
        // The group is killed on every path, also when the plugin ended by itself: a grandchild it left behind
        // must not outlive the lane.
        spawn::kill_group(&mut self.child);
        let status = self.child.wait();
        // The group is dead, so stderr ends at once unless something outside the group holds it.
        let _ = self.stderr_ended.recv_timeout(GRACE);
        match stop {
            Some(Stop::Stopped) => End::Stopped,
            Some(Stop::Failed(reason)) => End::Incomplete(with_tail(&reason, &self.tail)),
            None => match status {
                Ok(status) if status.success() => End::Complete,
                Ok(status) => End::Incomplete(with_tail(
                    &format!("The plugin stopped with {}", describe(status)),
                    &self.tail,
                )),
                Err(error) => End::Incomplete(format!("Cannot wait for the plugin: {error}")),
            },
        }
    }
}

impl Drop for Process {
    /// A process dropped without [`finish`](Process::finish), such as after a failed start, is still killed.
    fn drop(&mut self) {
        spawn::kill_group(&mut self.child);
        let _ = self.child.wait();
    }
}

fn line_flow(handler: &mut dyn LineHandler, number: usize, bytes: &[u8]) -> ControlFlow<Next> {
    match handler.line(number, bytes) {
        Next::Continue => ControlFlow::Continue(()),
        other => ControlFlow::Break(other),
    }
}

fn pumped_for(flow: ControlFlow<Next>) -> Option<Pumped> {
    match flow {
        ControlFlow::Break(Next::Done) => Some(Pumped::Done),
        ControlFlow::Break(Next::SinkFull) => Some(Pumped::Stop(Stop::Stopped)),
        ControlFlow::Break(Next::Stop(reason)) => Some(Pumped::Stop(Stop::Failed(reason))),
        ControlFlow::Break(Next::Continue) | ControlFlow::Continue(()) => None,
    }
}

fn describe(status: ExitStatus) -> String {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(signal) = status.signal() {
            return format!("signal {signal}");
        }
    }
    match status.code() {
        Some(code) => format!("exit code {code}"),
        None => "an unknown status".to_owned(),
    }
}

fn with_tail(reason: &str, tail: &StderrTail) -> String {
    let quote = tail.quote();
    if quote.is_empty() {
        reason.to_owned()
    } else {
        format!("{reason}:\n{quote}")
    }
}
