//! The lines of a scan or a resolve, collected and checked as they arrive (the format in the write-plugin skill).
//!
//! What the lines add up to is used only when the plugin then exits 0, so a plugin that fails halfway leaves
//! nothing in a playlist.

use std::{collections::HashSet, path::Path};

use foundation::{FrameRate, Warning};
use handler_api::{
    Choice, ImageSequence, OverlayRef, ResolvedEntry, ResolvedPlaylist, Settings, VideoRef,
};
use serde::Deserialize;

use crate::{
    run::{LineHandler, Next},
    wire::{ErrorIn, WarningIn, cut, json_reason, load_warning},
};

/// Which operation the lines answer, and so which of them are allowed.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Op {
    Scan,
    Resolve,
}

/// One line: exactly one of these keys.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LineIn {
    warning: Option<WarningIn>,
    error: Option<ErrorIn>,
    choice: Option<ChoiceIn>,
    playlist: Option<PlaylistIn>,
    entry: Option<EntryIn>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ChoiceIn {
    value: String,
    label: Option<String>,
    #[serde(default)]
    detail: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PlaylistIn {
    name: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EntryIn {
    name: String,
    video: VideoIn,
    #[serde(default)]
    overlays: Vec<OverlayIn>,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum VideoIn {
    File(String),
    ImageSequence(SequenceIn),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SequenceIn {
    pattern: String,
    start_number: u32,
    frame_rate: RateIn,
    width: u32,
    height: u32,
    frame_count: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RateIn {
    num: u32,
    den: u32,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OverlayIn {
    label: String,
    slot: String,
    path: String,
    handler: String,
    #[serde(default)]
    options: Settings,
}

/// An `error` line: what the plugin says went wrong, and the setting or path it is about.
pub(crate) struct Reported {
    pub reason: String,
    pub field: Option<String>,
    pub path: Option<String>,
}

/// What a scan or resolve printed so far.
pub(crate) struct Collected {
    op: Op,
    /// The plugin's `error` line, which ends the run.
    pub error: Option<Reported>,
    choices: Vec<Choice>,
    name: Option<String>,
    entries: Vec<ResolvedEntry>,
    names: HashSet<String>,
    warnings: Vec<Warning>,
}

impl Collected {
    pub(crate) fn new(op: Op) -> Self {
        Self {
            op,
            error: None,
            choices: Vec::new(),
            name: None,
            entries: Vec::new(),
            names: HashSet::new(),
            warnings: Vec::new(),
        }
    }

    pub(crate) fn into_choices(self) -> Vec<Choice> {
        self.choices
    }

    /// The playlist, named `fallback` when the plugin named none.
    pub(crate) fn into_playlist(self, fallback: &str) -> ResolvedPlaylist {
        ResolvedPlaylist {
            display_name: self.name.unwrap_or_else(|| fallback.to_owned()),
            entries: self.entries,
            warnings: self.warnings,
        }
    }

    fn accept(&mut self, line: LineIn) -> Result<(), String> {
        let wrong = |key: &str, op: &str| format!("'{key}' does not belong in a {op}");
        match (line, self.op) {
            (
                LineIn {
                    warning: Some(warning),
                    error: None,
                    choice: None,
                    playlist: None,
                    entry: None,
                },
                _,
            ) => self.warnings.push(load_warning(warning)),
            (
                LineIn {
                    warning: None,
                    error: Some(error),
                    choice: None,
                    playlist: None,
                    entry: None,
                },
                _,
            ) => {
                self.error = Some(Reported {
                    reason: cut(&error.reason),
                    field: error.field.filter(|field| !field.is_empty()),
                    path: error.path.filter(|path| !path.is_empty()),
                });
            }
            (
                LineIn {
                    warning: None,
                    error: None,
                    choice: Some(choice),
                    playlist: None,
                    entry: None,
                },
                Op::Scan,
            ) => self.choices.push(Choice {
                label: choice.label.unwrap_or_else(|| choice.value.clone()),
                value: choice.value,
                detail: choice.detail,
            }),
            (
                LineIn {
                    warning: None,
                    error: None,
                    choice: None,
                    playlist: Some(playlist),
                    entry: None,
                },
                Op::Resolve,
            ) => {
                if self.name.is_some() || !self.entries.is_empty() {
                    return Err("a playlist must come once, before the first entry".to_owned());
                }
                self.name = Some(cut(&playlist.name));
            }
            (
                LineIn {
                    warning: None,
                    error: None,
                    choice: None,
                    playlist: None,
                    entry: Some(entry),
                },
                Op::Resolve,
            ) => self.add_entry(entry)?,
            (
                LineIn {
                    choice: Some(_), ..
                },
                Op::Resolve,
            ) => {
                return Err(wrong("choice", "resolve"));
            }
            (
                LineIn {
                    playlist: Some(_), ..
                },
                Op::Scan,
            ) => {
                return Err(wrong("playlist", "scan"));
            }
            (LineIn { entry: Some(_), .. }, Op::Scan) => return Err(wrong("entry", "scan")),
            _ => {
                return Err(
                    "needs exactly one of warning, error, choice, playlist or entry".to_owned(),
                );
            }
        }
        Ok(())
    }

    fn add_entry(&mut self, entry: EntryIn) -> Result<(), String> {
        if !self.names.insert(entry.name.clone()) {
            return Err(format!("the entry name '{}' is used twice", entry.name));
        }
        let video = match entry.video {
            VideoIn::File(file) => VideoRef::File(absolute("the video", file)?),
            VideoIn::ImageSequence(sequence) => VideoRef::ImageSequence(ImageSequence {
                pattern: absolute("the image pattern", sequence.pattern)?,
                start_number: sequence.start_number,
                frame_rate: FrameRate::new(sequence.frame_rate.num, sequence.frame_rate.den)
                    .ok_or("the frame rate needs a positive num and den")?,
                width: sequence.width,
                height: sequence.height,
                frame_count: sequence.frame_count,
            }),
        };
        let mut slots = HashSet::new();
        let mut labels = HashSet::new();
        let mut overlays = Vec::with_capacity(entry.overlays.len());
        for overlay in entry.overlays {
            if !slots.insert(overlay.slot.clone()) {
                return Err(format!(
                    "entry '{}' has the slot '{}' twice",
                    entry.name, overlay.slot
                ));
            }
            if !labels.insert(overlay.label.clone()) {
                return Err(format!(
                    "entry '{}' has the label '{}' twice",
                    entry.name, overlay.label
                ));
            }
            overlays.push(OverlayRef {
                label: overlay.label,
                slot: overlay.slot,
                path: absolute("the overlay path", overlay.path)?,
                handler: overlay.handler,
                options: overlay.options,
            });
        }
        self.entries.push(ResolvedEntry {
            name: entry.name,
            video,
            overlays,
        });
        Ok(())
    }
}

fn absolute(what: &str, path: String) -> Result<std::path::PathBuf, String> {
    if Path::new(&path).is_absolute() {
        Ok(path.into())
    } else {
        Err(format!("{what} '{path}' is not an absolute path"))
    }
}

impl LineHandler for Collected {
    fn line(&mut self, number: usize, bytes: &[u8]) -> Next {
        let bytes = bytes.trim_ascii();
        if bytes.is_empty() {
            return Next::Continue;
        }
        let parsed = std::str::from_utf8(bytes)
            .map_err(|_| "is not valid UTF-8".to_owned())
            .and_then(|text| serde_json::from_str(text).map_err(|error| json_reason(&error)))
            .and_then(|line| self.accept(line));
        match (parsed, &self.error) {
            (Err(reason), _) => Next::Stop(format!("line {number}: {reason}")),
            (Ok(()), Some(error)) => Next::Stop(error.reason.clone()),
            (Ok(()), None) => Next::Continue,
        }
    }
}
