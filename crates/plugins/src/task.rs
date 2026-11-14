//! A plugin's task handler: one run of the plugin's program over a bundle of lanes, reporting progress.

use std::sync::Arc;

use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use handler_api::{Bundle, Values};
use serde::Deserialize;
use serde_json::json;

use crate::{
    PROTOCOL,
    kinds::TaskHandler,
    lanes,
    program::Program,
    run::{End, LineHandler, Next},
    wire::{ErrorIn, WarningIn, cut, json_reason, load_warning},
};

/// How far a task is, as it last said.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TaskProgress {
    /// Units finished.
    pub done: Option<u64>,
    /// Units in all, when the task knows.
    pub total: Option<u64>,
    /// What it is doing now.
    pub text: Option<String>,
}

/// A task that runs an installed plugin's process.
pub struct PluginTask {
    program: Arc<Program>,
    spec: TaskHandler,
}

impl PluginTask {
    /// The task for `handler`, which `program`'s plugin declares.
    pub fn new(program: Arc<Program>, handler: &TaskHandler) -> Self {
        Self {
            program,
            spec: handler.clone(),
        }
    }

    /// What the manifest declares about the task.
    pub fn spec(&self) -> &TaskHandler {
        &self.spec
    }

    /// Runs the task over the lanes of `bundle`, calling `on_progress` for each `progress` line. It succeeds when
    /// the plugin exits 0 without an `error` line; the plugin's `error` reason, or why it ended otherwise, is the
    /// failure.
    pub fn run(
        &self,
        bundle: &Bundle,
        options: &Values,
        on_progress: &mut dyn FnMut(TaskProgress),
        cancel: &CancelToken,
    ) -> Result<(), Interrupt<UserError>> {
        cancel.check()?;
        let failed =
            |reason: String| Interrupt::Failed(UserError::new(ErrorCode::PluginInvalid, reason));
        self.spec.inputs.check(bundle).map_err(|reason| {
            Interrupt::Failed(UserError::new(ErrorCode::InvalidRequest, reason))
        })?;
        self.program
            .code_hash()
            .map_err(|error| failed(format!("The plugin's files cannot be read: {error}")))?;
        let request = json!({
            "protocol": PROTOCOL,
            "op": "task",
            "handler": self.spec.id,
            "input": lanes::input(bundle).map_err(failed)?,
            "options": options.declared(),
            "settings": self.program.settings(),
        });
        let mut lines = TaskLines {
            on_progress,
            error: None,
        };
        let end = self
            .program
            .run(&self.spec.id, &request, &mut lines, cancel);
        if let Some(reason) = lines.error {
            return Err(failed(reason));
        }
        match end {
            End::Complete => Ok(()),
            End::Incomplete(reason) => Err(failed(reason)),
            End::Stopped => Err(Interrupt::Cancelled),
        }
    }
}

/// One line: an object of exactly one of these keys.
#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum LineIn {
    Progress(ProgressIn),
    Warning(WarningIn),
    Error(ErrorIn),
    Done(DoneIn),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProgressIn {
    done: Option<u64>,
    total: Option<u64>,
    text: Option<String>,
}

/// `{"done":{}}`: the plugin says it finished; the exit code is what decides.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DoneIn {}

/// The lines of one task run.
struct TaskLines<'a> {
    on_progress: &'a mut dyn FnMut(TaskProgress),
    /// The plugin's `error` line, which ends the run.
    error: Option<String>,
}

impl LineHandler for TaskLines<'_> {
    fn line(&mut self, number: usize, bytes: &[u8]) -> Next {
        let bytes = bytes.trim_ascii();
        if bytes.is_empty() {
            return Next::Continue;
        }
        let line = std::str::from_utf8(bytes)
            .map_err(|_| "is not valid UTF-8".to_owned())
            .and_then(|text| {
                serde_json::from_str(text).map_err(|error| {
                    // Valid JSON that is not an object of one key reads as a syntax error to the enum.
                    if error.is_syntax() && serde_json::from_str::<serde_json::Value>(text).is_ok()
                    {
                        "needs exactly one of progress, warning, error or done".to_owned()
                    } else {
                        json_reason(&error)
                    }
                })
            });
        let line: LineIn = match line {
            Ok(line) => line,
            Err(reason) => {
                return Next::Stop(format!("Line {number} of the plugin's output {reason}"));
            }
        };
        match line {
            LineIn::Progress(progress) => (self.on_progress)(TaskProgress {
                done: progress.done,
                total: progress.total,
                text: progress.text.as_deref().map(cut),
            }),
            LineIn::Warning(warning) => {
                let warning = load_warning(warning);
                tracing::warn!(
                    "Task warning: {} ({})",
                    warning.problem,
                    warning.reasons.join("; ")
                );
            }
            LineIn::Error(error) => {
                let reason = cut(&error.reason);
                self.error = Some(reason.clone());
                return Next::Stop(reason);
            }
            LineIn::Done(DoneIn {}) => {}
        }
        Next::Continue
    }
}
