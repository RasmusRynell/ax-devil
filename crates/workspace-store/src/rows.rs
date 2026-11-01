//! The sidebar's rows: one projection of a workspace state.

use foundation::UserError;
use workspace::{Content, ContentId};

use crate::{HiddenRef, Resolution, WorkspaceState};

/// What a row stands for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RowKind {
    /// A workspace item.
    Item,
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
}

/// The rows of `state` in tree order: each item, then for a playlist its entries, each followed by its lanes.
///
/// A video item is one row followed by its lanes (none when it has no overlays), since its single entry would only
/// repeat the item. Opening an item row opens its first entry; opening an entry or lane row opens that entry. An
/// item still resolving, or one that failed, has only its own row. Hidden rows are included and flagged: whether
/// to list them is the reader's choice.
pub fn rows(state: &WorkspaceState) -> Vec<Row> {
    let mut rows = Vec::new();
    for item in &state.workspace.items {
        let (status, content) = match state.resolutions.get(&item.id) {
            Some(Resolution::Resolved { content, .. }) => (RowStatus::Ready, Some(content)),
            Some(Resolution::Failed(error)) => (RowStatus::Unavailable(error.clone()), None),
            Some(Resolution::Pending { .. }) | None => (RowStatus::Pending, None),
        };
        let is_playlist = matches!(content, Some(Content::Playlist(_)));
        rows.push(Row {
            key: item.id.to_string(),
            kind: RowKind::Item,
            label: item.label.clone(),
            status,
            hidden: content
                .filter(|_| !is_playlist)
                .is_some_and(|video| state.is_hidden(&HiddenRef::Entry(video.id().clone()))),
            entry: content
                .and_then(|content| content.entries().first())
                .map(|entry| entry.id.clone()),
        });
        let Some(content) = content else {
            continue;
        };
        for entry in content.entries() {
            if is_playlist {
                rows.push(Row {
                    key: entry.id.to_string(),
                    kind: RowKind::Entry,
                    label: entry.label.clone(),
                    status: RowStatus::Ready,
                    hidden: state.is_hidden(&HiddenRef::Entry(entry.id.clone())),
                    entry: Some(entry.id.clone()),
                });
            }
            for overlay in &entry.overlays {
                rows.push(Row {
                    key: format!("{}{KEY_SEPARATOR}{}", entry.id, overlay.label),
                    kind: RowKind::Lane,
                    label: overlay.label.clone(),
                    status: RowStatus::Ready,
                    hidden: state
                        .is_hidden(&HiddenRef::Lane(entry.id.clone(), overlay.label.clone())),
                    entry: Some(entry.id.clone()),
                });
            }
        }
    }
    rows
}

/// Between an entry id and a lane label in a lane row's key; no file name holds it, so keys never collide.
const KEY_SEPARATOR: char = '\u{1f}';

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{DocCommand, apply, complete};
    use foundation::{CancelToken, ErrorCode, Interrupt};
    use handlers::Handlers;
    use std::{collections::HashSet, fs, path::Path};
    use workspace::{
        WorkspaceItem,
        intake::{self, NewOverlay},
    };

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
                    match recipe.resolve(&id, &Handlers::built_in(), &CancelToken::new()) {
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
        intake::playlist("mot_challenge", settings, None, &Handlers::built_in()).unwrap()
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
                (RowKind::Item, "MOT Challenge", false),
                (RowKind::Entry, "SEQ-01", false),
                (RowKind::Lane, "GT", false),
                (RowKind::Entry, "SEQ-02", false),
                (RowKind::Lane, "GT", false),
                (RowKind::Item, "v.mp4", false),
                (RowKind::Lane, "gt.txt", false),
            ]
        );
        assert_eq!(listed[0].status, RowStatus::Pending);
        assert_eq!(listed[1].status, RowStatus::Unavailable(not_found));
        assert!(listed[..2].iter().all(|row| row.entry.is_none()));
        let first = ContentId::of_entry(&playlist.id, "SEQ-01");
        assert_eq!(
            listed[2].entry,
            Some(first.clone()),
            "the item opens its first entry"
        );
        assert_eq!(listed[4].entry, Some(first.clone()));
        let keys: HashSet<_> = listed.iter().map(|row| &row.key).collect();
        assert_eq!(keys.len(), listed.len(), "keys are unique");

        state
            .hidden
            .insert(HiddenRef::Lane(first.clone(), "GT".into()));
        state.hidden.insert(HiddenRef::Entry(ContentId::of_entry(
            &playlist.id,
            "SEQ-02",
        )));
        state.hidden.insert(HiddenRef::Entry(ContentId::of_entry(
            &playlist.id,
            "SEQ-99",
        )));
        let hidden: Vec<_> = rows(&state).iter().map(|row| row.hidden).collect();
        assert_eq!(
            hidden,
            [false, false, false, false, true, true, false, false, false],
            "an unknown reference hides nothing"
        );
    }
}
