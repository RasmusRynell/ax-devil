use crate::item::WorkspaceItem;
use foundation::{ErrorCode, ItemId, UserError, Warning};
use handler_api::ResolvedPlaylists;
use serde::{Deserialize, Deserializer, Serialize, Serializer, de};
use std::{
    collections::{HashMap, HashSet},
    fmt,
};

pub use handler_api::{ImageSequence, OverlayRef, VideoRef};

/// Names one Content, one playlist of a playlist item, or one entry of a playlist, for as long as its item exists.
/// Derived from the item id (and the playlist and entry names), so it names the same thing after the item resolves
/// again. It travels as its text, and text that is
/// not a Content or entry id does not deserialize.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct ContentId(String);

impl ContentId {
    /// The id of the Content a single-Content item resolves to.
    pub fn of_item(item: &ItemId) -> Self {
        Self(item.to_string())
    }

    /// The id of the playlist `name` of the playlist item `item`: `{item}/{name}`. `name` is the numbered name, so
    /// the ids of one item differ.
    pub fn of_playlist(item: &ItemId, name: &str) -> Self {
        Self(format!("{item}/{name}"))
    }

    /// The id of the entry `entry` of the playlist `playlist` of the item `item`: `{item}/{playlist}/{entry}`. The
    /// playlist is part of it, so it stays the same when the resolver later returns a second playlist.
    pub fn of_entry(item: &ItemId, playlist: &str, entry: &str) -> Self {
        Self(format!("{item}/{playlist}/{entry}"))
    }

    /// The id as text, the form that crosses the wire.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl Serialize for ContentId {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.0)
    }
}

/// Only `<item>` or `<item>/<rest>` deserializes, so nothing a client sends can name a Content, playlist or entry no
/// item could have.
impl<'de> Deserialize<'de> for ContentId {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let text = String::deserialize(deserializer)?;
        let (item, rest) = match text.split_once('/') {
            Some((item, rest)) => (item, Some(rest)),
            None => (text.as_str(), None),
        };
        let item =
            ItemId::parse(item).ok_or_else(|| de::Error::custom("that is not a content id"))?;
        Ok(match rest {
            Some(rest) if !rest.is_empty() => Self(format!("{item}/{rest}")),
            Some(_) => return Err(de::Error::custom("that is not a content id")),
            None => Self::of_item(&item),
        })
    }
}

impl fmt::Display for ContentId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// One video and the overlays drawn on it: what a viewer opens.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    /// Names the entry; viewers open an entry by this id.
    pub id: ContentId,
    /// The name rows and viewers show; unique within a playlist.
    pub label: String,
    /// Where the frames come from.
    pub video: VideoRef,
    /// The overlays, each shown as its own lane; labels and slots are unique within the entry.
    pub overlays: Vec<OverlayRef>,
}

impl Entry {
    /// The lane names: the overlay labels, or the entry's own label for the one plain lane of an entry without
    /// overlays.
    pub fn lane_labels(&self) -> Vec<&str> {
        self.lane_names(|overlay| &overlay.label)
    }

    /// The lane slots: the overlay slots, or the entry's own label for the one plain lane of an entry without
    /// overlays.
    pub fn lane_slots(&self) -> Vec<&str> {
        self.lane_names(|overlay| &overlay.slot)
    }

    fn lane_names(&self, of: impl Fn(&OverlayRef) -> &String) -> Vec<&str> {
        if self.overlays.is_empty() {
            vec![self.label.as_str()]
        } else {
            self.overlays
                .iter()
                .map(|overlay| of(overlay).as_str())
                .collect()
        }
    }
}

/// A recorded video a viewer can seek in, with the overlays added to it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SeekableVideoContent {
    /// The item it was resolved from.
    pub item_id: ItemId,
    /// The video and its overlays; its id is the item id.
    pub entry: Entry,
}

/// One playlist of a [`PlaylistContent`]: the next run of its entries.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PlaylistSpan {
    /// Names the playlist: `{item}/{name}`.
    pub id: ContentId,
    /// What rows show: the name the resolver gave it, numbered ` (2)`, ` (3)` when another playlist of the item has
    /// the same name.
    pub name: String,
    /// How many entries it holds; the spans cover the entries in order, so the first span starts at entry 0.
    pub entries: usize,
}

/// What a playlist of `item` is called: the playlist's own name when the item is unlabelled; else the label, followed
/// by ` / ` and the playlist's name when the item has `several` playlists. Derived each time, never stored.
pub fn playlist_label(item: &WorkspaceItem, span: &PlaylistSpan, several: bool) -> String {
    match (item.label.as_str(), several) {
        ("", _) => span.name.clone(),
        (label, true) => format!("{label} / {}", span.name),
        (label, false) => label.to_owned(),
    }
}

