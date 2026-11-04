use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
};

use foundation::{CancelToken, ErrorCode, HostPath, Interrupt, UserError};
use handler_api::{
    Cli, Field, FieldKind, Form, OverlayRef, PlaylistResolver, ResolvedEntry, ResolvedPlaylist,
    ResolvedPlaylists, ResolverSpec, Values, VideoRef, invalid_field,
};

use crate::resolved_file_type;

const COMPRESSION_SUFFIXES: [&str; 4] = ["bz2", "gz", "xz", "zst"];

/// Matches the videos of one folder with the overlay files of another by file name.
///
/// Names match after a trailing compression suffix and one more suffix are removed. Decoders read files as they are,
/// so a matched file that is compressed fails the resolve and names the file instead of yielding an empty overlay.
/// Settings are `videos_dir`, `overlays_dir`, `handler_type`, the decoder that reads the overlay files, and its
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
                        Field::new("videos_dir", "Videos folder", FieldKind::Folder)
                            .required()
                            .positional(),
                        Field::new("overlays_dir", "Overlays folder", FieldKind::Folder)
                            .required()
                            .positional(),
                        Field::new("handler_type", "Overlay format", FieldKind::Handler).required(),
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

    fn resolve(
        &self,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<ResolvedPlaylists, Interrupt<UserError>> {
        let required = |key: &str| {
            settings
                .text(key)
                .ok_or_else(|| Interrupt::Failed(invalid_field("Required", key)))
        };
        let (videos_dir, overlays_dir) = (
            Path::new(required("videos_dir")?),
            Path::new(required("overlays_dir")?),
        );
        let handler = required("handler_type")?;
        let options = settings.options("options");
        cancel.check()?;
        require_dir(videos_dir, "Video directory does not exist").map_err(Interrupt::Failed)?;
        require_dir(overlays_dir, "Overlay directory does not exist").map_err(Interrupt::Failed)?;

        let videos = index_files(videos_dir, "video", cancel)?;
        let mut overlays = index_files(overlays_dir, "overlay", cancel)?;
        let mut entries = Vec::with_capacity(videos.len().min(overlays.len()));
        for (name, video) in videos {
            let Some(path) = overlays.remove(&name) else {
                continue;
            };
            if let Some(compressed) = [&video, &path].into_iter().find(|p| is_compressed(p)) {
                return Err(Interrupt::Failed(
                    UserError::new(
                        ErrorCode::UnsupportedFormat,
                        "Compressed files are not supported; decompress the file first",
                    )
                    .at(HostPath::new(compressed)),
                ));
            }
            let overlay = OverlayRef {
                label: name.clone(),
                slot: "overlay".to_owned(),
                path,
                handler: handler.to_string(),
                options: options.clone(),
            };
            entries.push(ResolvedEntry {
                name,
                video: VideoRef::File(video),
                overlays: vec![overlay],
            });
        }
        if entries.is_empty() {
            return Err(Interrupt::Failed(
                UserError::new(
                    ErrorCode::PlaylistInvalid,
                    format!(
                        "No video file name matches an overlay file name in {}",
                        overlays_dir.display()
                    ),
                )
                .at(HostPath::new(videos_dir)),
            ));
        }
        entries.sort_by(|a, b| a.name.cmp(&b.name));
        Ok(ResolvedPlaylists {
            playlists: vec![ResolvedPlaylist {
                name: self.spec.name.clone(),
                entries,
            }],
            warnings: Vec::new(),
        })
    }
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

/// Whether the file name ends in a compression suffix.
fn is_compressed(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| {
            COMPRESSION_SUFFIXES
                .iter()
                .any(|compression| ext.eq_ignore_ascii_case(compression))
        })
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
