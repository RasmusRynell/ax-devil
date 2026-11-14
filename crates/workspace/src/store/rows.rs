//! The sidebar's rows: one projection of a workspace state.

use crate::{Content, ContentId, DisplayHost, Entry, ItemId, playlist_label};
use foundation::{UserError, Warning};
use serde::{Deserialize, Serialize};
use ts_rs::TS;

use crate::store::{HiddenRef, Location, Resolution, WorkspaceState};

/// What a row stands for.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum RowKind {
    /// A video or live item, or an item with no Content yet.
    Item,
    /// One playlist of a playlist item.
    Playlist,
    /// An entry of an item's Content.
    Entry,
    /// A lane of an entry.
    Lane,
}

/// Whether what a row stands for can be opened.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RowStatus {
    /// Resolving has not finished.
    Pending,
    /// The item has no Content, and why.
    Unavailable(UserError),
    /// Ready to open.
    Ready,
}

/// One row of the workspace tree.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    /// Names the row for as long as what it stands for exists.
    pub key: String,
    /// The item the row belongs to, which a viewer is opened on.
    pub item: ItemId,
    /// What the row stands for.
    pub kind: RowKind,
    /// What the user reads.
    pub label: String,
    /// Whether it can be opened.
    pub status: RowStatus,
    /// Whether the user hid it.
    pub hidden: bool,
    /// The entry that opening the row opens.
    pub entry: Option<ContentId>,
    /// What resolving left out of an item's Content; empty for entries and lanes.
    pub warnings: Vec<Warning>,
    /// Where it is read from, which tells same-named rows apart; none for a playlist and for a row not resolved.
    pub location: Option<Location>,
}

/// The rows of `state` in tree order. A playlist item has no row of its own: each of its playlists is a top-level
/// row, however many there are, named by [`playlist_label`], followed by the playlist's entries, each followed by
/// its lanes. The item's warnings are on its first playlist row.
///
/// A video item is one row followed by its lanes (none when it has no overlays), since its single entry would only
/// repeat the item. Opening a video row opens its entry, and a playlist row its first entry that is not hidden (the first if all are); opening an entry or lane row opens that
/// entry. An item still resolving, or one that failed, has only its own row, named by its display name. Hidden rows
/// are included and flagged: whether to list them is the reader's choice.
pub fn rows(state: &WorkspaceState) -> Vec<Row> {
    let mut rows = Vec::new();
    for item in &state.workspace.items {
        let id = item.id;
        let row = |key: String, kind, label: String, entry: Option<&Entry>, hidden| Row {
            key,
            item: id,
            kind,
            label,
            status: RowStatus::Ready,
            hidden,
            entry: entry.map(|entry| entry.id.clone()),
            warnings: Vec::new(),
            location: None,
        };
        let entry_rows = |rows: &mut Vec<Row>, entry: &Entry, own_row: bool| {
            if own_row {
                rows.push(Row {
                    location: Some(Location::of_video(&entry.video)),
                    ..row(
                        entry.id.to_string(),
                        RowKind::Entry,
                        entry.label.clone(),
                        Some(entry),
                        state.entry_hidden(entry),
                    )
                });
            }
            let hidden_lanes = entry.lanes_hidden_at_first();
            for overlay in &entry.overlays {
                rows.push(Row {
                    location: overlay
                        .file()
                        .map(|file| Location::File(file.to_path_buf())),
                    ..row(
                        format!("{}{KEY_SEPARATOR}{}", entry.id, overlay.label),
                        RowKind::Lane,
                        overlay.label.clone(),
                        Some(entry),
                        state.is_hidden(
                            &HiddenRef::Lane(entry.id.clone(), overlay.label.clone()),
                            hidden_lanes.contains(overlay.label.as_str()),
                        ),
                    )
                });
            }
        };
        match state.resolutions.get(&item.id) {
            Some(Resolution::Resolved { content, .. }) => match content {
                Content::SeekableVideo(video) => {
                    rows.push(Row {
                        location: Some(Location::of_video(&video.entry.video)),
                        ..row(
                            item.id.to_string(),
                            RowKind::Item,
                            item.display_name(),
                            Some(&video.entry),
                            false,
                        )
                    });
                    entry_rows(&mut rows, &video.entry, false);
                }
                // A live video has no entry; its one stream is named by its own id.
                Content::LiveVideo(live) => {
                    rows.push(Row {
                        entry: Some(live.id.clone()),
                        // The resolved host is what a variable held, which may carry a login: only a plain one is a place.
                        location: DisplayHost::of(&live.camera.host).map(|host| Location::Camera {
                            host,
                            head: live.camera.head,
                        }),
                        ..row(
                            item.id.to_string(),
                            RowKind::Item,
                            item.display_name(),
                            None,
                            false,
                        )
                    });
                    if live.overlay.handler().is_some() {
                        rows.push(Row {
                            entry: Some(live.id.clone()),
                            ..row(
                                format!("{}{KEY_SEPARATOR}{}", live.id, live.slot),
                                RowKind::Lane,
                                live.overlay.mode_name().to_owned(),
                                None,
                                false,
                            )
                        });
                    }
                }
                Content::Playlist(playlist) => {
                    let several = playlist.playlists.len() > 1;
                    let mut entries = playlist.entries.iter();
                    for (at, span) in playlist.playlists.iter().enumerate() {
                        let own: Vec<&Entry> = entries.by_ref().take(span.entries).collect();
                        let mut first = row(
                            span.id.to_string(),
                            RowKind::Playlist,
                            playlist_label(item, span, several),
                            own.iter()
                                .find(|entry| !state.entry_hidden(entry))
                                .or(own.first())
                                .copied(),
                            false,
                        );
                        if at == 0 {
                            first.warnings = playlist.warnings.clone();
                        }
                        rows.push(first);
                        for entry in own {
                            entry_rows(&mut rows, entry, true);
                        }
                    }
                }
            },
            other => rows.push(Row {
                status: match other {
                    Some(Resolution::Failed(error)) => RowStatus::Unavailable(error.clone()),
                    _ => RowStatus::Pending,
                },
                ..row(
                    item.id.to_string(),
                    RowKind::Item,
                    item.display_name(),
                    None,
                    false,
                )
            }),
        }
    }
    rows
}

