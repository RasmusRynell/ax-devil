//! Bookmarks and reviewed marks: the commands that change them and the groups the Bookmarks view lists.

use crate::{Resolution, WorkspaceState};
use foundation::MediaTime;
use workspace::{Bookmark, BookmarkId, BookmarkKind, BookmarkTarget, Content, ContentId, ItemId};

/// A change to the workspace's bookmarks or reviewed marks.
#[derive(Clone, Debug)]
pub enum BookmarkCommand {
    /// Marks a frame of an entry of a resolved video or playlist item, with kind Note and no text. Does nothing when
    /// the item is not such an item, `entry` is not one of its Content's entries or `time` is negative.
    Add {
        /// Names the new bookmark.
        id: BookmarkId,
        /// The item.
        item: ItemId,
        /// The entry marked: the item's own id for a video.
        entry: ContentId,
        /// The frame's index.
        frame: u64,
        /// The frame's time.
        time: MediaTime,
    },
    /// Changes a bookmark's text (surrounding whitespace dropped) or kind. Does nothing for an unknown bookmark.
    Edit {
        /// The bookmark.
        id: BookmarkId,
        /// The new text, if it changes.
        note: Option<String>,
        /// The new kind, if it changes.
        kind: Option<BookmarkKind>,
    },
    /// Deletes a bookmark. Does nothing for an unknown one.
    Remove(BookmarkId),
    /// Marks a video or entry reviewed or not. Marking needs a target a resolved video or playlist item has now;
    /// clearing is always allowed, so the mark of an entry that left its playlist can go.
    SetReviewed {
        /// What is reviewed.
        target: BookmarkTarget,
        /// Whether it is reviewed afterwards.
        reviewed: bool,
    },
}

/// Whether a group can be opened.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Standing {
    /// The target exists now; opening it opens this entry.
    Open(ContentId),
    /// The item is still resolving.
    Pending,
    /// The item resolved, or failed, without the target: its entry left the playlist or the item cannot be read.
    Missing,
}

/// A video or playlist entry with bookmarks or a reviewed mark, and its bookmarks.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BookmarkGroup {
    /// What the group is about.
    pub target: BookmarkTarget,
    /// The item's name.
    pub item_name: String,
    /// How the entry is shown under its item; empty for a video. The entry's label, with its playlist's name only when
    /// the item has several playlists or the entry is gone.
    pub entry: String,
    /// Whether the target is marked reviewed.
    pub reviewed: bool,
    /// Whether it can be opened.
    pub standing: Standing,
    /// The group's bookmarks by frame, then time.
    pub bookmarks: Vec<Bookmark>,
}

/// Applies `command` to `state`.
pub(crate) fn apply(next: &mut WorkspaceState, command: BookmarkCommand) {
    match command {
        BookmarkCommand::Add {
            id,
            item,
            entry,
            frame,
            time,
        } => {
            if time < MediaTime::ZERO {
                return;
            }
            if let Some(target) = target_of(next, &item, &entry) {
                next.workspace.bookmarks.push(Bookmark {
                    id,
                    target,
                    frame,
                    time,
                    kind: BookmarkKind::Note,
                    note: String::new(),
                });
            }
        }
        BookmarkCommand::Edit { id, note, kind } => {
            if let Some(bookmark) = next.workspace.bookmarks.iter_mut().find(|b| b.id == id) {
                if let Some(note) = note {
                    bookmark.note = note.trim().to_owned();
                }
                if let Some(kind) = kind {
                    bookmark.kind = kind;
                }
            }
        }
        BookmarkCommand::Remove(id) => next.workspace.bookmarks.retain(|b| b.id != id),
        BookmarkCommand::SetReviewed { target, reviewed } => {
            if !reviewed {
                next.workspace.reviewed.remove(&target);
            } else if finds(next, &target) {
                next.workspace.reviewed.insert(target);
            }
        }
    }
}

/// The target of `entry` of `item`, when the item is a resolved video or playlist with that entry.
fn target_of(state: &WorkspaceState, item: &ItemId, entry: &ContentId) -> Option<BookmarkTarget> {
    let Some(Resolution::Resolved { content, .. }) = state.resolutions.get(item) else {
        return None;
    };
    target_in(item, content, entry)
}

