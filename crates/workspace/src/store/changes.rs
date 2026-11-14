use crate::ContentId;
use std::collections::HashMap;

/// Pending source changes, using one revision clock for an item and its entries.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SourceChanges {
    /// All changes noticed, including resolver inputs that do not offer a viewer reload.
    pub revision: u32,
    /// The latest pending change belonging to the whole item.
    pub item: Option<u32>,
    /// The latest pending change for each affected active entry.
    pub entries: HashMap<ContentId, u32>,
}

impl SourceChanges {
    pub(super) fn record(&mut self, entries: Vec<ContentId>, whole_item: bool) {
        self.revision += 1;
        if whole_item {
            self.item = Some(self.revision);
        }
        for entry in entries {
            self.entries.insert(entry, self.revision);
        }
    }

    pub(super) fn covered(&mut self, seen: u32) {
        self.revision = self.revision.saturating_sub(seen);
        self.item = self
            .item
            .and_then(|revision| revision.checked_sub(seen))
            .filter(|r| *r > 0);
        self.entries.retain(|_, revision| {
            *revision = revision.saturating_sub(seen);
            *revision > 0
        });
    }
}