/// Several videos in order, each opened as an entry; the playlists of an item split them into runs.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PlaylistContent {
    /// Names this Content.
    pub id: ContentId,
    /// The item it was resolved from.
    pub item_id: ItemId,
    /// The name rows and viewers show: the resolver's.
    pub display_name: String,
    /// The entries of every playlist, in order.
    pub entries: Vec<Entry>,
    /// The playlists, each covering the next run of `entries`; at least one, none empty.
    pub playlists: Vec<PlaylistSpan>,
    /// What the resolver left out of the playlist.
    pub warnings: Vec<Warning>,
}

/// What a viewer opens, by kind. Viewers register by kind; live sources join this set later.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Content {
    /// A recorded video.
    SeekableVideo(SeekableVideoContent),
    /// A playlist of recorded videos.
    Playlist(PlaylistContent),
}

impl Content {
    /// Names this Content.
    pub fn id(&self) -> &ContentId {
        match self {
            Content::SeekableVideo(video) => &video.entry.id,
            Content::Playlist(playlist) => &playlist.id,
        }
    }

    /// The name rows and viewers show.
    pub fn display_name(&self) -> &str {
        match self {
            Content::SeekableVideo(video) => &video.entry.label,
            Content::Playlist(playlist) => &playlist.display_name,
        }
    }

    /// The entries in order: the one entry of a video, or those of a playlist.
    pub fn entries(&self) -> &[Entry] {
        match self {
            Content::SeekableVideo(video) => std::slice::from_ref(&video.entry),
            Content::Playlist(playlist) => &playlist.entries,
        }
    }

    /// What resolving left out of the Content, such as a skipped sequence.
    pub fn warnings(&self) -> &[Warning] {
        match self {
            Content::SeekableVideo(_) => &[],
            Content::Playlist(playlist) => &playlist.warnings,
        }
    }

    /// The entry with this id.
    pub fn entry(&self, id: &ContentId) -> Option<&Entry> {
        self.entries().iter().find(|entry| entry.id == *id)
    }
}

/// Builds the Content of the playlist item `item` from what the resolver `resolver` returned. Playlists that
/// share a name are numbered ` (2)`, ` (3)` (skipping suffixes already taken), and the playlist and entry names give
/// the ids `{item}/{playlist}` and `{item}/{playlist}/{entry}`. Fails when the resolver returned no playlist or an
/// empty one; when two entries of a playlist share a name or two ids of the item collide, naming the entry; or
/// when two overlays of an entry share a lane label.
pub(crate) fn playlist_content(
    item: &ItemId,
    resolver: &str,
    resolved: ResolvedPlaylists,
) -> Result<PlaylistContent, UserError> {
    if resolved.playlists.iter().any(|p| p.entries.is_empty()) {
        return Err(UserError::new(
            ErrorCode::PlaylistInvalid,
            "Playlists require at least one entry.",
        )
        .at(*item));
    }
    let names = numbered(resolved.playlists.iter().map(|p| p.name.as_str()));
    let mut ids = HashSet::new();
    let mut entries = Vec::new();
    let mut spans = Vec::with_capacity(names.len());
    for (playlist, name) in resolved.playlists.into_iter().zip(names) {
        let id = ContentId::of_playlist(item, &name);
        if !ids.insert(id.clone()) {
            return Err(UserError::new(
                ErrorCode::PlaylistInvalid,
                format!("Two playlists have the same name: {name}"),
            )
            .at(*item));
        }
        spans.push(PlaylistSpan {
            id,
            name: name.clone(),
            entries: playlist.entries.len(),
        });
        for entry in playlist.entries {
            let id = ContentId::of_entry(item, &name, &entry.name);
            if !ids.insert(id.clone()) {
                return Err(UserError::new(
                    ErrorCode::PlaylistInvalid,
                    format!(
                        "Two entries of the playlist have the same name: {}",
                        entry.name
                    ),
                )
                .at(*item));
            }
            let entry = Entry {
                id,
                label: entry.name,
                video: entry.video,
                overlays: entry.overlays,
            };
            check_unique_lanes(item, &entry)?;
            entries.push(entry);
        }
    }
    Ok(PlaylistContent {
        id: ContentId::of_item(item),
        item_id: *item,
        display_name: resolver.to_owned(),
        entries,
        playlists: spans,
        warnings: resolved.warnings,
    })
}

/// `names` with the second and later of equal names numbered ` (2)`, ` (3)`, skipping a number when that name is
/// already one of `names`. Names compare as exact strings. A name's numbers are handed out in order from one counter
/// per name, so the work is linear however many playlists share a name.
fn numbered<'a>(names: impl IntoIterator<Item = &'a str>) -> Vec<String> {
    let names: Vec<&str> = names.into_iter().collect();
    let present: HashSet<&str> = names.iter().copied().collect();
    // The next number to try for each name seen; two names never produce the same numbered name, so only the
    // names given by the resolver can collide.
    let mut next_number: HashMap<&str, usize> = HashMap::new();
    names
        .iter()
        .map(|&name| {
            let Some(number) = next_number.get_mut(name) else {
                next_number.insert(name, 2);
                return name.to_owned();
            };
            loop {
                let candidate = format!("{name} ({number})");
                *number += 1;
                if !present.contains(candidate.as_str()) {
                    return candidate;
                }
            }
        })
        .collect()
}

