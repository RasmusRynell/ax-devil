//! The workspace file as JSON: private DTOs that mirror the domain, so the domain types carry no serde and a
//! `HostPath` never serializes by accident.

use foundation::{BookmarkId, HostPath, ItemId, MediaTime};
use handler_api::Settings;
use overlay_sync::MatchPolicy;
use serde::{Deserialize, Serialize, de::DeserializeOwned, de::IgnoredAny};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
    path::{Component, Path, PathBuf},
};
use workspace::{
    Bookmark, BookmarkKind, BookmarkTarget, OverlayRecipe, PlaylistRecipe, Recipe, Source,
    Unreadable, VideoRecipe, Workspace, WorkspaceItem,
};

const VERSION: u64 = 2;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Top {
    #[serde(rename = "version")]
    _version: IgnoredAny,
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    items: Vec<Value>,
    #[serde(default)]
    bookmarks: Vec<Value>,
    #[serde(default)]
    reviewed: Vec<Value>,
    #[serde(default)]
    recovers: Option<Value>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct VideoItem {
    kind: String,
    id: ItemId,
    label: String,
    video: String,
    #[serde(default)]
    overlays: Vec<OverlayDto>,
    #[serde(default)]
    lane_matching: BTreeMap<String, MatchPolicy>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct OverlayDto {
    path: String,
    decoder: String,
    label: String,
    #[serde(default)]
    options: Settings,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PlaylistItem {
    kind: String,
    id: ItemId,
    label: String,
    resolver: String,
    #[serde(default)]
    settings: Settings,
    #[serde(default)]
    lane_matching: BTreeMap<String, MatchPolicy>,
}

#[derive(Serialize, Deserialize, Default, Clone, Copy)]
#[serde(rename_all = "snake_case")]
enum KindDto {
    #[default]
    Note,
    Issue,
    Example,
    Question,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct BookmarkDto {
    id: BookmarkId,
    item: ItemId,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    entry: Option<String>,
    frame: u64,
    time_us: i64,
    #[serde(default)]
    kind: KindDto,
    #[serde(default)]
    note: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct TargetDto {
    item: ItemId,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    entry: Option<String>,
}

/// `path` made absolute against the working folder, with `.` and `..` folded without touching the disk.
pub(crate) fn normalize(path: &Path) -> PathBuf {
    let absolute = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    let mut folded = PathBuf::new();
    for component in absolute.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                folded.pop();
            }
            other => folded.push(other),
        }
    }
    folded
}

/// `path` normalized with its existing part resolved through symlinks, so two spellings of one place (`/var` and
/// `/private/var` on macOS) compare equal.
fn real(path: &Path) -> PathBuf {
    let path = normalize(path);
    let mut head = path.as_path();
    let mut rest = Vec::new();
    while let (Some(parent), Some(name)) = (head.parent(), head.file_name()) {
        if let Ok(resolved) = std::fs::canonicalize(head) {
            return rest
                .iter()
                .rev()
                .fold(resolved, |resolved, name| resolved.join(name));
        }
        rest.push(name);
        head = parent;
    }
    path
}

/// A path as the file holds it: relative with `/` inside `base` (compared by where both really are), else absolute.
fn written(path: &HostPath, base: Option<&Path>) -> String {
    let path = normalize(path.as_path());
    match base.and_then(|base| {
        real(&path)
            .strip_prefix(real(base))
            .ok()
            .map(Path::to_path_buf)
    }) {
        Some(relative) => relative
            .components()
            .map(|part| part.as_os_str().to_string_lossy())
            .collect::<Vec<_>>()
            .join("/"),
        None => path.to_string_lossy().into_owned(),
    }
}

/// Whether everything in `raw` is in `kept`, the same data read and written again: a key `raw` has that the format
/// does not know is missing from `kept`, while defaults only `kept` has do not count.
fn covered(raw: &Value, kept: &Value) -> bool {
    match (raw, kept) {
        (Value::Object(raw), Value::Object(kept)) => raw
            .iter()
            .all(|(key, raw)| kept.get(key).is_some_and(|kept| covered(raw, kept))),
        (Value::Array(raw), Value::Array(kept)) => {
            raw.len() == kept.len() && raw.iter().zip(kept).all(|(raw, kept)| covered(raw, kept))
        }
        _ => raw == kept,
    }
}

fn value(dto: &impl Serialize) -> Value {
    serde_json::to_value(dto).expect("document types serialize to JSON")
}

fn write_item(item: &WorkspaceItem, base: Option<&Path>) -> Value {
    let Recipe {
        source,
        lane_matching,
    } = &item.recipe;
    let (id, label, lane_matching) = (item.id, item.label.clone(), lane_matching.clone());
    match source {
        Source::Video(recipe) => value(&VideoItem {
            kind: "video".into(),
            id,
            label,
            video: written(&recipe.video, base),
            overlays: recipe
                .overlays
                .iter()
                .map(|overlay| OverlayDto {
                    path: written(&overlay.path, base),
                    decoder: overlay.handler.clone(),
                    label: overlay.label.clone(),
                    options: overlay.options.clone(),
                })
                .collect(),
            lane_matching,
        }),
        Source::Playlist(recipe) => value(&PlaylistItem {
            kind: "playlist".into(),
            id,
            label,
            resolver: recipe.resolver.clone(),
            settings: recipe.settings.clone(),
            lane_matching,
        }),
        Source::Unreadable(unreadable) => unreadable.raw.clone(),
    }
}

/// The document for `workspace`. `base` is the folder paths inside it are written relative to. `recovers` is only for
/// recovery copies: the workspace file the copy belongs to, `Some(None)` for Untitled.
pub(crate) fn to_document(
    workspace: &Workspace,
    base: Option<&Path>,
    recovers: Option<Option<&Path>>,
) -> Value {
    let kind = |kind: BookmarkKind| match kind {
        BookmarkKind::Note => KindDto::Note,
        BookmarkKind::Issue => KindDto::Issue,
        BookmarkKind::Example => KindDto::Example,
        BookmarkKind::Question => KindDto::Question,
    };
    let mut document = json!({
        "version": VERSION,
        "name": workspace.name,
        "items": workspace.items.iter().map(|item| write_item(item, base)).collect::<Vec<_>>(),
        "bookmarks": workspace.bookmarks.iter().map(|bookmark| value(&BookmarkDto {
            id: bookmark.id,
            item: bookmark.target.item,
            entry: bookmark.target.entry.clone(),
            frame: bookmark.frame,
            time_us: bookmark.time.as_us(),
            kind: kind(bookmark.kind),
            note: bookmark.note.clone(),
        })).collect::<Vec<_>>(),
        "reviewed": workspace.reviewed.iter().map(|target| value(&TargetDto {
            item: target.item,
            entry: target.entry.clone(),
        })).collect::<Vec<_>>(),
    });
    if let Some(recovers) = recovers {
        document["recovers"] = json!(recovers.map(|path| path.to_string_lossy()));
    }
    document
}

fn unreadable(raw: Value, reason: String) -> WorkspaceItem {
    let text = |key: &str| raw.get(key).and_then(Value::as_str);
    WorkspaceItem {
        id: text("id")
            .and_then(ItemId::parse)
            .unwrap_or_else(ItemId::generate),
        label: text("label")
            .or_else(|| text("kind"))
            .unwrap_or("Unreadable item")
            .to_owned(),
        recipe: Recipe::new(Source::Unreadable(Unreadable { raw, reason })),
    }
}

fn read_item(raw: Value, base: &Path) -> WorkspaceItem {
    let resolve = |text: &str| HostPath::new(normalize(&base.join(text)));
    let Some(object) = raw.as_object() else {
        return unreadable(raw, "This item is not a JSON object.".into());
    };
    let parsed = match object.get("kind").and_then(Value::as_str) {
        Some("video") => serde_json::from_value::<VideoItem>(raw.clone()).map(|dto| {
            let source = Source::Video(VideoRecipe {
                video: resolve(&dto.video),
                overlays: dto
                    .overlays
                    .into_iter()
                    .map(|overlay| OverlayRecipe {
                        path: resolve(&overlay.path),
                        handler: overlay.decoder,
                        label: overlay.label,
                        options: overlay.options,
                    })
                    .collect(),
            });
            (dto.id, dto.label, source, dto.lane_matching)
        }),
        Some("playlist") => serde_json::from_value::<PlaylistItem>(raw.clone()).map(|dto| {
            let source = Source::Playlist(PlaylistRecipe {
                resolver: dto.resolver,
                settings: dto.settings,
            });
            (dto.id, dto.label, source, dto.lane_matching)
        }),
        kind => {
            let kind = kind.unwrap_or("");
            let reason =
                format!("Unknown item kind: \"{kind}\". A newer version or a missing plugin?");
            return unreadable(raw, reason);
        }
    };
    match parsed {
        Ok((_, _, _, lane_matching))
            if object
                .get("lane_matching")
                .is_some_and(|raw| !covered(raw, &value(&lane_matching))) =>
        {
            unreadable(
                raw,
                "Its lane matching has a setting this version does not know.".into(),
            )
        }
        Ok((id, label, source, lane_matching)) => WorkspaceItem {
            id,
            label,
            recipe: Recipe {
                source,
                lane_matching,
            },
        },
        Err(error) => unreadable(raw, error.to_string()),
    }
}

fn parse<T: DeserializeOwned>(raw: Value, name: &str, what: &str, n: usize) -> Result<T, String> {
    serde_json::from_value(raw).map_err(|error| format!("{name}: {what} {n} is malformed: {error}"))
}

/// Reads a document: the workspace and, for a recovery copy, the file it recovers. `name` is the file's name for
/// messages; relative paths are read against `base`. Fails whole, with the reason, on anything the format forbids.
pub(crate) fn from_document(
    bytes: &[u8],
    name: &str,
    base: &Path,
) -> Result<(Workspace, Option<PathBuf>), String> {
    let document: Value = serde_json::from_slice(bytes)
        .map_err(|error| format!("{name} is not valid JSON: {error}"))?;
    let Some(version) = document.get("version") else {
        return Err(format!("{name} is not a workspace file."));
    };
    if version.as_u64() != Some(VERSION) {
        return Err(format!(
            "{name} has workspace file version {version}; this app reads {VERSION}."
        ));
    }
    let top: Top = serde_json::from_value(document).map_err(|error| format!("{name}: {error}"))?;
    let recovers = match top.recovers {
        None => None,
        Some(Value::String(path)) if Path::new(&path).is_absolute() => Some(PathBuf::from(path)),
        Some(_) => return Err(format!("{name} has no valid workspace file path.")),
    };

    let items: Vec<WorkspaceItem> = top
        .items
        .into_iter()
        .map(|raw| read_item(raw, base))
        .collect();
    let mut ids = HashSet::new();
    if let Some(item) = items.iter().find(|item| !ids.insert(item.id)) {
        return Err(format!("{name}: two items have the id {}", item.id));
    }

    let mut bookmarks = Vec::with_capacity(top.bookmarks.len());
    let mut bookmark_ids = HashSet::new();
    for (index, raw) in top.bookmarks.into_iter().enumerate() {
        let n = index + 1;
        let dto: BookmarkDto = parse(raw, name, "bookmark", n)?;
        if dto.time_us < 0 {
            return Err(format!(
                "{name}: bookmark {n} is malformed: time_us is negative"
            ));
        }
        if !bookmark_ids.insert(dto.id) {
            return Err(format!("{name}: two bookmarks have the id {}", dto.id));
        }
        if !ids.contains(&dto.item) {
            return Err(format!(
                "{name}: bookmark {n} names no item of the workspace"
            ));
        }
        bookmarks.push(Bookmark {
            id: dto.id,
            target: BookmarkTarget {
                item: dto.item,
                entry: dto.entry,
            },
            frame: dto.frame,
            time: MediaTime::from_us(dto.time_us),
            kind: match dto.kind {
                KindDto::Note => BookmarkKind::Note,
                KindDto::Issue => BookmarkKind::Issue,
                KindDto::Example => BookmarkKind::Example,
                KindDto::Question => BookmarkKind::Question,
            },
            note: dto.note,
        });
    }

    let mut reviewed = BTreeSet::new();
    for (index, raw) in top.reviewed.into_iter().enumerate() {
        let n = index + 1;
        let dto: TargetDto = parse(raw, name, "reviewed target", n)?;
        if !ids.contains(&dto.item) {
            return Err(format!(
                "{name}: reviewed target {n} names no item of the workspace"
            ));
        }
        reviewed.insert(BookmarkTarget {
            item: dto.item,
            entry: dto.entry,
        });
    }

    let title = top
        .name
        .map(|title| title.trim().to_owned())
        .filter(|title| !title.is_empty());
    Ok((
        Workspace {
            name: title,
            items,
            bookmarks,
            reviewed,
        },
        recovers,
    ))
}
