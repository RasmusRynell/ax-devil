//! Reading the task, view and command handlers and the lane inputs of a task or a `lanes` decoder, and checking what
//! they refer to.

use std::{
    collections::HashSet,
    path::{Path, PathBuf},
};

use serde_json::Value;

use super::Fields;
use crate::{
    CommandHandler, DecoderInput, Handler, LaneInputs, ManifestError, On, Place, SkippedHandler,
    TaskHandler, ViewHandler, manifest::is_inside,
};

impl Fields<'_> {
    /// The `lanes` list and optional `video` flag.
    pub(super) fn lane_inputs(&mut self) -> Result<LaneInputs, ManifestError> {
        let Value::Array(items) = self.take("lanes")? else {
            return Err(self.error("lanes", "must be a list"));
        };
        if items.is_empty() {
            return Err(self.error("lanes", "must name at least one lane"));
        }
        let mut lanes: Vec<(String, String)> = Vec::with_capacity(items.len());
        for (index, item) in items.into_iter().enumerate() {
            let at = self.path(&format!("lanes[{index}]"));
            let Value::Object(mut map) = item else {
                return Err(ManifestError::new(at, "must be an object"));
            };
            let mut read = Fields::new(&mut map, format!("{at}."));
            let key = read.key()?;
            let label = read.non_empty("label")?;
            read.finish()?;
            if lanes.iter().any(|(other, _)| *other == key) {
                return Err(ManifestError::new(
                    format!("{at}.key"),
                    format!("'{key}' is declared twice"),
                ));
            }
            lanes.push((key, label));
        }
        Ok(LaneInputs {
            lanes,
            video: self.optional_bool("video")?,
        })
    }

    pub(super) fn task(&mut self) -> Result<TaskHandler, ManifestError> {
        Ok(TaskHandler {
            id: self.id("id")?,
            name: self.non_empty("name")?,
            description: self.optional_non_empty("description")?,
            inputs: self.lane_inputs()?,
            options: self.form("options", false)?,
        })
    }

    pub(super) fn view(&mut self) -> Result<ViewHandler, ManifestError> {
        let id = self.id("id")?;
        let title = self.non_empty("title")?;
        let icon = self.optional_non_empty("icon")?;
        let place = match self.string("place")?.as_str() {
            "sidebar" => Place::Sidebar,
            "video" => Place::Video,
            other => {
                return Err(self.error("place", format!("'{other}' is not sidebar or video")));
            }
        };
        let document = PathBuf::from(self.non_empty("document")?);
        if !is_inside(&document) {
            return Err(self.error("document", "must be a path inside the plugin's folder"));
        }
        Ok(ViewHandler {
            id,
            title,
            icon,
            place,
            document,
        })
    }

    pub(super) fn command(&mut self) -> Result<CommandHandler, ManifestError> {
        let id = self.id("id")?;
        let title = self.non_empty("title")?;
        let on = match self.string("on")?.as_str() {
            "app" => On::App,
            "item" => On::Item,
            "lane" => On::Lane,
            other => {
                return Err(self.error("on", format!("'{other}' is not app, item or lane")));
            }
        };
        Ok(CommandHandler {
            id,
            title,
            on,
            shortcut: self.optional_non_empty("shortcut")?,
            runs: self.id("runs")?,
        })
    }
}

/// Sets aside the handlers that refer to something that is not there: a view whose document is not a file of the
/// plugin, and a command that runs nothing it may run (another plugin's handler, a resolver, a `path` decoder, or a
/// view set aside). The rest keep their order.
pub(super) fn resolve(
    handlers: Vec<Handler>,
    folder: &Path,
) -> (Vec<Handler>, Vec<SkippedHandler>) {
    let mut skipped = Vec::new();
    let mut set_aside = |handler: &Handler, reason: String| {
        skipped.push(SkippedHandler {
            kind: handler.kind().to_owned(),
            id: Some(handler.id().to_owned()),
            reason,
        });
    };
    let mut kept = Vec::with_capacity(handlers.len());
    for handler in handlers {
        match &handler {
            Handler::View(view) if !folder.join(&view.document).is_file() => {
                let reason = format!(
                    "document '{}' is not a file in the plugin's folder",
                    view.document.display()
                );
                set_aside(&handler, reason);
            }
            _ => kept.push(handler),
        }
    }
    let runnable: HashSet<String> = kept
        .iter()
        .filter(|handler| match handler {
            Handler::Decoder(decoder) => decoder.input == DecoderInput::Lanes,
            Handler::Task(_) | Handler::View(_) => true,
            Handler::PlaylistResolver(_) | Handler::Command(_) => false,
        })
        .map(|handler| handler.id().to_owned())
        .collect();
    let (handlers, unresolved): (Vec<_>, Vec<_>) = kept.into_iter().partition(|handler| {
        !matches!(handler, Handler::Command(command) if !runnable.contains(&command.runs))
    });
    for handler in &unresolved {
        if let Handler::Command(command) = handler {
            set_aside(
                handler,
                format!(
                    "runs '{}', which is not a lanes decoder, task or view of this plugin",
                    command.runs
                ),
            );
        }
    }
    (handlers, skipped)
}
