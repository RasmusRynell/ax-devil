//! One plugin run: start the process, feed its lines to the sink, and end it on every path.

use std::{
    ffi::{OsStr, OsString},
    io::{self, Read, Write},
    ops::ControlFlow,
    process::{Child, ExitStatus},
    sync::mpsc::{self, RecvTimeoutError},
    thread,
    time::{Duration, Instant},
};

use foundation::CancelToken;
use handler_api::{Header, SceneSink};

use crate::{
    lines::{LineBuffer, TooLong},
    records::{Next, Records},
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

/// What to run.
pub(crate) struct Spec<'a> {
    /// The plugin's id, for thread names and logs.
    pub id: &'a str,
    pub program: &'a OsStr,
    pub args: &'a [OsString],
    pub env: &'a [String],
    /// The request line, without its newline.
    pub request: String,
    pub idle_timeout: Duration,
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

/// The end of a run and the header the plugin declared, if any.
pub(crate) struct Outcome {
    pub end: End,
    pub header: Option<Header>,
}

/// Why the loop stopped before the plugin finished by itself.
enum Stop {
    Failed(String),
    Stopped,
}

enum Chunk {
    Data(Vec<u8>),
    Failed(String),
}

/// Runs the plugin to its end, handing its lines to `sink`. The plugin's whole process group is killed
/// whenever the run ends, and its working folder removed.
pub(crate) fn run(spec: &Spec<'_>, sink: &mut dyn SceneSink, cancel: &CancelToken) -> Outcome {
    let mut records = Records::new(sink);
    let end = match supervise(spec, &mut records, cancel) {
        Ok(end) => end,
        Err(reason) => End::Incomplete(reason),
    };
    let header = records.finish();
    Outcome { end, header }
}

fn supervise(
    spec: &Spec<'_>,
    records: &mut Records<'_>,
    cancel: &CancelToken,
) -> Result<End, String> {
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
    let outcome = watch(spec, &mut child, records, cancel);
    // The group is killed on every path, also when the plugin ended by itself: a grandchild it left behind
    // must not outlive the lane.
    spawn::kill_group(&mut child);
    let status = child.wait();
    drop(folder);
    let (stop, tail) = outcome?;
    Ok(match stop {
        Some(Stop::Stopped) => End::Stopped,
        Some(Stop::Failed(reason)) => End::Incomplete(with_tail(&reason, &tail)),
        None => match status {
            Ok(status) if status.success() => End::Complete,
            Ok(status) => End::Incomplete(with_tail(
                &format!("The plugin stopped with {}", describe(status)),
                &tail,
            )),
            Err(error) => End::Incomplete(format!("Cannot wait for the plugin: {error}")),
        },
    })
}

/// Pumps the process until it finishes or must be stopped. Returns why it stopped early, if it did, and the end of
/// stderr.
fn watch(
    spec: &Spec<'_>,
    child: &mut Child,
    records: &mut Records<'_>,
    cancel: &CancelToken,
) -> Result<(Option<Stop>, StderrTail), String> {
    let thread_error = |error: io::Error| format!("Cannot start a thread for the plugin: {error}");
    let (Some(mut stdin), Some(mut stdout), Some(stderr)) =
        (child.stdin.take(), child.stdout.take(), child.stderr.take())
    else {
        return Err("The plugin has no pipes".to_owned());
    };

    let request = spec.request.clone();
    thread::Builder::new()
        .name(format!("plugin-stdin-{}", spec.id))
        .spawn(move || {
            // A plugin that does not read its request closes the pipe; that is its business.
            let _ = stdin.write_all(request.as_bytes());
            let _ = stdin.write_all(b"\n");
        })
        .map_err(thread_error)?;
    let tail = StderrTail::default();
    let (stderr_done, stderr_ended) = mpsc::channel();
    tail.drain(stderr, spec.id.to_owned(), stderr_done);
    let (sender, chunks) = mpsc::sync_channel(READ_AHEAD);
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

    let mut lines = LineBuffer::new(MAX_LINE);
    let mut last_output = Instant::now();
    let mut last_poll = last_output;
    let mut exited: Option<Instant> = None;
    let mut ended = false;
    let mut stop = None;
    while stop.is_none() {
        if cancel.is_cancelled() {
            stop = Some(Stop::Stopped);
            break;
        }
        match exited {
            None if last_output.elapsed() >= spec.idle_timeout => {
                stop = Some(Stop::Failed(format!(
                    "The plugin produced nothing for {} s",
                    spec.idle_timeout.as_secs()
                )));
                break;
            }
            Some(at) if ended || at.elapsed() >= GRACE => break,
            _ => {}
        }
        if ended {
            thread::sleep(Duration::from_millis(10));
        } else {
            match chunks.recv_timeout(POLL) {
                Ok(Chunk::Data(chunk)) => {
                    last_output = Instant::now();
                    stop = feed(&mut lines, records, &chunk);
                }
                Ok(Chunk::Failed(error)) => {
                    stop = Some(Stop::Failed(format!(
                        "Reading the plugin's output failed: {error}"
                    )));
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    ended = true;
                    last_output = Instant::now();
                    let flow = lines.finish(|number, bytes| match records.line(number, bytes) {
                        Next::Continue => ControlFlow::Continue(()),
                        other => ControlFlow::Break(other),
                    });
                    stop = stop_for(flow);
                }
            }
        }
        if exited.is_none() && (ended || last_poll.elapsed() >= POLL) {
            last_poll = Instant::now();
            match child.try_wait() {
                Ok(Some(_)) => {
                    exited = Some(last_poll);
                    spawn::kill_group(child);
                }
                Ok(None) => {}
                Err(error) => {
                    stop = Some(Stop::Failed(format!("Cannot wait for the plugin: {error}")));
                }
            }
        }
    }
    if stop.is_some() {
        spawn::kill_group(child);
    }
    // The group is dead, so stderr ends at once unless something outside the group holds it.
    let _ = stderr_ended.recv_timeout(GRACE);
    Ok((stop, tail))
}

/// Gives a chunk of stdout to the line reader and the records; `Some` when the run must stop.
fn feed(lines: &mut LineBuffer, records: &mut Records<'_>, chunk: &[u8]) -> Option<Stop> {
    let number = lines.next_number();
    match lines.push(chunk, |number, bytes| match records.line(number, bytes) {
        Next::Continue => ControlFlow::Continue(()),
        other => ControlFlow::Break(other),
    }) {
        Err(TooLong) => Some(Stop::Failed(format!(
            "Line {number} of the plugin's output is longer than 16 MiB"
        ))),
        Ok(flow) => stop_for(flow),
    }
}

fn stop_for(flow: ControlFlow<Next>) -> Option<Stop> {
    match flow {
        ControlFlow::Break(Next::SinkFull) => Some(Stop::Stopped),
        ControlFlow::Break(Next::Stop(reason)) => Some(Stop::Failed(reason)),
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