fn target_in(item: &ItemId, content: &Content, entry: &ContentId) -> Option<BookmarkTarget> {
    let entry = content.entry(entry)?;
    let own = match content {
        Content::SeekableVideo(_) => None,
        Content::Playlist(_) => Some(entry_text(item, &entry.id)),
        Content::LiveVideo(_) => return None,
    };
    Some(BookmarkTarget {
        item: *item,
        entry: own,
    })
}

/// An entry's text in a target: its id without the item.
fn entry_text(item: &ItemId, id: &ContentId) -> String {
    id.as_str()
        .strip_prefix(&format!("{item}/"))
        .unwrap_or(id.as_str())
        .to_owned()
}

/// Whether the target names something its item's resolved Content has.
fn finds(state: &WorkspaceState, target: &BookmarkTarget) -> bool {
    open_entry(state, target).is_some()
}

/// The entry id and position of the target in its item's Content, when the item has resolved to it.
fn open_entry(state: &WorkspaceState, target: &BookmarkTarget) -> Option<(ContentId, usize)> {
    let Some(Resolution::Resolved { content, .. }) = state.resolutions.get(&target.item) else {
        return None;
    };
    content
        .entries()
        .iter()
        .enumerate()
        .find(|(_, entry)| match (&target.entry, content) {
            (None, Content::SeekableVideo(_)) => true,
            (Some(text), Content::Playlist(_)) => entry_text(&target.item, &entry.id) == *text,
            _ => false,
        })
        .map(|(position, entry)| (entry.id.clone(), position))
}

/// One group per target with a bookmark or a reviewed mark, in the order of the workspace's items, then of the
/// entries their playlists have now, a target its item no longer has an entry for last, by its text. Every bookmark
/// stored is listed, also those of an item this build cannot read.
pub fn groups(state: &WorkspaceState) -> Vec<BookmarkGroup> {
    let workspace = &state.workspace;
    let mut groups = Vec::new();
    for item in &workspace.items {
        let mut targets: Vec<&BookmarkTarget> = workspace
            .reviewed
            .iter()
            .chain(workspace.bookmarks.iter().map(|b| &b.target))
            .filter(|target| target.item == item.id)
            .collect();
        targets.sort();
        targets.dedup();
        let resolution = state.resolutions.get(&item.id);
        let position = |target: &BookmarkTarget| {
            open_entry(state, target).map_or(usize::MAX, |(_, position)| position)
        };
        targets.sort_by_key(|target| position(target));
        for target in targets {
            let found = open_entry(state, target);
            let several = matches!(
                resolution,
                Some(Resolution::Resolved { content: Content::Playlist(playlist), .. })
                    if playlist.playlists.len() > 1
            );
            let entry = match (&found, &target.entry, resolution) {
                (Some((id, _)), Some(_), Some(Resolution::Resolved { content, .. }))
                    if !several =>
                {
                    content
                        .entry(id)
                        .map_or_else(String::new, |e| e.label.clone())
                }
                (_, text, _) => text.clone().unwrap_or_default(),
            };
            let mut bookmarks: Vec<Bookmark> = workspace
                .bookmarks
                .iter()
                .filter(|bookmark| bookmark.target == *target)
                .cloned()
                .collect();
            bookmarks.sort_by_key(|bookmark| (bookmark.frame, bookmark.time));
            let standing = match (found, resolution) {
                (Some((id, _)), _) => Standing::Open(id),
                (None, Some(Resolution::Resolved { .. } | Resolution::Failed(_))) => {
                    Standing::Missing
                }
                (None, Some(Resolution::Pending { .. }) | None) => Standing::Pending,
            };
            groups.push(BookmarkGroup {
                target: target.clone(),
                item_name: item.display_name(),
                entry,
                reviewed: workspace.reviewed.contains(target),
                standing,
                bookmarks,
            });
        }
    }
    groups
}

/// What a kind is called.
pub fn kind_label(kind: BookmarkKind) -> &'static str {
    match kind {
        BookmarkKind::Note => "Note",
        BookmarkKind::Issue => "Issue",
        BookmarkKind::Example => "Example",
        BookmarkKind::Question => "Question",
    }
}

