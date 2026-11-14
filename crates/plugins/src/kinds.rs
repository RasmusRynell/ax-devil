//! The handler kinds that are not file decoders or playlist resolvers: tasks, views and commands, and the lane
//! inputs a task or a `lanes` decoder takes.

use std::path::PathBuf;

use handler_api::{Bundle, Form, On, Place};

/// The lanes a `lanes` decoder or a task reads, by the key the program finds them under in its request.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct LaneInputs {
    /// `(key, label)` for each input lane, in manifest order; the label is what the user picks the lane for.
    pub lanes: Vec<(String, String)>,
    /// Whether the program is also given the video the lanes belong to.
    pub video: bool,
}

impl LaneInputs {
    /// Whether `bundle` holds exactly the lanes declared, each under its key, and the video if and only if the
    /// program takes one.
    pub fn check(&self, bundle: &Bundle) -> Result<(), String> {
        for (key, _) in &self.lanes {
            if !bundle.lanes.contains_key(key) {
                return Err(format!("A lane for '{key}' is needed"));
            }
        }
        for key in bundle.lanes.keys() {
            if !self.lanes.iter().any(|(declared, _)| declared == key) {
                return Err(format!("No lane '{key}' is read"));
            }
        }
        match (self.video, bundle.video.is_some()) {
            (true, false) => Err("The video is needed".to_owned()),
            (false, true) => Err("No video is taken".to_owned()),
            _ => Ok(()),
        }
    }
}

/// A task the plugin declares: reads lanes, writes whatever it likes elsewhere, and produces no Scenes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TaskHandler {
    /// The handler id.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// One sentence on what the task does, if the manifest gives one.
    pub description: Option<String>,
    /// The lanes and video it reads.
    pub inputs: LaneInputs,
    /// The options the task takes.
    pub options: Form,
}

/// A page the plugin ships, shown in a sandboxed frame.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ViewHandler {
    /// The handler id.
    pub id: String,
    /// What the user reads.
    pub title: String,
    /// The name of an icon, if the manifest gives one.
    pub icon: Option<String>,
    /// Where it is shown.
    pub place: Place,
    /// The page, a file inside the plugin's folder, relative to it.
    pub document: PathBuf,
}

/// A menu entry and shortcut that runs one of the plugin's own decoders, tasks or views.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CommandHandler {
    /// The handler id, unique within the plugin.
    pub id: String,
    /// What the user reads.
    pub title: String,
    /// What it applies to.
    pub on: On,
    /// The default key, such as `Ctrl+Alt+E`.
    pub shortcut: Option<String>,
    /// The id of the decoder (input `lanes`), task or view it runs.
    pub runs: String,
}
