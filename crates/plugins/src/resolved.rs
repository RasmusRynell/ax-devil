//! The lines of a scan or a resolve, collected and checked as they arrive (the format in the write-plugin skill).
//!
//! What the lines add up to is used only when the plugin then exits 0, so a plugin that fails halfway leaves
//! nothing in a playlist.

use std::{
    collections::HashSet,
    path::{Path, PathBuf},
};

use foundation::{FrameRate, Warning};
use handler_api::{
    Choice, ImageSequence, OverlayRef, ResolvedEntry, ResolvedPlaylist, ResolvedPlaylists,
    Settings, VideoRef,
};
use serde::Deserialize;

use crate::{
    budget::{Budget, WATCH_BYTES},
    run::{LineHandler, Next},
    wire::{ErrorIn, WarningIn, cut, json_reason, load_warning},
};

/// The most warnings a scan or resolve keeps, as the scene store does for a decode; later ones are counted, not kept.
const MAX_WARNINGS: usize = 64;
/// The most choices a scan may print.
const MAX_CHOICES: usize = 50_000;
/// The most entries and playlist lines a resolve may print.
const MAX_ENTRIES: usize = 50_000;

/// The largest count a playlist may state: what the frontend reads without losing digits (JavaScript's 2^53 - 1).
const MAX_COUNT: u64 = (1 << 53) - 1;

/// Which operation the lines answer, and so which of them are allowed.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Op {
    Scan,
    Resolve,
    /// The files to watch before a resolve.
    Watch,
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
    watch: Option<String>,
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
    #[serde(default)]
    matches: Option<u64>,
    #[serde(default)]
    sequence_groups: Option<u64>,
    #[serde(default)]
    metadata: Settings,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EntryIn {
    name: String,
    video: VideoIn,
    #[serde(default)]
    overlays: Vec<OverlayIn>,
    #[serde(default = "yes")]
    considered: bool,
    #[serde(default)]
    metadata: Settings,
}

fn yes() -> bool {
    true
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
    #[serde(default = "yes")]
    considered: bool,
    #[serde(default)]
    metadata: Settings,
}

/// An `error` line: what the plugin says went wrong, and the setting or path it is about.
pub(crate) struct Reported {
    pub reason: String,
    pub field: Option<String>,
    pub path: Option<String>,
}

/// A playlist as printed so far.
struct Printed {
    name: Option<String>,
    matches: Option<u64>,
    sequence_groups: Option<u64>,
    metadata: Settings,
    entries: Vec<ResolvedEntry>,
}

impl Printed {
    fn named(name: Option<String>) -> Self {
        Self {
            name,
            matches: None,
            sequence_groups: None,
            metadata: Settings::new(),
            entries: Vec::new(),
        }
    }
}

/// What a scan or resolve printed so far.
pub(crate) struct Collected {
    op: Op,
    /// The plugin's `error` line, which ends the run.
    pub error: Option<Reported>,
    choices: Vec<Choice>,
    /// The playlists so far, the last one still taking entries; entries before any `playlist` line fill one with no
    /// name yet.
    playlists: Vec<Printed>,
    /// The files a `watch` operation named, and the bytes of their paths.
    watched: Vec<PathBuf>,
    watched_bytes: usize,
    /// Entries and playlist lines so far.
    printed: usize,
    warnings: Vec<Warning>,
    /// The metadata kept so far.
    budget: Budget,
    /// Warnings printed after `MAX_WARNINGS` were kept.
    suppressed: usize,
}

impl Collected {
    pub(crate) fn new(op: Op) -> Self {
        Self {
            op,
            error: None,
            choices: Vec::new(),
            playlists: Vec::new(),
            watched: Vec::new(),
            watched_bytes: 0,
            printed: 0,
            warnings: Vec::new(),
            budget: Budget::default(),
            suppressed: 0,
        }
    }

    pub(crate) fn into_choices(self) -> Vec<Choice> {
        self.choices
    }

    pub(crate) fn into_watched(self) -> Vec<PathBuf> {
        self.watched
    }

