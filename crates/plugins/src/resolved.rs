//! The lines of a scan or a resolve, collected and checked as they arrive (the format in the write-plugin skill).
//!
//! What the lines add up to is used only when the plugin then exits 0, so a plugin that fails halfway leaves
//! nothing in a playlist.

use std::{collections::HashSet, path::Path};

use foundation::{FrameRate, Warning};
use handler_api::{
    Choice, ImageSequence, OverlayRef, ResolvedEntry, ResolvedPlaylist, ResolvedPlaylists,
    Settings, VideoRef,
};
use serde::Deserialize;

use crate::{
    run::{LineHandler, Next},
    wire::{ErrorIn, WarningIn, cut, json_reason, load_warning},
};

/// The most warnings a scan or resolve keeps, as the scene store does for a decode; later ones are counted, not kept.
const MAX_WARNINGS: usize = 64;
/// The most choices a scan may print.
const MAX_CHOICES: usize = 50_000;
/// The most entries and playlist lines a resolve may print.
const MAX_ENTRIES: usize = 50_000;

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
    /// The playlists so far, the last one still taking entries; entries before any `playlist` line fill one with no
    /// name yet.
    playlists: Vec<(Option<String>, Vec<ResolvedEntry>)>,
    /// Entries and playlist lines so far.
    printed: usize,
    /// The entry names of the last playlist.
    names: HashSet<String>,
    warnings: Vec<Warning>,
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
            printed: 0,
            names: HashSet::new(),
            warnings: Vec::new(),
            suppressed: 0,
        }
    }

    pub(crate) fn into_choices(self) -> Vec<Choice> {
        self.choices
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
                .map(|(name, entries)| ResolvedPlaylist {
                    name: name.unwrap_or_else(|| fallback.to_owned()),
                    entries,
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
                },
                Op::Resolve,
            ) => {
                check_room(self.printed, MAX_ENTRIES, "entries and playlists")?;
                if playlist.name.is_empty() {
                    return Err("the playlist name is empty".to_owned());
                }
                self.printed += 1;
                self.playlists.push((Some(cut(&playlist.name)), Vec::new()));
                self.names.clear();
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
        check_room(self.printed, MAX_ENTRIES, "entries and playlists")?;
        if entry.name.is_empty() {
            return Err("the entry name is empty".to_owned());
        }
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
        self.printed += 1;
        if self.playlists.is_empty() {
            self.playlists.push((None, Vec::new()));
        }
        if let Some((_, entries)) = self.playlists.last_mut() {
            entries.push(ResolvedEntry {
                name: entry.name,
                video,
                overlays,
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

#[cfg(test)]
mod tests {
    use super::*;

    fn feed(op: Op, line: &str, count: usize) -> Option<String> {
        let mut collected = Collected::new(op);
        (1..=count + 1).find_map(|number| match collected.line(number, line.as_bytes()) {
            Next::Stop(reason) => Some(reason),
            Next::Continue | Next::SinkFull => None,
        })
    }

    #[test]
    fn warnings_past_the_cap_are_summarized_but_choices_and_entries_stop_the_run() {
        let warning = r#"{"warning": {"problem": "p", "consequence": "c"}}"#;
        let choice = r#"{"choice": {"value": "a"}}"#;
        let entry =
            |n: usize| format!(r#"{{"entry": {{"name": "e{n}", "video": {{"file": "/v.mp4"}}}}}}"#);
        let mut collected = Collected::new(Op::Resolve);
        for number in 1..=MAX_WARNINGS + 10 {
            assert!(matches!(
                collected.line(number, warning.as_bytes()),
                Next::Continue
            ));
        }
        assert!(matches!(
            collected.line(MAX_WARNINGS + 11, entry(0).as_bytes()),
            Next::Continue
        ));
        let resolved = collected.into_playlists("fallback");
        assert_eq!(resolved.playlists[0].entries.len(), 1);
        assert_eq!(resolved.warnings.len(), MAX_WARNINGS + 1);
        assert_eq!(
            resolved.warnings[MAX_WARNINGS].problem,
            "10 more warnings suppressed"
        );

        assert_eq!(
            feed(Op::Scan, choice, MAX_CHOICES).as_deref(),
            Some("line 50001: more than 50000 choices")
        );
        let mut collected = Collected::new(Op::Resolve);
        let stopped =
            (1..=MAX_ENTRIES + 1).find_map(|n| match collected.line(n, entry(n).as_bytes()) {
                Next::Stop(reason) => Some(reason),
                Next::Continue | Next::SinkFull => None,
            });
        assert_eq!(
            stopped.as_deref(),
            Some("line 50001: more than 50000 entries and playlists")
        );
    }

    #[test]
    fn playlist_lines_split_the_entries_and_entries_before_the_first_one_take_the_resolvers_name() {
        let entry = |name: &str| {
            format!(r#"{{"entry": {{"name": "{name}", "video": {{"file": "/v.mp4"}}}}}}"#)
        };
        let playlist = |name: &str| format!(r#"{{"playlist": {{"name": "{name}"}}}}"#);
        let run = |lines: &[String]| {
            let mut collected = Collected::new(Op::Resolve);
            for (number, line) in lines.iter().enumerate() {
                if let Next::Stop(reason) = collected.line(number + 1, line.as_bytes()) {
                    return Err(reason);
                }
            }
            Ok(collected.into_playlists("resolver"))
        };

        let resolved = run(&[
            entry("a"),
            playlist("p"),
            entry("a"),
            entry("b"),
            playlist("p"),
        ])
        .unwrap();
        let shape: Vec<_> = resolved
            .playlists
            .iter()
            .map(|playlist| {
                let names: Vec<_> = playlist.entries.iter().map(|e| e.name.as_str()).collect();
                (playlist.name.as_str(), names)
            })
            .collect();
        assert_eq!(
            shape,
            [
                ("resolver", vec!["a"]),
                ("p", vec!["a", "b"]),
                ("p", vec![])
            ]
        );

        assert_eq!(
            run(&[playlist("p"), entry("a"), entry("a")])
                .err()
                .as_deref(),
            Some("line 3: the entry name 'a' is used twice")
        );
        assert_eq!(
            run(&[playlist("")]).err().as_deref(),
            Some("line 1: the playlist name is empty")
        );
    }
}
