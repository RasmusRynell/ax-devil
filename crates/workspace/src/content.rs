use crate::item::ItemId;
use foundation::UserError;
use playlist_resolvers::ResolvedPlaylist;
use std::{collections::HashSet, fmt};

pub use playlist_resolvers::{ImageSequence, OverlayRef, VideoRef};

/// Names one Content, or one entry of a playlist, for as long as its item exists. Derived from the item id (and the
/// entry label), so it names the same thing after the item resolves again.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct ContentId(String);

impl ContentId {
    /// The id of the Content a single-Content item resolves to.
    pub fn of_item(item: &ItemId) -> Self {
        Self(item.to_string())
    }

    /// The id of the entry `label` of the playlist item `item`: `{item}/{label}`.
    pub fn of_entry(item: &ItemId, label: &str) -> Self {
        Self(format!("{item}/{label}"))
    }

    /// The id as text, the form that crosses the wire.
    pub fn as_str(&self) -> &str {
        &self.0
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
    /// The overlays, each shown as its own lane; labels are unique within the entry.
    pub overlays: Vec<OverlayRef>,
}

impl Entry {
    /// The lane names: the overlay labels, or the entry's own label for the one plain lane of an entry without
    /// overlays.
    pub fn lane_labels(&self) -> Vec<&str> {
        if self.overlays.is_empty() {
            vec![self.label.as_str()]
        } else {
            self.overlays
                .iter()
                .map(|overlay| overlay.label.as_str())
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

/// Several videos in order, each opened as an entry.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PlaylistContent {
    /// Names this Content.
    pub id: ContentId,
    /// The item it was resolved from.
    pub item_id: ItemId,
    /// The name rows and viewers show.
    pub display_name: String,
    /// The entries in playlist order.
    pub entries: Vec<Entry>,
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

    /// The entry with this id.
    pub fn entry(&self, id: &str) -> Option<&Entry> {
        self.entries().iter().find(|entry| entry.id.as_str() == id)
    }
}

/// Gives the entries of `resolved` their ids, `{item}/{name}`. Fails, naming the label, when two entries share a name
/// or two overlays of an entry share a lane label.
pub(crate) fn playlist_content(
    item: &ItemId,
    resolved: ResolvedPlaylist,
) -> Result<PlaylistContent, UserError> {
    let mut names = HashSet::new();
    let mut entries = Vec::with_capacity(resolved.entries.len());
    for entry in resolved.entries {
        if !names.insert(entry.name.clone()) {
            return Err(UserError::new(
                "Two entries of the playlist have the same name",
                entry.name,
            ));
        }
        let entry = Entry {
            id: ContentId::of_entry(item, &entry.name),
            label: entry.name,
            video: entry.video,
            overlays: entry.overlays,
        };
        check_unique_lanes(&entry)?;
        entries.push(entry);
    }
    Ok(PlaylistContent {
        id: ContentId::of_item(item),
        item_id: *item,
        display_name: resolved.display_name,
        entries,
    })
}

/// Fails, naming the label, when two overlays of `entry` would be the same lane.
pub(crate) fn check_unique_lanes(entry: &Entry) -> Result<(), UserError> {
    let mut labels = HashSet::new();
    for overlay in &entry.overlays {
        if !labels.insert(overlay.label.as_str()) {
            return Err(UserError::new(
                format!("Two overlays of {} have the same name", entry.label),
                overlay.label.clone(),
            ));
        }
    }
    Ok(())
}
