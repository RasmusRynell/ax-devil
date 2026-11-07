//! What an audience may learn of the Doc: the only constructor of [`DocWire`].
//!
//! The function starts with an exhaustive destructure of `Doc` (no `..`; `scripts/check-architecture.cjs` rejects a
//! rest pattern in this file), so a new Doc field does not compile until its projection is decided here. An audience
//! without host details sees only items the Doc has open, with their entries and rows, and for a failed item only its
//! public code and the item id: never a path, a recipe or the reason text. What a user wrote, such as labels and the
//! workspace name, is shared on purpose. A live stream shows only its label, overlay kind and lane: never the camera's
//! host, a login or a broker, and an unnamed one is called `Live stream` to an audience without host details. An
//! audience with host details also gets its recipe as stored, with `$NAME` references as references and no literal
//! password.

use api_types::{
    Audience, DocWire,
    bookmarks::{BookmarkGroupInfo, BookmarkInfo, BookmarkKindWire, BookmarkStanding},
    content::{ContentInfo, EntryInfo, PlaylistSpanInfo},
    live::{LiveOverlayKind, NewLiveWire},
    playback::WarningInfo,
    project as project_error,
    workspace::{
        ItemKind, ItemRecipe, ItemState, NewOverlayWire, ResolutionState, RowInfo, RowKindWire,
        RowStatusInfo, WorkspaceState,
    },
};
use sessions::session::Doc;
use std::collections::HashMap;
use workspace::{
    BookmarkKind, Content, Entry, LiveVideoContent, SeekableVideoContent, Source, Workspace,
    WorkspaceItem, playlist_label,
};
use workspace_store::{
    BookmarkGroup, ContentGen, Resolution, Row, RowKind, RowStatus, Saved, Standing,
    WorkspaceParts, bookmark_groups, details, rows,
};

/// `doc` as `audience` may see it.
pub fn project(doc: &Doc, audience: Audience) -> DocWire {
    let Doc { workspace: state } = doc;
    let WorkspaceParts {
        workspace,
        file,
        saved,
        epoch,
        resolutions,
        open,
        changed,
        // The rows carry what the user hid.
        hidden: _,
    } = state.parts();
    let Workspace {
        name,
        items,
        // Listed as groups, which also carry the reviewed marks.
        bookmarks: _,
        reviewed: _,
    } = workspace;
    let sees_everything = audience.host_details;
    let listed: Vec<&WorkspaceItem> = items
        .iter()
        .filter(|item| sees_everything || open.contains(&item.id))
        .collect();
    let labels: HashMap<String, String> = listed
        .iter()
        .map(|item| (item.id.to_string(), item_label(item, audience)))
        .collect();
    let items = listed
        .into_iter()
        .map(|item| {
            let WorkspaceItem {
                id,
                label: stored_label,
                recipe,
            } = item;
            let label = labels[&id.to_string()].clone();
            ItemState {
                id: id.to_string(),
                label: label.clone(),
                kind: match &recipe.source {
                    Source::Video(_) => ItemKind::Video,
                    Source::Playlist(_) => ItemKind::Playlist,
                    Source::Live(_) => ItemKind::Live,
                    Source::Unreadable(_) => ItemKind::Unreadable,
                },
                open: open.contains(id),
                recipe: item_recipe(stored_label, &recipe.source, audience),
                resolution: match resolutions.get(id) {
                    Some(Resolution::Resolved {
                        content,
                        generation,
                    }) => ResolutionState::Resolved {
                        content: content_info(
                            item,
                            content,
                            *generation,
                            &label,
                            live_recipe(stored_label, &recipe.source, audience),
                        ),
                    },
                    Some(Resolution::Failed(error)) => {
                        ResolutionState::Failed(project_error(error, audience))
                    }
                    Some(Resolution::Pending { attempt: _ }) | None => ResolutionState::Pending,
                },
                changed: changed.get(id).copied(),
            }
        })
        .collect();
    let mut rows: Vec<Row> = rows(state)
        .into_iter()
        .filter(|row| labels.contains_key(&row.item.to_string()))
        .collect();
    // An item's row is named as its item is; the store names an unnamed live stream by its host.
    for row in rows.iter_mut().filter(|row| row.kind == RowKind::Item) {
        if let Some(label) = labels.get(&row.key) {
            row.label.clone_from(label);
        }
    }
    // The audience's own rows tell each other apart, by locations only if it may see the host's.
    let details = details(&rows, sees_everything);
    let rows = rows
        .into_iter()
        .zip(details)
        .map(|(row, detail)| row_info(row, detail, audience))
        .collect();
    let bookmarks = bookmark_groups(state)
        .into_iter()
        .filter(|group| labels.contains_key(&group.target.item.to_string()))
        .map(group_info)
        .collect();
    DocWire {
        workspace: WorkspaceState {
            name: name.clone(),
            modified: state.modified(),
            recovered: matches!(saved, Saved::Recovered { offer: true }),
            epoch,
            // The path names the host's folders, so only an audience with host details learns it.
            file: file
                .as_ref()
                .filter(|_| sees_everything)
                .map(|file| file.as_path().to_string_lossy().into_owned()),
            items,
            rows,
            bookmarks,
        },
    }
}

