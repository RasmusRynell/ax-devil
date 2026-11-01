use serde::{Deserialize, Deserializer, Serialize, Serializer, de};
use std::fmt;
use uuid::Uuid;

/// Names one workspace item for as long as it exists. It travels as its text, and text that is not an item id does not
/// deserialize.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ItemId(Uuid);

impl ItemId {
    /// A new random id.
    pub fn generate() -> Self {
        Self(Uuid::new_v4())
    }

    /// The id written as `text`, or `None` when `text` is not an item id.
    pub fn parse(text: &str) -> Option<Self> {
        Uuid::parse_str(text).ok().map(Self)
    }
}

impl fmt::Display for ItemId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}

impl Serialize for ItemId {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_str(self)
    }
}

impl<'de> Deserialize<'de> for ItemId {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let text = String::deserialize(deserializer)?;
        Self::parse(&text).ok_or_else(|| de::Error::custom("that is not an item id"))
    }
}
