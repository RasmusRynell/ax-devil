use std::{
    collections::HashMap,
    fs::{self, DirEntry, FileType},
    path::{Path, PathBuf},
};

use foundation::{CancelToken, Cancelled, Interrupt, UserError};

use crate::{
    Choice, Cli, Field, FieldKind, OverlayRef, PlaylistResolver, ResolvedEntry, ResolvedPlaylist,
    ResolverSchema, ResolverSpec, Settings, VideoRef, WatchPaths,
};

static SPEC: ResolverSpec = ResolverSpec {
    id: "folder_pair",
    display_name: "Folder Pair",
    description: "Match videos and overlays from two folders by file name.",
};

static SCHEMA: ResolverSchema = ResolverSchema {
    fields: &[
        Field {
            key: "videos_dir",
            label: "Videos folder",
            kind: FieldKind::Folder,
            required: true,
            cli: Cli::Positional,
        },
        Field {
            key: "overlays_dir",
            label: "Overlays folder",
            kind: FieldKind::Folder,
            required: true,
            cli: Cli::Positional,
        },
        Field {
            key: "handler_type",
            label: "Overlay format",
            kind: FieldKind::Handler,
            required: true,
            cli: Cli::Flag("handler-type"),
        },
    ],
};

const COMPRESSION_SUFFIXES: [&str; 4] = ["bz2", "gz", "xz", "zst"];

/// Matches the videos of one folder with the overlay files of another by file name.
///
/// Names match after a trailing compression suffix and one more suffix are removed. Decoders read files as they are,
/// so a matched file that is compressed fails the resolve and names the file instead of yielding an empty overlay.
/// Settings are `videos_dir`, `overlays_dir` and `handler_type`, the decoder handler that reads the overlay files.
pub struct FolderPairResolver;

impl PlaylistResolver for FolderPairResolver {
    fn spec(&self) -> &'static ResolverSpec {
        &SPEC
    }

    fn schema(&self) -> &'static ResolverSchema {
        &SCHEMA
    }

    fn scan(
        &self,
        field: &str,
        _settings: &Settings,
        _cancel: &CancelToken,
    ) -> Result<Vec<Choice>, Interrupt<UserError>> {
        Err(Interrupt::Failed(UserError::new(
            "This setting has nothing to scan",
            field,
        )))
    }

    fn resolve(
        &self,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<ResolvedPlaylist, Interrupt<UserError>> {
        let videos_dir = required_text(settings, "videos_dir").map_err(Interrupt::Failed)?;
        let overlays_dir = required_text(settings, "overlays_dir").map_err(Interrupt::Failed)?;
        let handler = required_text(settings, "handler_type").map_err(Interrupt::Failed)?;
        let (videos_dir, overlays_dir) = (Path::new(videos_dir), Path::new(overlays_dir));
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
                return Err(Interrupt::Failed(UserError::new(
                    "Compressed files are not supported; decompress the file first",
                    compressed.display().to_string(),
                )));
            }
            let overlay = OverlayRef {
                label: name.clone(),
                path,
                handler: handler.to_string(),
                options: Settings::new(),
            };
            entries.push(ResolvedEntry {
                name,
                video: VideoRef::File(video),
                overlays: vec![overlay],
            });
        }
        if entries.is_empty() {
            return Err(Interrupt::Failed(UserError::new(
                "No video file name matches an overlay file name",
                format!("{} and {}", videos_dir.display(), overlays_dir.display()),
            )));
        }
        entries.sort_by(|a, b| a.name.cmp(&b.name));
        Ok(ResolvedPlaylist {
            display_name: SPEC.display_name.to_string(),
            entries,
        })
    }

    fn watch_paths(
        &self,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<WatchPaths, Cancelled> {
        cancel.check()?;
        let folders = ["videos_dir", "overlays_dir"]
            .into_iter()
            .filter_map(|key| required_text(settings, key).ok())
            .map(PathBuf::from)
            .collect();
        Ok(WatchPaths {
            folders,
            ..WatchPaths::default()
        })
    }
}

/// The type of a directory entry, following a symlink to what it points at; `None` for a broken link.
pub(crate) fn resolved_file_type(entry: &DirEntry) -> Option<FileType> {
    let file_type = entry.file_type().ok()?;
    if file_type.is_symlink() {
        fs::metadata(entry.path()).ok().map(|meta| meta.file_type())
    } else {
        Some(file_type)
    }
}

pub(crate) fn required_text<'a>(settings: &'a Settings, key: &str) -> Result<&'a str, UserError> {
    match settings.get(key).and_then(|value| value.as_str()) {
        Some(text) if !text.is_empty() => Ok(text),
        _ => Err(UserError::new("Must be a non-empty string", key)),
    }
}

fn require_dir(path: &Path, reason: &str) -> Result<(), UserError> {
    if path.is_dir() {
        Ok(())
    } else {
        Err(UserError::new(reason, path.display().to_string()))
    }
}

/// Files of `dir` by match name; dotfiles, folders and names that are not UTF-8 are left out.
fn index_files(
    dir: &Path,
    label: &str,
    cancel: &CancelToken,
) -> Result<HashMap<String, PathBuf>, Interrupt<UserError>> {
    let unreadable = |error: std::io::Error| {
        Interrupt::Failed(UserError::new(
            format!("Cannot read the {label} directory: {error}"),
            dir.display().to_string(),
        ))
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
    Err(Interrupt::Failed(UserError::new(
        format!(
            "Duplicate {label} file name(s) after suffix stripping: {}",
            duplicates.join(", ")
        ),
        dir.display().to_string(),
    )))
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