/// Every bookmark of `state` as CSV text, in the order of [`groups`]: a header, then a row each of the item's name,
/// the entry as shown, the frame, the time in seconds to the millisecond, the kind's name and the text. A reviewed
/// target without bookmarks has no row.
pub fn csv(state: &WorkspaceState) -> String {
    let mut text = String::from("target,entry,frame,time,kind,note\r\n");
    for group in groups(state) {
        for bookmark in &group.bookmarks {
            let micros = bookmark.time.as_us();
            // Half a millisecond rounds away from zero; the time is never negative.
            let millis = (micros + 500) / 1000;
            let time = format!("{}.{:03}", millis / 1000, millis % 1000);
            let fields = [
                group.item_name.as_str(),
                group.entry.as_str(),
                &bookmark.frame.to_string(),
                &time,
                kind_label(bookmark.kind),
                bookmark.note.as_str(),
            ];
            let row: Vec<String> = fields.iter().map(|field| quoted(field)).collect();
            text.push_str(&row.join(","));
            text.push_str("\r\n");
        }
    }
    text
}

/// A CSV field, quoted when it holds a comma, a quote or a line break.
fn quoted(field: &str) -> String {
    if field.contains([',', '"', '\n', '\r']) {
        format!("\"{}\"", field.replace('"', "\"\""))
    } else {
        field.to_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{DocCommand, apply as apply_doc, complete};
    use handlers::Handlers;

    fn resolved(path: &std::path::Path) -> (WorkspaceState, ItemId) {
        let item = workspace::intake::video(path, &[], &Handlers::built_in()).unwrap();
        let id = item.id;
        let (state, jobs) = apply_doc(
            &WorkspaceState::default(),
            DocCommand::AddItems {
                items: vec![item],
                open: false,
            },
        );
        let job = &jobs[0];
        let content = crate::resolve(
            job,
            &Handlers::built_in(),
            &workspace::Environment::of_process(),
            &foundation::CancelToken::new(),
        )
        .ok()
        .unwrap();
        (complete(&state, id, job.attempt, Ok(content)), id)
    }

    fn mark(state: &WorkspaceState, item: ItemId, frame: u64, micros: i64) -> WorkspaceState {
        let command = BookmarkCommand::Add {
            id: BookmarkId::generate(),
            item,
            entry: ContentId::of_item(&item),
            frame,
            time: MediaTime::from_us(micros),
        };
        apply_doc(state, DocCommand::Bookmarks(command)).0
    }

    #[test]
    fn a_marked_frame_is_a_note_and_a_bad_mark_changes_nothing() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let (state, item) = resolved(&file);

        let marked = mark(&state, item, 75, 3_000_000);
        let [bookmark] = marked.workspace.bookmarks.as_slice() else {
            panic!("expected one bookmark");
        };
        assert_eq!(
            (bookmark.frame, bookmark.kind, bookmark.note.as_str()),
            (75, BookmarkKind::Note, "")
        );
        assert_eq!(bookmark.target.entry, None);
        assert_eq!(
            mark(&marked, item, 75, 3_000_000).workspace.bookmarks.len(),
            2
        );
        assert_eq!(
            mark(&state, item, 1, -1),
            state,
            "a negative time is refused"
        );
        assert_eq!(
            mark(&state, ItemId::generate(), 1, 0),
            state,
            "an unknown item is refused"
        );
    }

    #[test]
    fn groups_follow_frames_and_a_text_is_trimmed_or_cleared() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let (state, item) = resolved(&file);
        let state = mark(&mark(&state, item, 9, 900_000), item, 2, 200_000);
        let id = state.workspace.bookmarks[0].id;
        let edit = |state: &WorkspaceState, note: &str| {
            let command = BookmarkCommand::Edit {
                id,
                note: Some(note.into()),
                kind: Some(BookmarkKind::Issue),
            };
            apply_doc(state, DocCommand::Bookmarks(command)).0
        };

        let state = edit(&state, "  blur  ");
        let [group] = groups(&state).try_into().unwrap();
        let frames: Vec<u64> = group.bookmarks.iter().map(|b| b.frame).collect();
        assert_eq!(frames, [2, 9]);
        assert_eq!(group.standing, Standing::Open(ContentId::of_item(&item)));
        let edited = &group.bookmarks[1];
        assert_eq!(
            (edited.note.as_str(), edited.kind),
            ("blur", BookmarkKind::Issue)
        );
        assert_eq!(edit(&state, "  ").workspace.bookmarks[0].note, "");
    }

    #[test]
    fn a_reviewed_mark_needs_a_target_but_can_always_be_cleared() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let (state, item) = resolved(&file);
        let set = |state: &WorkspaceState, entry: Option<&str>, reviewed| {
            let target = BookmarkTarget {
                item,
                entry: entry.map(str::to_owned),
            };
            let command = BookmarkCommand::SetReviewed { target, reviewed };
            apply_doc(state, DocCommand::Bookmarks(command)).0
        };

        assert_eq!(set(&state, Some("list/a"), true), state);
        let reviewed = set(&state, None, true);
        let [group] = groups(&reviewed).try_into().unwrap();
        assert!(group.reviewed && group.bookmarks.is_empty());
        assert!(set(&reviewed, None, false).workspace.reviewed.is_empty());
    }

    #[test]
    fn the_csv_lists_every_bookmark_with_quoted_text_and_millisecond_times() {
        let folder = tempfile::tempdir().unwrap();
        let file = folder.path().join("gate.mp4");
        std::fs::write(&file, "x").unwrap();
        let (state, item) = resolved(&file);
        let state = mark(&state, item, 41, 1_366_700);
        let id = state.workspace.bookmarks[0].id;
        let command = BookmarkCommand::Edit {
            id,
            note: Some("a, \"b\"\nc".into()),
            kind: Some(BookmarkKind::Question),
        };
        let state = apply_doc(&state, DocCommand::Bookmarks(command)).0;
        let reviewed = BookmarkCommand::SetReviewed {
            target: BookmarkTarget { item, entry: None },
            reviewed: true,
        };
        let state = apply_doc(&state, DocCommand::Bookmarks(reviewed)).0;

        assert_eq!(
            csv(&state),
            "target,entry,frame,time,kind,note\r\ngate.mp4,,41,1.367,Question,\"a, \"\"b\"\"\nc\"\r\n"
        );
    }

    #[test]
    fn playlist_groups_follow_the_entries_and_an_entry_that_left_is_missing_and_last() {
        let folder = tempfile::tempdir().unwrap();
        for name in ["SEQ-01", "SEQ-02"] {
            std::fs::create_dir_all(folder.path().join(name).join("gt")).unwrap();
            std::fs::write(folder.path().join(name).join("gt/gt.txt"), "").unwrap();
            std::fs::write(
                folder.path().join(name).join("seqinfo.ini"),
                "[Sequence]\nframeRate=25\nseqLength=10\nimWidth=64\nimHeight=48\n",
            )
            .unwrap();
        }
        let mut settings = serde_json::Map::new();
        settings.insert("root".into(), folder.path().display().to_string().into());
        let item =
            workspace::intake::playlist("mot_challenge", settings, &Handlers::built_in()).unwrap();
        let id = item.id;
        let (state, jobs) = apply_doc(
            &WorkspaceState::default(),
            DocCommand::AddItems {
                items: vec![item],
                open: false,
            },
        );
        let content = crate::resolve(
            &jobs[0],
            &Handlers::built_in(),
            &workspace::Environment::of_process(),
            &foundation::CancelToken::new(),
        )
        .ok()
        .unwrap();
        let state = complete(&state, id, jobs[0].attempt, Ok(content));
        let add = |state: &WorkspaceState, entry: &str| {
            let command = BookmarkCommand::Add {
                id: BookmarkId::generate(),
                item: id,
                entry: ContentId::of_entry(&id, "MOT Challenge", entry),
                frame: 3,
                time: MediaTime::from_us(120_000),
            };
            apply_doc(state, DocCommand::Bookmarks(command)).0
        };
        let state = add(&add(&state, "SEQ-02"), "SEQ-01");
        assert_eq!(
            add(&state, "SEQ-09"),
            state,
            "an entry the playlist lacks is refused"
        );
        let mut state = state;
        state.workspace.bookmarks.push(Bookmark {
            id: BookmarkId::generate(),
            target: BookmarkTarget {
                item: id,
                entry: Some("MOT Challenge/SEQ-00".into()),
            },
            frame: 1,
            time: MediaTime::ZERO,
            kind: BookmarkKind::Note,
            note: String::new(),
        });

        let listed = groups(&state);

        let shown: Vec<(&str, bool)> = listed
            .iter()
            .map(|group| (group.entry.as_str(), group.standing == Standing::Missing))
            .collect();
        assert_eq!(
            shown,
            [
                ("SEQ-01", false),
                ("SEQ-02", false),
                ("MOT Challenge/SEQ-00", true)
            ]
        );
        assert_eq!(
            listed[0].target.entry.as_deref(),
            Some("MOT Challenge/SEQ-01")
        );
    }
}