    /// The playlists in the order they were printed; entries printed before any playlist line form one named
    /// `fallback`.
    pub(crate) fn into_playlists(mut self, fallback: &str) -> ResolvedPlaylists {
        if self.suppressed > 0 {
            self.warnings.push(Warning {
                problem: format!("{} more warnings suppressed", self.suppressed),
                reasons: Vec::new(),
                consequence: "They are not listed.".to_owned(),
                more_reasons: false,
            });
        }
        ResolvedPlaylists {
            playlists: self
                .playlists
                .into_iter()
                .map(|printed| ResolvedPlaylist {
                    name: printed.name.unwrap_or_else(|| fallback.to_owned()),
                    entries: printed.entries,
                    matches: printed.matches,
                    sequence_groups: printed.sequence_groups,
                    metadata: printed.metadata,
                })
                .collect(),
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
                    watch: None,
                },
                _,
            ) => {
                if self.warnings.len() < MAX_WARNINGS {
                    self.warnings.push(load_warning(warning));
                } else {
                    self.suppressed += 1;
                }
            }
            (
                LineIn {
                    warning: None,
                    error: Some(error),
                    choice: None,
                    playlist: None,
                    entry: None,
                    watch: None,
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
                    watch: None,
                },
                Op::Scan,
            ) => {
                check_room(self.choices.len(), MAX_CHOICES, "choices")?;
                self.choices.push(Choice {
                    label: choice.label.unwrap_or_else(|| choice.value.clone()),
                    value: choice.value,
                    detail: choice.detail,
                });
            }
            (
                LineIn {
                    warning: None,
                    error: None,
                    choice: None,
                    playlist: Some(playlist),
                    entry: None,
                    watch: None,
                },
                Op::Resolve,
            ) => {
                check_room(self.printed, MAX_ENTRIES, "entries and playlists")?;
                if playlist.name.is_empty() {
                    return Err("the playlist name is empty".to_owned());
                }
                for (key, count) in [
                    ("matches", playlist.matches),
                    ("sequence_groups", playlist.sequence_groups),
                ] {
                    if count.is_some_and(|count| count > MAX_COUNT) {
                        return Err(format!("'{key}' is more than {MAX_COUNT}"));
                    }
                }
                self.budget.take(&playlist.metadata)?;
                self.printed += 1;
                self.playlists.push(Printed {
                    matches: playlist.matches,
                    sequence_groups: playlist.sequence_groups,
                    metadata: playlist.metadata,
                    ..Printed::named(Some(cut(&playlist.name)))
                });
            }
            (
                LineIn {
                    warning: None,
                    error: None,
                    choice: None,
                    playlist: None,
                    entry: Some(entry),
                    watch: None,
                },
                Op::Resolve,
            ) => self.add_entry(entry)?,
            (
                LineIn {
                    warning: None,
                    error: None,
                    choice: None,
                    playlist: None,
                    entry: None,
                    watch: Some(path),
                },
                Op::Watch,
            ) => {
                check_room(self.watched.len(), MAX_ENTRIES, "watched paths")?;
                if self.watched_bytes + path.len() > WATCH_BYTES {
                    return Err(format!(
                        "the watched paths are more than {WATCH_BYTES} bytes"
                    ));
                }
                self.watched_bytes += path.len();
                self.watched.push(absolute("the watched path", path)?);
            }
            (LineIn { watch: Some(_), .. }, Op::Scan) => return Err(wrong("watch", "scan")),
            (LineIn { watch: Some(_), .. }, Op::Resolve) => {
                return Err(wrong("watch", "resolve"));
            }
            (
                LineIn {
                    choice: Some(_), ..
                },
                Op::Watch,
            ) => return Err(wrong("choice", "watch")),
            (
                LineIn {
                    playlist: Some(_), ..
                },
                Op::Watch,
            ) => return Err(wrong("playlist", "watch")),
            (LineIn { entry: Some(_), .. }, Op::Watch) => return Err(wrong("entry", "watch")),
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
                    "needs exactly one of warning, error, choice, playlist, entry or watch"
                        .to_owned(),
                );
            }
        }
        Ok(())
    }

    fn add_entry(&mut self, entry: EntryIn) -> Result<(), String> {
        check_room(self.printed, MAX_ENTRIES, "entries and playlists")?;
        if entry.name.is_empty() {
            return Err("the entry name is empty".to_owned());
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
        self.budget.take(&entry.metadata)?;
        let mut slots = HashSet::new();
        let mut labels = HashSet::new();
        let mut overlays = Vec::with_capacity(entry.overlays.len());
        let mut lanes_starting_hidden = Vec::new();
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
            self.budget.take(&overlay.metadata)?;
            if !overlay.considered {
                lanes_starting_hidden.push(overlay.label.clone());
            }
            overlays.push(OverlayRef {
                label: overlay.label,
                slot: overlay.slot,
                path: absolute("the overlay path", overlay.path)?,
                handler: overlay.handler,
                options: overlay.options,
                metadata: overlay.metadata,
            });
        }
        self.printed += 1;
        if self.playlists.is_empty() {
            self.playlists.push(Printed::named(None));
        }
        if let Some(playlist) = self.playlists.last_mut() {
            playlist.entries.push(ResolvedEntry {
                name: entry.name,
                video,
                overlays,
                starts_hidden: !entry.considered,
                lanes_starting_hidden,
                metadata: entry.metadata,
            });
        }
        Ok(())
    }
}

/// Fails, so the run stops, when `kept` choices or entries already fill their `limit`: a plugin that never stops
/// printing must not grow the host's memory without end.
fn check_room(kept: usize, limit: usize, what: &str) -> Result<(), String> {
    if kept >= limit {
        return Err(format!("more than {limit} {what}"));
    }
    Ok(())
}

fn absolute(what: &str, path: String) -> Result<PathBuf, String> {
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

#[cfg(test)]
mod tests;
