//! What an audience may learn of the Doc: the only constructor of [`DocWire`].
//!
//! The function starts with an exhaustive destructure of `Doc` (no `..`; `scripts/check-architecture.cjs` rejects a
//! rest pattern in this file), so a new Doc field does not compile until its projection is decided here. An audience
//! without host details sees only items the Doc has open, with their entries and rows, and for a failed item only its
//! public code and the item id: never a path, a recipe or the reason text. What a user wrote, such as labels and the
//! workspace name, is shared on purpose.

use api_types::{
    Audience, DocWire,
    content::{ContentInfo, EntryInfo},
    project as project_error,
    workspace::{ItemState, ResolutionState, RowInfo, RowKindWire, RowStatusInfo, WorkspaceState},
};
use sessions::session::Doc;
use std::collections::HashSet;
use workspace::{Content, Entry, SeekableVideoContent, Workspace, WorkspaceItem};
use workspace_store::{ContentGen, Resolution, Row, RowKind, RowStatus, WorkspaceParts, rows};

/// `doc` as `audience` may see it.
pub fn project(doc: &Doc, audience: Audience) -> DocWire {
    let Doc { workspace: state } = doc;
    let WorkspaceParts {
        workspace,
        resolutions,
        open,
        // The rows carry what the user hid.
        hidden: _,
    } = state.parts();
    let Workspace { name, items } = workspace;
    let sees_everything = audience.host_details;
    let listed: Vec<&WorkspaceItem> = items
        .iter()
        .filter(|item| sees_everything || open.contains(&item.id))
        .collect();
    let keys: HashSet<String> = listed.iter().map(|item| item.id.to_string()).collect();
    let items = listed
        .into_iter()
        .map(|item| {
            let WorkspaceItem {
                id,
                label,
                recipe: _,
            } = item;
            ItemState {
                id: id.to_string(),
                label: label.clone(),
                open: open.contains(id),
                resolution: match resolutions.get(id) {
                    Some(Resolution::Resolved {
                        content,
                        generation,
                    }) => ResolutionState::Resolved {
                        content: content_info(content, *generation),
                    },
                    Some(Resolution::Failed(error)) => {
                        ResolutionState::Failed(project_error(error, audience))
                    }
                    Some(Resolution::Pending { attempt: _ }) | None => ResolutionState::Pending,
                },
            }
        })
        .collect();
    // Rows come in tree order, each item's own row first, so the rows of an unlisted item are the ones between its
    // row and the next item's.
    let mut shown = false;
    let rows = rows(state)
        .into_iter()
        .filter(|row| {
            if row.kind == RowKind::Item {
                shown = keys.contains(&row.key);
            }
            shown
        })
        .map(|row| row_info(row, audience))
        .collect();
    DocWire {
        workspace: WorkspaceState {
            name: name.clone(),
            items,
            rows,
        },
    }
}

fn row_info(row: Row, audience: Audience) -> RowInfo {
    let Row {
        key,
        kind,
        label,
        status,
        hidden,
        entry,
    } = row;
    RowInfo {
        key,
        kind: match kind {
            RowKind::Item => RowKindWire::Item,
            RowKind::Entry => RowKindWire::Entry,
            RowKind::Lane => RowKindWire::Lane,
        },
        label,
        status: match status {
            RowStatus::Pending => RowStatusInfo::Pending,
            RowStatus::Unavailable(error) => {
                RowStatusInfo::Unavailable(project_error(&error, audience))
            }
            RowStatus::Ready => RowStatusInfo::Ready,
        },
        hidden,
        entry: entry.map(|entry| entry.to_string()),
    }
}

