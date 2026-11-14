//! Naming and editing an item in place: its id, place, bookmarks and reviewed marks stay.

use crate::{Content, DerivedLane, ItemId, Source, WorkspaceItem};

use crate::store::{ContentGen, Resolution, ResolveJob, WorkspaceState};

impl WorkspaceState {
    /// Names `item` `label` (see [`crate::store::DocCommand::RenameItem`]), which the sender has already normalized with
    /// `crate::intake::with_label` in its own environment: the store knows none.
    pub(crate) fn rename(&mut self, item: ItemId, label: &str) {
        if let Some(existing) = self.workspace.items.iter_mut().find(|i| i.id == item) {
            existing.label = label.trim().to_owned();
        }
    }

    /// Replaces the item with `item`'s id by `item`, and returns the job that resolves it when its recipe changed and
    /// no `content` came with it (see [`crate::store::DocCommand::EditItem`]).
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
        item.recipe.derived = existing.recipe.derived.clone();
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

    /// Sets the derived lane at `slot` of `item` to `lane` (its slot becomes `slot`), or removes it for `None` (see
    /// [`crate::store::DocCommand::SetDerivedLane`]), and returns the job that resolves the item again. Changes
    /// nothing for an item that is not a video or playlist, or when `lane` reads itself or a derived lane after it.
    pub(crate) fn set_derived(
        &mut self,
        item: ItemId,
        slot: &str,
        lane: Option<DerivedLane>,
    ) -> Option<ResolveJob> {
        let existing = self.workspace.items.iter().find(|i| i.id == item)?;
        if !matches!(
            existing.recipe.source,
            Source::Video(_) | Source::Playlist(_)
        ) {
            return None;
        }
        let mut derived = existing.recipe.derived.clone();
        let at = derived.iter().position(|d| d.slot == slot);
        match (lane, at) {
            (Some(mut lane), at) => {
                lane.slot = slot.to_owned();
                let built_after = &derived[at.unwrap_or(derived.len())..];
                if lane.lanes.values().any(|input| {
                    input == slot || built_after.iter().any(|later| later.slot == *input)
                }) {
                    return None;
                }
                match at {
                    Some(at) => derived[at] = lane,
                    None => derived.push(lane),
                }
            }
            (None, Some(at)) => {
                derived.remove(at);
            }
            (None, None) => return None,
        }
        let existing = self.workspace.items.iter_mut().find(|i| i.id == item)?;
        if existing.recipe.derived == derived {
            return None;
        }
        existing.recipe.derived = derived;
        let existing = existing.clone();
        let job = self.start(&existing);
        self.forget_hidden(&item);
        Some(job)
    }
}
