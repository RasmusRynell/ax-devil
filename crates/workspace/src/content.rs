use crate::item::WorkspaceItem;
use crate::live::LiveVideoContent;
use foundation::{ErrorCode, ItemId, UserError, Warning};
use handler_api::ResolvedPlaylists;
use serde::{Deserialize, Deserializer, Serialize, Serializer, de};
use std::{
    collections::{HashMap, HashSet},
    fmt,
};

pub use handler_api::{ImageSequence, OverlayRef, Settings, VideoRef};

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

    /// This id with the item it names replaced by `item`; the playlist and entry names stay.
    fn of_other_item(&self, item: &ItemId) -> Self {
        let rest = self.0.find('/').map_or("", |slash| &self.0[slash..]);
        Self(format!("{item}{rest}"))
    }

    /// Whether this id names the item `item` or something inside it: ids are the item's id, then `/` and the names
    /// inside it.
    pub fn names_item(&self, item: &ItemId) -> bool {
        let own = item.to_string();
        self.0
            .strip_prefix(&own)
            .is_some_and(|rest| rest.is_empty() || rest.starts_with('/'))
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
    /// Whether the entry is hidden until the user shows it.
    pub starts_hidden: bool,
    /// The labels of the lanes that are hidden until the user shows them.
    pub lanes_starting_hidden: Vec<String>,
    /// Facts the resolver states about the entry; the lanes' are on their overlays.
    pub metadata: Settings,
}

impl Entry {
    /// The labels of the lanes that start hidden, as a set to test a long entry's lanes against in constant time.
    pub fn lanes_hidden_at_first(&self) -> HashSet<&str> {
        self.lanes_starting_hidden
            .iter()
            .map(String::as_str)
            .collect()
    }

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
    /// How many matches the resolver found, when it says.
    pub matches: Option<u64>,
    /// How many sequence groups the resolver found, when it says.
    pub sequence_groups: Option<u64>,
    /// Facts the resolver states about the playlist.
    pub metadata: Settings,
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

impl PlaylistContent {
    /// This Content as the item `item` resolves to: every id is `item`'s, with the same playlist and entry names. A
    /// playlist resolved before the item it is for exists takes the id of the item it replaces this way.
    #[must_use]
    pub fn rebound(mut self, item: &ItemId) -> Self {
        self.id = self.id.of_other_item(item);
        self.item_id = *item;
        for entry in &mut self.entries {
            entry.id = entry.id.of_other_item(item);
        }
        for span in &mut self.playlists {
            span.id = span.id.of_other_item(item);
        }
        self
    }
}

/// What a viewer opens, by kind. Viewers register by kind.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Content {
    /// A recorded video.
    SeekableVideo(SeekableVideoContent),
    /// A playlist of recorded videos.
    Playlist(PlaylistContent),
    /// A camera's live stream (boxed: it is much larger than the rest). It has no entries: a viewer opens it from the camera and overlay it names.
    LiveVideo(Box<LiveVideoContent>),
}

impl Content {
    /// Names this Content.
    pub fn id(&self) -> &ContentId {
        match self {
            Content::SeekableVideo(video) => &video.entry.id,
            Content::Playlist(playlist) => &playlist.id,
            Content::LiveVideo(live) => &live.id,
        }
    }

    /// The name rows and viewers show.
    pub fn display_name(&self) -> &str {
        match self {
            Content::SeekableVideo(video) => &video.entry.label,
            Content::Playlist(playlist) => &playlist.display_name,
            Content::LiveVideo(live) => &live.label,
        }
    }

    /// The entries in order: the one entry of a video, or those of a playlist; none for a live video.
    pub fn entries(&self) -> &[Entry] {
        match self {
            Content::SeekableVideo(video) => std::slice::from_ref(&video.entry),
            Content::Playlist(playlist) => &playlist.entries,
            Content::LiveVideo(_) => &[],
        }
    }

    /// The lane slots of every lane a viewer of this Content shows, including a live video's one overlay.
    pub fn lane_slots(&self) -> Vec<&str> {
        match self {
            Content::LiveVideo(live) => vec![live.slot.as_str()],
            _ => self.entries().iter().flat_map(Entry::lane_slots).collect(),
        }
    }

    /// What resolving left out of the Content, such as a skipped sequence.
    pub fn warnings(&self) -> &[Warning] {
        match self {
            Content::SeekableVideo(_) | Content::LiveVideo(_) => &[],
            Content::Playlist(playlist) => &playlist.warnings,
        }
    }

    /// The entry with this id.
    pub fn entry(&self, id: &ContentId) -> Option<&Entry> {
        self.entries().iter().find(|entry| entry.id == *id)
    }
}

/// Builds the Content of the playlist item `item` from what the resolver `resolver` returned. Playlists that
/// share a name are numbered ` (2)`, ` (3)` (skipping suffixes already taken), and so are entries of a playlist
/// that share a name; the numbered names give the ids `{item}/{playlist}` and `{item}/{playlist}/{entry}`. Fails when
/// the resolver returned no playlist or an empty one, when two ids of the item collide (a `/` in a name can do
/// that), naming the entry, or when two overlays of an entry share a lane label.
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
            matches: playlist.matches,
            sequence_groups: playlist.sequence_groups,
            metadata: playlist.metadata,
        });
        let entry_names = numbered(playlist.entries.iter().map(|entry| entry.name.as_str()));
        for (entry, entry_name) in playlist.entries.into_iter().zip(entry_names) {
            let id = ContentId::of_entry(item, &name, &entry_name);
            if !ids.insert(id.clone()) {
                return Err(UserError::new(
                    ErrorCode::PlaylistInvalid,
                    format!("Two entries of the item have the same id: {entry_name}"),
                )
                .at(*item));
            }
            let entry = Entry {
                id,
                label: entry_name,
                video: entry.video,
                overlays: entry.overlays,
                starts_hidden: entry.starts_hidden,
                lanes_starting_hidden: entry.lanes_starting_hidden,
                metadata: entry.metadata,
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
mod tests;
