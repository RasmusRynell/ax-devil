use foundation::{ErrorCode, ItemId, UserError, Warning};
use handler_api::ResolvedPlaylist;
use serde::{Deserialize, Deserializer, Serialize, Serializer, de};
use std::{collections::HashSet, fmt};

pub use handler_api::{ImageSequence, OverlayRef, VideoRef};

/// Names one Content, or one entry of a playlist, for as long as its item exists. Derived from the item id (and the
/// entry label), so it names the same thing after the item resolves again. It travels as its text, and text that is
/// not a Content or entry id does not deserialize.
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

impl Serialize for ContentId {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.0)
    }
}

/// Only `<item>` or `<item>/<label>` deserializes, so nothing a client sends can name a Content or entry no item
/// could have.
impl<'de> Deserialize<'de> for ContentId {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let text = String::deserialize(deserializer)?;
        let (item, label) = match text.split_once('/') {
            Some((item, label)) => (item, Some(label)),
            None => (text.as_str(), None),
        };
        let item =
            ItemId::parse(item).ok_or_else(|| de::Error::custom("that is not a content id"))?;
        Ok(match label {
            Some(label) if !label.is_empty() => Self::of_entry(&item, label),
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

/// Gives the entries of `resolved` their ids, `{item}/{name}`. Fails, naming the label in its reason, when two
/// entries share a name or two overlays of an entry share a lane label.
pub(crate) fn playlist_content(
    item: &ItemId,
    resolved: ResolvedPlaylist,
) -> Result<PlaylistContent, UserError> {
    let mut names = HashSet::new();
    let mut entries = Vec::with_capacity(resolved.entries.len());
    for entry in resolved.entries {
        if !names.insert(entry.name.clone()) {
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
            id: ContentId::of_entry(item, &entry.name),
            label: entry.name,
            video: entry.video,
            overlays: entry.overlays,
        };
        check_unique_lanes(item, &entry)?;
        entries.push(entry);
    }
    Ok(PlaylistContent {
        id: ContentId::of_item(item),
        item_id: *item,
        display_name: resolved.display_name,
        entries,
        warnings: resolved.warnings,
    })
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
            ContentId::of_entry(&item, "SEQ-01"),
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
}
