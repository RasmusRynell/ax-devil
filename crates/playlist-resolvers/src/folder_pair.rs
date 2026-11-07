use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
};

use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, UserError};
use handler_api::{
    Choice, Cli, Field, FieldKind, Form, OverlayRef, PlaylistResolver, ResolvedEntry,
    ResolvedPlaylist, ResolvedPlaylists, ResolverSpec, ScanCount, Values, VideoRef, invalid_field,
};

use crate::resolved_file_type;

const COMPRESSION_SUFFIXES: [&str; 4] = ["bz2", "gz", "xz", "zst"];

/// Matches the videos of one folder with the overlay files of another by file name.
///
/// Names match after a trailing compression suffix and one more suffix are removed; the files are not read until an
/// entry opens. Its count-only `pairs` scan reports how many pairs the folders hold. Settings are `videos_dir`, `overlays_dir`, `handler_type`, the decoder that reads the overlay files, and its
/// `options`, which every overlay is given as they are.
pub struct FolderPairResolver {
    spec: ResolverSpec,
}

impl FolderPairResolver {
    pub(crate) fn new() -> Self {
        Self {
            spec: ResolverSpec {
                id: "folder_pair".to_owned(),
                plugin: "folder_pair".to_owned(),
                name: "Folder Pair".to_owned(),
                description: "Match videos and overlays from two folders by file name.".to_owned(),
                form: Form {
                    fields: vec![
                        Field::new("videos_dir", "Videos", FieldKind::Folder)
                            .required()
                            .positional(),
                        Field::new("overlays_dir", "Overlays", FieldKind::Folder)
                            .required()
                            .positional(),
                        Field::new("handler_type", "Decoder", FieldKind::Handler).required(),
                        Field {
                            cli: Cli::Flag("option".to_owned()),
                            ..Field::new(
                                "options",
                                "Overlay options",
                                FieldKind::DecoderOptions {
                                    handler_field: "handler_type".to_owned(),
                                },
                            )
                        },
                        Field::new(
                            "pairs",
                            "Pairs",
                            FieldKind::Scan {
                                depends_on: vec![
                                    "videos_dir".to_owned(),
                                    "overlays_dir".to_owned(),
                                ],
                                count: Some(ScanCount {
                                    missing: "Select video and overlay folders.".to_owned(),
                                    found: "Found {count} matched pair(s).".to_owned(),
                                    none: "No matched video/overlay pairs found.".to_owned(),
                                }),
                            },
                        ),
                    ],
                },
            },
        }
    }
}

impl PlaylistResolver for FolderPairResolver {
    fn spec(&self) -> &ResolverSpec {
        &self.spec
    }

