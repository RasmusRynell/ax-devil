//! What the user chose to hide or show, over how a resolver started the entries and lanes.

use workspace::{Content, ContentId, Entry, ItemId};

use crate::{Resolution, WorkspaceState};

/// What the user hid.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum HiddenRef {
    /// An entry, by its content id.
    Entry(ContentId),
    /// A lane of an entry, by the entry's content id and the lane's label.
    Lane(ContentId, String),
}

impl HiddenRef {
    /// The entry the reference is about.
    pub(crate) fn content(&self) -> &ContentId {
        let (Self::Entry(id) | Self::Lane(id, _)) = self;
        id
    }

    /// Whether `content` still has what the reference names.
    fn is_in(&self, content: &Content) -> bool {
        match self {
            Self::Entry(id) => content.entry(id).is_some(),
            Self::Lane(id, label) => content
                .entry(id)
                .is_some_and(|entry| entry.overlays.iter().any(|overlay| overlay.label == *label)),
        }
    }
}

impl WorkspaceState {
    /// Whether this entry or lane is hidden: the user's choice, else `starts_hidden`, how its resolver started it.
    pub fn is_hidden(&self, target: &HiddenRef, starts_hidden: bool) -> bool {
        self.hidden.get(target).copied().unwrap_or(starts_hidden)
    }

    /// Whether `entry` is hidden, by the user's choice or else as its resolver started it.
    pub fn entry_hidden(&self, entry: &Entry) -> bool {
        self.is_hidden(&HiddenRef::Entry(entry.id.clone()), entry.starts_hidden)
    }

    /// Whether the user may hide `target`. A video's own entry, a live stream and its lane stay shown; where the item
    /// is not resolved yet, the choice is kept for the Content it resolves to.
    pub(crate) fn may_hide(&self, target: &HiddenRef) -> bool {
        let Some(item) = self
            .workspace
            .items
            .iter()
            .find(|item| target.content().names_item(&item.id))
        else {
            return true;
        };
        match (self.resolutions.get(&item.id), target) {
            (Some(Resolution::Resolved { content, .. }), _) => match content {
                Content::SeekableVideo(_) => matches!(target, HiddenRef::Lane(..)),
                Content::LiveVideo(_) => false,
                Content::Playlist(_) => true,
            },
            _ => true,
        }
    }

    /// Pins the visibility every entry and lane of `item`'s resolved playlist has now as the user's choice, so that
    /// resolving again changes only what is new: an entry or lane that survives keeps its visibility, whatever the
    /// resolver now starts it as.
    pub(crate) fn pin_hidden(&mut self, item: &ItemId) {
        let Some(Resolution::Resolved {
            content: Content::Playlist(playlist),
            ..
        }) = self.resolutions.get(item)
        else {
            return;
        };
        let mut pinned = Vec::new();
        for entry in &playlist.entries {
            pinned.push((HiddenRef::Entry(entry.id.clone()), self.entry_hidden(entry)));
            for overlay in &entry.overlays {
                let lane = HiddenRef::Lane(entry.id.clone(), overlay.label.clone());
                let starts_hidden = entry.lanes_starting_hidden.contains(&overlay.label);
                pinned.push((lane.clone(), self.is_hidden(&lane, starts_hidden)));
            }
        }
        self.hidden.extend(pinned);
    }

    /// Forgets what the user chose for entries and lanes `content`, the new Content of `item`, no longer has, so
    /// they come back as their resolver starts them.
    pub(crate) fn forget_hidden_outside(&mut self, item: &ItemId, content: &Content) {
        self.hidden
            .retain(|target, _| !target.content().names_item(item) || target.is_in(content));
    }
}