/// Fails, naming the label in its reason, when two overlays of `entry` of the item `item` would be the same lane: they
/// share a label or a slot.
pub(crate) fn check_unique_lanes(item: &ItemId, entry: &Entry) -> Result<(), UserError> {
    let mut labels = HashSet::new();
    let mut slots = HashSet::new();
    for overlay in &entry.overlays {
        if !labels.insert(overlay.label.as_str()) || !slots.insert(overlay.slot.as_str()) {
            return Err(UserError::new(
                ErrorCode::OverlayInvalid,
                format!(
                    "Two overlays of {} have the same name: {}",
                    entry.label, overlay.label
                ),
            )
            .at(*item));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_content_or_entry_id_deserializes() {
        let item = ItemId::generate();
        for id in [
            ContentId::of_item(&item),
            ContentId::of_playlist(&item, "Runs"),
            ContentId::of_entry(&item, "Runs", "SEQ-01"),
        ] {
            let text = serde_json::to_value(&id).unwrap();
            assert_eq!(serde_json::from_value::<ContentId>(text).unwrap(), id);
        }
        for bad in ["", "SEQ-01", "not-an-item/SEQ-01", &format!("{item}/")] {
            assert!(
                serde_json::from_value::<ContentId>(bad.into()).is_err(),
                "{bad:?}"
            );
        }
    }

    fn playlist(name: &str, entries: &[&str]) -> handler_api::ResolvedPlaylist {
        handler_api::ResolvedPlaylist {
            name: name.into(),
            entries: entries
                .iter()
                .map(|entry| handler_api::ResolvedEntry {
                    name: (*entry).into(),
                    video: VideoRef::File(format!("/{entry}.mp4").into()),
                    overlays: Vec::new(),
                })
                .collect(),
        }
    }

    fn build(playlists: Vec<handler_api::ResolvedPlaylist>) -> Result<PlaylistContent, UserError> {
        let resolved = ResolvedPlaylists {
            playlists,
            warnings: Vec::new(),
        };
        playlist_content(&ItemId::generate(), "Runs", resolved)
    }

    #[test]
    fn shared_names_are_numbered_around_the_suffixes_already_there() {
        let names = numbered(["a", "a", "a (2)", "b", "a", "A"]);
        assert_eq!(names, ["a", "a (3)", "a (2)", "b", "a (4)", "A"]);

        // Linear in the number of names: this would take half a minute if every duplicate searched from 2.
        let many = numbered(vec!["run"; 20_000]);
        assert_eq!(many[19_999], "run (20000)");
        assert_eq!(many.iter().collect::<HashSet<_>>().len(), 20_000);
    }

    #[test]
    fn several_playlists_give_ids_by_playlist_name_and_refuse_what_has_no_clear_identity() {
        let content = build(vec![
            playlist("run", &["a", "b"]),
            playlist("run", &["a"]),
            playlist("x/y", &["z"]),
        ])
        .unwrap();
        let spans: Vec<_> = content
            .playlists
            .iter()
            .map(|span| (span.name.as_str(), span.entries))
            .collect();
        assert_eq!(spans, [("run", 2), ("run (2)", 1), ("x/y", 1)]);
        assert_eq!(content.display_name, "Runs");
        let item = content.item_id;
        let ids: Vec<_> = content
            .entries
            .iter()
            .map(|entry| entry.id.clone())
            .collect();
        assert_eq!(
            ids,
            [
                ContentId::of_entry(&item, "run", "a"),
                ContentId::of_entry(&item, "run", "b"),
                ContentId::of_entry(&item, "run (2)", "a"),
                ContentId::of_entry(&item, "x/y", "z"),
            ]
        );

        let reason = |playlists| build(playlists).unwrap_err().reason;
        assert_eq!(
            reason(vec![playlist("run", &["a"]), playlist("empty", &[])]),
            "Playlists require at least one entry."
        );
        assert!(reason(vec![playlist("run", &["a", "a"])]).ends_with(": a"));
        let mut lanes = playlist("run", &["a"]);
        let overlay = handler_api::OverlayRef {
            label: "GT".into(),
            slot: "gt".into(),
            path: "/o.txt".into(),
            handler: "MOT_FILE".into(),
            options: Default::default(),
        };
        lanes.entries[0].overlays = vec![overlay.clone(), overlay];
        assert!(reason(vec![lanes]).ends_with(": GT"));
        // "a/b" + "c" and "a" + "b/c" are the same id.
        assert!(reason(vec![playlist("a", &["b/c"]), playlist("a/b", &["c"])]).ends_with(": c"));
    }
}