    fn scan(
        &self,
        field: &str,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<Vec<Choice>, Interrupt<UserError>> {
        if field != "pairs" {
            return Err(Interrupt::Failed(invalid_field(
                "This setting has nothing to scan",
                field,
            )));
        }
        let (videos_dir, overlays_dir) = folders(settings)?;
        let pairs = discover(videos_dir, overlays_dir, cancel)?;
        Ok(pairs
            .into_iter()
            .map(|pair| Choice {
                label: pair.name.clone(),
                value: pair.name,
                detail: String::new(),
            })
            .collect())
    }

    fn resolve(
        &self,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<ResolvedPlaylists, Interrupt<UserError>> {
        let (videos_dir, overlays_dir) = folders(settings)?;
        let handler = settings
            .text("handler_type")
            .ok_or_else(|| Interrupt::Failed(invalid_field("Required", "handler_type")))?;
        let options = settings.options("options");
        let entries: Vec<_> = discover(videos_dir, overlays_dir, cancel)?
            .into_iter()
            .map(|pair| ResolvedEntry {
                overlays: vec![OverlayRef {
                    label: pair.name.clone(),
                    slot: "overlay".to_owned(),
                    path: pair.overlay,
                    handler: handler.to_string(),
                    options: options.clone(),
                }],
                name: pair.name,
                video: VideoRef::File(pair.video),
            })
            .collect();
        if entries.is_empty() {
            return Err(Interrupt::Failed(
                UserError::new(
                    ErrorCode::PlaylistInvalid,
                    format!(
                        "No matched video/overlay pairs in '{}' and '{}'.",
                        videos_dir.display(),
                        overlays_dir.display()
                    ),
                )
                .at(HostPath::new(videos_dir)),
            ));
        }
        Ok(ResolvedPlaylists {
            playlists: vec![ResolvedPlaylist {
                name: self.spec.name.clone(),
                entries,
            }],
            warnings: Vec::new(),
        })
    }
}

/// The two folders the settings name.
fn folders(settings: &Values) -> Result<(&Path, &Path), Interrupt<UserError>> {
    let required = |key: &str| {
        settings
            .path(key)
            .ok_or_else(|| Interrupt::Failed(invalid_field("Required", key)))
    };
    Ok((required("videos_dir")?, required("overlays_dir")?))
}

/// A video and the overlay file that goes with it.
struct Pair {
    name: String,
    video: PathBuf,
    overlay: PathBuf,
}

/// The pairs of files with the same match name in the two folders, in name order. Nothing is read from the files.
fn discover(
    videos_dir: &Path,
    overlays_dir: &Path,
    cancel: &CancelToken,
) -> Result<Vec<Pair>, Interrupt<UserError>> {
    cancel.check()?;
    require_dir(videos_dir, "Video directory does not exist").map_err(Interrupt::Failed)?;
    require_dir(overlays_dir, "Overlay directory does not exist").map_err(Interrupt::Failed)?;
    let videos = index_files(videos_dir, "video", cancel)?;
    let mut overlays = index_files(overlays_dir, "overlay", cancel)?;
    let mut pairs: Vec<_> = videos
        .into_iter()
        .filter_map(|(name, video)| {
            let overlay = overlays.remove(&name)?;
            Some(Pair {
                name,
                video,
                overlay,
            })
        })
        .collect();
    pairs.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(pairs)
}

fn require_dir(path: &Path, reason: &str) -> Result<(), UserError> {
    if path.is_dir() {
        Ok(())
    } else {
        Err(UserError::new(ErrorCode::FileNotFound, reason).at(HostPath::new(path)))
    }
}

/// Files of `dir` by match name; dotfiles, folders and names that are not UTF-8 are left out.
fn index_files(
    dir: &Path,
    label: &str,
    cancel: &CancelToken,
) -> Result<HashMap<String, PathBuf>, Interrupt<UserError>> {
    let unreadable = |error: std::io::Error| {
        Interrupt::Failed(
            UserError::new(
                ErrorCode::FileUnreadable,
                format!("Cannot read the {label} directory: {error}"),
            )
            .at(HostPath::new(dir)),
        )
    };
    let mut indexed = HashMap::new();
    let mut duplicates = Vec::new();
    for entry in fs::read_dir(dir).map_err(unreadable)? {
        cancel.check()?;
        let entry = entry.map_err(unreadable)?;
        let file_name = entry.file_name();
        let Some(file_name) = file_name.to_str() else {
            continue;
        };
        if file_name.starts_with('.') || !resolved_file_type(&entry).is_some_and(|ty| ty.is_file())
        {
            continue;
        }
        let name = match_name(file_name);
        if indexed.contains_key(name) {
            duplicates.push(name.to_string());
        } else {
            indexed.insert(name.to_string(), entry.path());
        }
    }
    if duplicates.is_empty() {
        return Ok(indexed);
    }
    duplicates.sort();
    duplicates.dedup();
    Err(Interrupt::Failed(
        UserError::new(
            ErrorCode::PlaylistInvalid,
            format!(
                "Duplicate {label} file name(s) after suffix stripping: {}",
                duplicates.join(", ")
            ),
        )
        .at(HostPath::new(dir)),
    ))
}

/// The file name without a trailing compression suffix and one more suffix.
fn match_name(file_name: &str) -> &str {
    let mut name = file_name;
    if let Some(suffix) = suffix(name)
        && COMPRESSION_SUFFIXES
            .iter()
            .any(|compression| suffix.eq_ignore_ascii_case(compression))
    {
        name = &name[..name.len() - suffix.len() - 1];
    }
    match suffix(name) {
        Some(suffix) => &name[..name.len() - suffix.len() - 1],
        None => name,
    }
}

/// The text after the last dot, unless the dot starts or ends the name.
fn suffix(name: &str) -> Option<&str> {
    let dot = name.rfind('.').filter(|&dot| dot > 0)?;
    Some(&name[dot + 1..]).filter(|suffix| !suffix.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn match_name_strips_compression_then_one_suffix() {
        let table = [
            ("alpha.mp4", "alpha"),
            ("beta.jsonl.gz", "beta"),
            ("camera.1.mp4", "camera.1"),
            ("camera.1.jsonl.GZ", "camera.1"),
            ("plain", "plain"),
            ("data.gz", "data"),
            ("trailing.", "trailing."),
        ];
        for (file_name, expected) in table {
            assert_eq!(match_name(file_name), expected, "{file_name}");
        }
    }
}