/// The name an item's row and viewer show to `audience`: its label, or when it has none (a live stream may) the host as
/// entered to an audience with host details and `Live stream` to everyone else.
fn item_label(item: &WorkspaceItem, audience: Audience) -> String {
    match &item.recipe.source {
        Source::Live(_) if item.label.is_empty() && !audience.host_details => {
            "Live stream".to_owned()
        }
        Source::Video(_) | Source::Playlist(_) | Source::Live(_) | Source::Unreadable(_) => {
            item.display_name()
        }
    }
}

fn group_info(group: BookmarkGroup) -> BookmarkGroupInfo {
    let BookmarkGroup {
        target,
        item_name,
        entry,
        reviewed,
        standing,
        bookmarks,
    } = group;
    let item = target.item.to_string();
    BookmarkGroupInfo {
        key: match &target.entry {
            Some(entry) => format!("{item}/{entry}"),
            None => item.clone(),
        },
        item,
        item_name,
        entry,
        target: target.entry,
        reviewed,
        standing: match standing {
            Standing::Open(entry) => BookmarkStanding::Open {
                entry: entry.to_string(),
            },
            Standing::Pending => BookmarkStanding::Pending,
            Standing::Missing => BookmarkStanding::Missing,
        },
        bookmarks: bookmarks
            .into_iter()
            .map(|bookmark| BookmarkInfo {
                id: bookmark.id.to_string(),
                frame: bookmark.frame,
                time_us: bookmark.time.as_us(),
                kind: match bookmark.kind {
                    BookmarkKind::Note => BookmarkKindWire::Note,
                    BookmarkKind::Issue => BookmarkKindWire::Issue,
                    BookmarkKind::Example => BookmarkKindWire::Example,
                    BookmarkKind::Question => BookmarkKindWire::Question,
                },
                note: bookmark.note,
            })
            .collect(),
    }
}

fn row_info(row: Row, detail: String, audience: Audience) -> RowInfo {
    let Row {
        key,
        item,
        kind,
        label,
        status,
        hidden,
        entry,
        warnings,
        location,
    } = row;
    RowInfo {
        key,
        item: item.to_string(),
        kind: match kind {
            RowKind::Item => RowKindWire::Item,
            RowKind::Playlist => RowKindWire::Playlist,
            RowKind::Entry => RowKindWire::Entry,
            RowKind::Lane => RowKindWire::Lane,
        },
        label,
        detail,
        // Names the host's files and cameras, so only an audience with host details learns it.
        location: location
            .filter(|_| audience.host_details)
            .map(|location| location.text()),
        status: match status {
            RowStatus::Pending => RowStatusInfo::Pending,
            RowStatus::Unavailable(error) => {
                RowStatusInfo::Unavailable(project_error(&error, audience))
            }
            RowStatus::Ready => RowStatusInfo::Ready,
        },
        hidden,
        entry: entry.map(|entry| entry.to_string()),
        // Reasons name host paths, as a lane's warnings do.
        warnings: warnings
            .into_iter()
            .filter(|_| audience.host_details)
            .map(WarningInfo::from)
            .collect(),
    }
}

