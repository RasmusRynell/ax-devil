use foundation::{HostPath, ItemId};
use serde::{Deserialize, Deserializer, Serialize, Serializer, de};
use std::fmt;

/// Names one Content for as long as its item exists. Derived from the item id, so it names the same Content after
/// the item resolves again. It travels as its text, and text that is not a Content id does not deserialize.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct ContentId(String);

impl ContentId {
    /// The id of the Content a single-Content item resolves to.
    pub fn of_item(item: &ItemId) -> Self {
        Self(item.to_string())
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

/// Only the id of an item's Content deserializes, so nothing a client sends can name a Content no item could have.
impl<'de> Deserialize<'de> for ContentId {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let text = String::deserialize(deserializer)?;
        ItemId::parse(&text)
            .map(|item| Self::of_item(&item))
            .ok_or_else(|| de::Error::custom("that is not a content id"))
    }
}

impl fmt::Display for ContentId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A video read from a file.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FileVideoSource {
    /// The video file.
    pub path: HostPath,
}

/// A recorded video a viewer can seek in.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SeekableVideoContent {
    /// Names this Content.
    pub id: ContentId,
    /// The item it was resolved from.
    pub item_id: ItemId,
    /// The name rows and viewers show.
    pub display_name: String,
    /// Where the frames come from.
    pub source: FileVideoSource,
}

/// What a viewer opens, by kind. Viewers register by kind; overlays and live sources join this set later.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Content {
    /// A recorded video.
    SeekableVideo(SeekableVideoContent),
}

impl Content {
    /// Names this Content.
    pub fn id(&self) -> &ContentId {
        match self {
            Content::SeekableVideo(video) => &video.id,
        }
    }
}