/// Between an entry id and a lane label in a lane row's key; no file name holds it, so keys never collide.
const KEY_SEPARATOR: char = '\u{1f}';

/// Visits the types this module sends to the frontend.
pub(crate) fn visit_api_types(types: &mut impl ts_rs::TypeVisitor) {
    types.visit::<RowKind>();
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::{DocCommand, apply, complete};
    use crate::{
        Environment, WorkspaceItem,
        intake::{self, NewOverlay},
    };
    use foundation::{CancelToken, ErrorCode, Interrupt};
    use handlers::Handlers;
    use std::{collections::HashSet, fs, path::Path};

    /// What finishing an item's job commits: its Content, or `error` when given.
    type Finish = Option<Result<(), UserError>>;

    /// A state with `items` added in order, each finished as given: `None` stays pending, `Some(Ok(()))` resolves
    /// and `Some(Err(error))` fails with `error`.
    fn state_of(items: Vec<(WorkspaceItem, Finish)>) -> WorkspaceState {
        let (finishes, items): (Vec<_>, Vec<_>) = items
            .into_iter()
            .map(|(item, finish)| ((item.id, item.recipe.clone(), finish), item))
            .unzip();
        let (mut state, jobs) = apply(
            &WorkspaceState::default(),
            DocCommand::AddItems { items, open: false },
        );
        for ((id, recipe, finish), job) in finishes.into_iter().zip(jobs) {
            let result = match finish {
                None => continue,
                Some(Err(error)) => Err(error),
                Some(Ok(())) => {
                    match recipe.resolve(
                        &id,
                        "",
                        &Handlers::built_in(),
                        &Environment::of_process(),
                        &CancelToken::new(),
                    ) {
                        Ok(content) => Ok(content),
                        Err(Interrupt::Failed(error)) => {
                            panic!("the fixture item resolves: {error}")
                        }
                        Err(Interrupt::Cancelled) => panic!("nothing cancels"),
                    }
                }
            };
            state = complete(&state, job.item, job.attempt, result);
        }
        state
    }

    fn mot_item(root: &Path) -> WorkspaceItem {
        let mut settings = serde_json::Map::new();
        settings.insert("root".into(), root.display().to_string().into());
        intake::playlist("mot_challenge", settings, &Handlers::built_in()).unwrap()
    }

    fn mot_folder(root: &Path) {
        for name in ["SEQ-01", "SEQ-02"] {
            fs::create_dir_all(root.join(name).join("gt")).unwrap();
            fs::write(root.join(name).join("gt/gt.txt"), "").unwrap();
            fs::write(
                root.join(name).join("seqinfo.ini"),
                "[Sequence]\nframeRate=25\nseqLength=10\nimWidth=64\nimHeight=48\n",
            )
            .unwrap();
        }
        fs::create_dir_all(root.join("SEQ-03")).unwrap();
        fs::write(
            root.join("SEQ-03/seqinfo.ini"),
            "[Sequence]\nimWidth=wide\n",
        )
        .unwrap();
    }

    fn summary(rows: &[Row]) -> Vec<(RowKind, &str, bool)> {
        rows.iter()
            .map(|row| (row.kind, row.label.as_str(), row.hidden))
            .collect()
    }

    #[test]
    fn rows_follow_the_items_with_pending_and_unavailable_rows_alone_then_entries_and_their_lanes()
    {
        let folder = tempfile::tempdir().unwrap();
        mot_folder(folder.path());
        let (gt, video) = (
            folder.path().join("SEQ-01/gt/gt.txt"),
            folder.path().join("v.mp4"),
        );
        fs::write(&video, "x").unwrap();
        let handlers = Handlers::built_in();
        let with_lane = intake::video(
            &video,
            &[NewOverlay {
                path: gt,
                handler: None,
                label: None,
                options: [("width", 64), ("height", 48)]
                    .map(|(key, size)| (key.to_owned(), size.into()))
                    .into_iter()
                    .collect(),
            }],
            &handlers,
        )
        .unwrap();
        let playlist = mot_item(folder.path());
        let pending = intake::video(&video, &[], &handlers).unwrap();
        let broken = intake::video(&folder.path().join("gone.mp4"), &[], &handlers).unwrap();
        let not_found = UserError::new(ErrorCode::FileNotFound, "File not found");
        let mut state = state_of(vec![
            (pending, None),
            (broken, Some(Err(not_found.clone()))),
            (playlist.clone(), Some(Ok(()))),
            (with_lane.clone(), Some(Ok(()))),
        ]);

        let listed = rows(&state);

        assert_eq!(
            summary(&listed),
            [
                (RowKind::Item, "v.mp4", false),
                (RowKind::Item, "gone.mp4", false),
                (RowKind::Playlist, "MOT Challenge", false),
                (RowKind::Entry, "SEQ-01", false),
                (RowKind::Lane, "GT", false),
                (RowKind::Entry, "SEQ-02", false),
                (RowKind::Lane, "GT", false),
                (RowKind::Item, "v.mp4", false),
                (RowKind::Lane, "gt", false),
            ]
        );
        // The first row of a kind and label: the pending video, the playlist's first lane.
        let row = |kind, label: &str| {
            listed
                .iter()
                .find(|row| row.kind == kind && row.label == label)
                .unwrap()
        };
        let (pending_row, broken_row) =
            (row(RowKind::Item, "v.mp4"), row(RowKind::Item, "gone.mp4"));
        assert_eq!(pending_row.status, RowStatus::Pending);
        assert_eq!(broken_row.status, RowStatus::Unavailable(not_found));
        assert!(pending_row.entry.is_none() && broken_row.entry.is_none());
        let first = ContentId::of_entry(&playlist.id, "MOT Challenge", "SEQ-01");
        let mot_row = row(RowKind::Playlist, "MOT Challenge");
        assert_eq!(
            mot_row.entry,
            Some(first.clone()),
            "a playlist opens its first entry"
        );
        assert_eq!(row(RowKind::Lane, "GT").entry, Some(first.clone()));
        assert_eq!(
            mot_row.warnings.len(),
            1,
            "the skipped sequence is warned of"
        );
        assert!(
            listed
                .iter()
                .all(|row| row.key == mot_row.key || row.warnings.is_empty())
        );
        let keys: HashSet<_> = listed.iter().map(|row| &row.key).collect();
        assert_eq!(keys.len(), listed.len(), "keys are unique");
        assert!(
            listed[2..7].iter().all(|row| row.item == playlist.id),
            "every row of a playlist item names the item"
        );

        state
            .hidden
            .insert(HiddenRef::Lane(first.clone(), "GT".into()), true);
        state.hidden.insert(
            HiddenRef::Entry(ContentId::of_entry(&playlist.id, "MOT Challenge", "SEQ-02")),
            true,
        );
        state.hidden.insert(
            HiddenRef::Entry(ContentId::of_entry(&playlist.id, "MOT Challenge", "SEQ-99")),
            true,
        );
        let hidden: Vec<_> = rows(&state).iter().map(|row| row.hidden).collect();
        assert_eq!(
            hidden,
            [false, false, false, false, true, true, false, false, false],
            "an unknown reference hides nothing"
        );
    }

    #[test]
    fn each_playlist_is_a_top_level_row_named_by_the_label_only_when_there_are_several() {
        let playlist = |item: &WorkspaceItem, name: &str, labels: &[&str]| {
            let entries: Vec<_> = labels
                .iter()
                .map(|label| crate::Entry {
                    id: ContentId::of_entry(&item.id, name, label),
                    label: (*label).to_owned(),
                    video: crate::VideoRef::File("/v.mp4".into()),
                    overlays: Vec::new(),
                    starts_hidden: *label == "z",
                    lanes_starting_hidden: Vec::new(),
                    metadata: Default::default(),
                })
                .collect();
            let span = crate::PlaylistSpan {
                id: ContentId::of_playlist(&item.id, name),
                name: name.to_owned(),
                entries: entries.len(),
                matches: None,
                sequence_groups: None,
                metadata: Default::default(),
            };
            (span, entries)
        };
        let content = |item: &WorkspaceItem, playlists: Vec<(crate::PlaylistSpan, Vec<_>)>| {
            let (playlists, entries): (Vec<_>, Vec<_>) = playlists.into_iter().unzip();
            Content::Playlist(crate::PlaylistContent {
                id: ContentId::of_item(&item.id),
                item_id: item.id,
                display_name: "Runs".into(),
                entries: entries.concat(),
                playlists,
                warnings: Vec::new(),
            })
        };
        let named = |label: &str| WorkspaceItem {
            label: label.into(),
            ..mot_item(Path::new("/runs"))
        };
        let (several, solo, plain) = (named("Mine"), named("Solo"), named(""));
        let (mut state, jobs) = apply(
            &WorkspaceState::default(),
            DocCommand::AddItems {
                items: vec![several.clone(), solo.clone(), plain.clone()],
                open: false,
            },
        );
        for (item, playlists) in [
            (
                &several,
                vec![
                    playlist(&several, "A", &["x"]),
                    playlist(&several, "A (2)", &["y", "z"]),
                ],
            ),
            (&solo, vec![playlist(&solo, "Run", &["w"])]),
            (&plain, vec![playlist(&plain, "Run", &["v"])]),
        ] {
            let job = jobs.iter().find(|job| job.item == item.id).unwrap();
            state = complete(&state, job.item, job.attempt, Ok(content(item, playlists)));
        }

        let listed = rows(&state);

        let kinds: Vec<_> = listed
            .iter()
            .map(|row| (row.kind, row.label.as_str()))
            .collect();
        assert_eq!(
            kinds,
            [
                (RowKind::Playlist, "Mine / A"),
                (RowKind::Entry, "x"),
                (RowKind::Playlist, "Mine / A (2)"),
                (RowKind::Entry, "y"),
                (RowKind::Entry, "z"),
                (RowKind::Playlist, "Solo"),
                (RowKind::Entry, "w"),
                (RowKind::Playlist, "Run"),
                (RowKind::Entry, "v"),
            ]
        );
        let hidden: Vec<_> = listed.iter().filter(|row| row.hidden).collect();
        assert_eq!(hidden.len(), 1, "an entry its resolver hides starts hidden");
        assert_eq!(hidden[0].label, "z");
        assert_eq!(
            listed[2].key,
            ContentId::of_playlist(&several.id, "A (2)").to_string()
        );
        assert_eq!(
            listed[2].entry, listed[3].entry,
            "a playlist opens its first entry"
        );
    }
}