/// What `source` was made from, for an audience with host details; it names the host's files, so nobody else gets it.
/// A live stream's is [`live_recipe`]'s.
fn item_recipe(label: &str, source: &Source, audience: Audience) -> Option<ItemRecipe> {
    if !audience.host_details {
        return None;
    }
    let label = Some(label.to_owned()).filter(|label| !label.is_empty());
    match source {
        Source::Video(video) => Some(ItemRecipe::Video {
            path: video.video.as_path().to_string_lossy().into_owned(),
            label,
            overlays: video
                .overlays
                .iter()
                .map(|overlay| NewOverlayWire {
                    path: overlay.path.as_path().to_string_lossy().into_owned(),
                    handler: Some(overlay.handler.clone()),
                    label: Some(overlay.label.clone()),
                    options: overlay.options.clone(),
                })
                .collect(),
        }),
        Source::Playlist(playlist) => Some(ItemRecipe::Playlist {
            resolver: playlist.resolver.clone(),
            label,
            settings: playlist.settings.clone(),
        }),
        Source::Live(_) => live_recipe(label.as_deref().unwrap_or_default(), source, audience)
            .map(ItemRecipe::Live),
        Source::Unreadable(_) => None,
    }
}

/// The request that would make a live item again, for an audience with host details. It is made from the stored
/// recipe, so a `$NAME` reference stays one and a literal password is left out.
fn live_recipe(label: &str, source: &Source, audience: Audience) -> Option<Box<NewLiveWire>> {
    match source {
        Source::Live(recipe) if audience.host_details => {
            let mut wire = NewLiveWire::from(recipe);
            wire.label = Some(label.to_owned()).filter(|label| !label.is_empty());
            Some(Box::new(wire))
        }
        Source::Live(_) | Source::Video(_) | Source::Playlist(_) | Source::Unreadable(_) => None,
    }
}

/// `content` of `item` as a viewer opens it; `label` is what the audience calls the item, which a live video shows
/// instead of the camera's host and a video shows after a rename, and `recipe` is what the audience may learn of its connection.
fn content_info(
    item: &WorkspaceItem,
    content: &Content,
    generation: ContentGen,
    label: &str,
    recipe: Option<Box<NewLiveWire>>,
) -> ContentInfo {
    let id = content.id().to_string();
    let generation = generation.0;
    let display_name = content.display_name().to_owned();
    match content {
        Content::SeekableVideo(video) => {
            let SeekableVideoContent { item_id: _, entry } = video;
            // The item's name now, which a rename changes without resolving again.
            ContentInfo::SeekableVideo {
                id,
                generation,
                display_name: label.to_owned(),
                entry: EntryInfo {
                    label: label.to_owned(),
                    ..entry_info(entry)
                },
            }
        }
        Content::Playlist(playlist) => ContentInfo::Playlist {
            id,
            generation,
            display_name,
            entries: playlist.entries.iter().map(entry_info).collect(),
            playlists: playlist
                .playlists
                .iter()
                .map(|span| PlaylistSpanInfo {
                    id: span.id.to_string(),
                    label: playlist_label(item, span, playlist.playlists.len() > 1),
                    entries: u32::try_from(span.entries).unwrap_or(u32::MAX),
                })
                .collect(),
        },
        Content::LiveVideo(live) => {
            let LiveVideoContent {
                item_id: _,
                id: _,
                // The expanded host: the audience's label stands in for it.
                label: _,
                // The camera and the overlay's broker and topic are the host's to connect with, nobody's to read; the
                // recipe passed in is the stored text, which an audience with host details may.
                camera: _,
                overlay,
                slot,
            } = &**live;
            ContentInfo::LiveVideo {
                id,
                generation,
                display_name: label.to_owned(),
                slot: slot.clone(),
                overlay: LiveOverlayKind::from(overlay),
                // The overlay's lane is named by its transport; a video without one has a plain lane.
                lanes: vec![
                    overlay
                        .handler()
                        .map_or_else(|| label.to_owned(), |_| overlay.mode_name().to_owned()),
                ],
                recipe,
            }
        }
    }
}

fn entry_info(entry: &Entry) -> EntryInfo {
    let Entry {
        id,
        label,
        // Where the frames and overlays are on the host: the playback socket opens them, no audience reads them.
        video: _,
        overlays: _,
    } = entry;
    EntryInfo {
        id: id.to_string(),
        label: label.clone(),
        lanes: entry.lane_labels().into_iter().map(str::to_owned).collect(),
    }
}

#[cfg(test)]
mod tests;
