//! What a seat may use of the workspace's saved streams, and the environment its session reads references in.

use super::project::item_label;
use api_types::Audience;
use foundation::ItemId;
use workspace::{Environment, LiveRecipe, Source, Workspace, WorkspaceItem, intake::with_label_as};

/// The logins saved with the workspace's streams, for an Owner seat that may copy them.
#[derive(Clone, Copy)]
pub(super) struct Saved<'a> {
    workspace: &'a Workspace,
    /// Whether the session's references read this machine's environment.
    expands: bool,
    /// What the seat is shown of hosts.
    audience: Audience,
}

impl<'a> Saved<'a> {
    pub(super) fn new(workspace: &'a Workspace, expands: bool, audience: Audience) -> Self {
        Self {
            workspace,
            expands,
            audience,
        }
    }

    /// The environment the session's references read: the process's where it may expand them, else none.
    pub(super) fn environment(self) -> Environment {
        if self.expands {
            Environment::of_process()
        } else {
            Environment::literal()
        }
    }

    /// `item` named `label` by this seat: the label is empty when it is empty or the item's current default name as
    /// this seat's own view shows it (`item_label`, the rule projection follows), so a seat that is not shown a host
    /// learns nothing of it by what clears.
    pub(super) fn labelled(self, item: WorkspaceItem, label: Option<&str>) -> WorkspaceItem {
        let unnamed = WorkspaceItem {
            label: String::new(),
            ..item.clone()
        };
        let default = item_label(&unnamed, self.audience, &self.environment());
        with_label_as(item, label, &default)
    }

    /// `label` for item `item` as it is stored, by [`labelled`](Self::labelled).
    pub(super) fn normalized_label(self, item: ItemId, label: &str) -> String {
        match self
            .workspace
            .items
            .iter()
            .find(|candidate| candidate.id == item)
        {
            Some(found) => self.labelled(found.clone(), Some(label.trim())).label,
            None => label.trim().to_owned(),
        }
    }

    /// The recipe of live stream `item`.
    pub(super) fn recipe(self, item: ItemId) -> Option<&'a LiveRecipe> {
        self.workspace
            .items
            .iter()
            .find(|candidate| candidate.id == item)
            .and_then(|found| match &found.recipe.source {
                Source::Live(recipe) => Some(recipe),
                _ => None,
            })
    }
}