fn content_info(content: &Content, generation: ContentGen) -> ContentInfo {
    let id = content.id().to_string();
    let generation = generation.0;
    let display_name = content.display_name().to_owned();
    match content {
        Content::SeekableVideo(video) => {
            let SeekableVideoContent { item_id: _, entry } = video;
            ContentInfo::SeekableVideo {
                id,
                generation,
                display_name,
                entry: entry_info(entry),
            }
        }
        Content::Playlist(playlist) => ContentInfo::Playlist {
            id,
            generation,
            display_name,
            entries: playlist.entries.iter().map(entry_info).collect(),
        },
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
mod tests {
    use super::*;
    use foundation::{ErrorCode, HostPath, ItemId, UserError};
    use std::path::PathBuf;
    use workspace::{ContentId, OverlayRef, PlaylistContent, Recipe, VideoRecipe, VideoRef};
    use workspace_store::{HiddenRef, WorkspaceState as State};

    fn item(id: ItemId, label: &str, folder: &str) -> WorkspaceItem {
        WorkspaceItem {
            id,
            label: label.into(),
            recipe: Recipe::Video(VideoRecipe {
                video: HostPath::new(format!("/home/{folder}/{label}")),
                overlays: Vec::new(),
            }),
        }
    }

    fn entry(id: ContentId, label: &str, folder: &str) -> Entry {
        Entry {
            id,
            label: label.into(),
            video: VideoRef::File(PathBuf::from(format!("/home/{folder}/{label}"))),
            overlays: vec![OverlayRef {
                label: "tracks.txt".into(),
                path: PathBuf::from(format!("/home/{folder}/tracks.txt")),
                handler: "MOT_FILE".into(),
                options: Default::default(),
            }],
        }
    }

    /// A Doc with a resolved open video, a resolved open playlist, a failed open item and two items with no viewer
    /// (a failed one and a resolved playlist), all carrying sentinels in every place a watcher must not learn of.
    fn doc() -> (Doc, [ItemId; 5]) {
        let ids = [(); 5].map(|()| ItemId::generate());
        let [shown, playlist, failed, closed, closed_playlist] = ids;
        let video = Content::SeekableVideo(SeekableVideoContent {
            item_id: shown,
            entry: entry(ContentId::of_item(&shown), "gate.mp4", "SECRET-PATH-A"),
        });
        let playlist_of = |item: ItemId, name: &str, folder: &str| {
            Content::Playlist(PlaylistContent {
                id: ContentId::of_item(&item),
                item_id: item,
                display_name: name.into(),
                entries: ["SEQ-01", "SEQ-02"]
                    .map(|label| entry(ContentId::of_entry(&item, label), label, folder))
                    .into(),
            })
        };
        let failure = |folder: &str| {
            let reason = "SECRET-REASON rtsp://root:SECRET-PASSWORD@camera";
            UserError::new(ErrorCode::FileUnreadable, reason)
                .at(HostPath::new(format!("/home/{folder}/lost.mp4")))
        };
        let mut state = State::default();
        state.workspace.name = Some("Review".into());
        state.workspace.items = vec![
            item(shown, "gate.mp4", "SECRET-PATH-A"),
            item(playlist, "MOT sequences", "SECRET-PATH-D"),
            item(failed, "lost.mp4", "SECRET-PATH-B"),
            item(closed, "closed.mp4", "SECRET-PATH-C"),
            item(closed_playlist, "closed playlist", "SECRET-PATH-E"),
        ];
        let resolved = |content| Resolution::Resolved {
            content,
            generation: ContentGen(1),
        };
        state.resolutions.insert(shown, resolved(video));
        state.resolutions.insert(
            playlist,
            resolved(playlist_of(playlist, "MOT sequences", "SECRET-PATH-D")),
        );
        state.resolutions.insert(
            closed_playlist,
            resolved(playlist_of(
                closed_playlist,
                "closed playlist",
                "SECRET-PATH-E",
            )),
        );
        state
            .resolutions
            .insert(failed, Resolution::Failed(failure("SECRET-PATH-B")));
        state
            .resolutions
            .insert(closed, Resolution::Failed(failure("SECRET-PATH-C")));
        state.open.extend([shown, playlist, failed]);
        state
            .hidden
            .insert(HiddenRef::Entry(ContentId::of_entry(&playlist, "SEQ-02")));
        (Doc { workspace: state }, ids)
    }

    #[test]
    fn a_watcher_learns_no_path_credential_or_reason_and_sees_only_what_is_open() {
        let (doc, [_, _, failed, _, _]) = doc();
        let public = Audience {
            host_details: false,
        };
        let watcher = serde_json::to_string(&project(&doc, public)).unwrap();
        for secret in [
            "SECRET",
            "/home",
            "rtsp",
            "closed.mp4",
            "closed playlist",
            "location",
        ] {
            assert!(!watcher.contains(secret), "{secret} reached {watcher}");
        }
        for shared in [
            "gate.mp4",
            "MOT sequences",
            "SEQ-01",
            "tracks.txt",
            "Review",
            "FileUnreadable",
            &failed.to_string(),
        ] {
            assert!(watcher.contains(shared), "{shared} missing from {watcher}");
        }

        let owner = serde_json::to_string(&project(&doc, Audience::HOST)).unwrap();
        for host_only in [
            "SECRET-REASON",
            "SECRET-PATH-B",
            "closed.mp4",
            "closed playlist",
        ] {
            assert!(
                owner.contains(host_only),
                "{host_only} missing from {owner}"
            );
        }
        for recipe_or_source in ["SECRET-PATH-A", "SECRET-PATH-D", "SECRET-PATH-E"] {
            assert!(
                !owner.contains(recipe_or_source),
                "{recipe_or_source} reached {owner}"
            );
        }
    }

    #[test]
    fn rows_follow_the_items_a_watcher_sees_and_carry_what_was_hidden() {
        let (doc, _) = doc();
        let public = Audience {
            host_details: false,
        };
        let rows = |audience| -> Vec<(String, bool)> {
            project(&doc, audience)
                .workspace
                .rows
                .into_iter()
                .map(|row| (row.label, row.hidden))
                .collect()
        };

        let watcher = rows(public);
        assert!(watcher.contains(&("SEQ-02".into(), true)), "{watcher:?}");
        assert!(watcher.contains(&("SEQ-01".into(), false)), "{watcher:?}");
        assert!(!watcher.iter().any(|(label, _)| label.starts_with("closed")));
        let owner = rows(Audience::HOST);
        assert!(owner.iter().any(|(label, _)| label == "closed playlist"));
    }
}
