//! Naming and editing an item in place: its id, place, bookmarks and reviewed marks stay.

use workspace::{Content, ItemId, Source, WorkspaceItem};

use crate::{ContentGen, Resolution, ResolveJob, WorkspaceState};

impl WorkspaceState {
    /// Names `item` `label` (see [`crate::DocCommand::RenameItem`]), which the sender has already normalized with
    /// `workspace::intake::with_label` in its own environment: the store knows none.
    pub(crate) fn rename(&mut self, item: ItemId, label: &str) {
        if let Some(existing) = self.workspace.items.iter_mut().find(|i| i.id == item) {
            existing.label = label.trim().to_owned();
        }
    }

    /// Replaces the item with `item`'s id by `item`, and returns the job that resolves it when its recipe changed and
    /// no `content` came with it (see [`crate::DocCommand::EditItem`]).
    pub(crate) fn edit(
        &mut self,
        mut item: WorkspaceItem,
        content: Option<Box<Content>>,
    ) -> Option<ResolveJob> {
        let same_kind = |existing: &WorkspaceItem| {
            matches!(
                (&existing.recipe.source, &item.recipe.source),
                (Source::Video(_), Source::Video(_))
                    | (Source::Playlist(_), Source::Playlist(_))
                    | (Source::Live(_), Source::Live(_))
            )
        };
        let at = self
            .workspace
            .items
            .iter()
            .position(|existing| existing.id == item.id && same_kind(existing))?;
        let existing = &self.workspace.items[at];
        item.recipe.lane_matching = existing.recipe.lane_matching.clone();
        let mut job = None;
        if item.recipe != existing.recipe {
            match content {
                Some(content) => {
                    self.next_generation += 1;
                    self.reloads.remove(&item.id);
                    self.resolutions.insert(
                        item.id,
                        Resolution::Resolved {
                            content: *content,
                            generation: ContentGen(self.next_generation),
                        },
                    );
                }
                None => job = Some(self.start(&item)),
            }
            // A new recipe starts the item's entries and lanes over, as its resolver says; after `start`, which pins.
            self.forget_hidden(&item.id);
        }
        self.workspace.items[at] = item;
        job
    }
}
