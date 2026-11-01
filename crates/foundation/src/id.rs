use std::fmt;
use uuid::Uuid;

/// Names one workspace item for as long as it exists.
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
