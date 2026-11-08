//! What a seat may use of the workspace's saved streams, and the environment its session reads references in.

use foundation::ItemId;
use workspace::{Environment, LiveRecipe, Source, Workspace, intake::with_label};

/// The logins saved with the workspace's streams, for the seat that may copy them.
#[derive(Clone, Copy)]
pub(super) struct Saved<'a> {
    workspace: &'a Workspace,
    /// Whether the seat is the session's owner; any other copies no login.
    owner: bool,
    /// Whether the seat is not the owner of a session whose references read this machine's environment: a stream it
    /// adds may not hold one.
    pub(super) guest: bool,
    /// Whether the session's references read this machine's environment.
    expands: bool,
}

impl<'a> Saved<'a> {
    pub(super) fn new(workspace: &'a Workspace, owner: bool, expands: bool) -> Self {
        Self {
            workspace,
            owner,
            guest: expands && !owner,
            expands,
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

    /// `label` for item `item` as it is stored: empty when it is empty or the item's current default name.
    pub(super) fn normalized_label(self, item: ItemId, label: &str) -> String {
        match self
            .workspace
            .items
            .iter()
            .find(|candidate| candidate.id == item)
        {
            Some(found) => with_label(found.clone(), Some(label), &self.environment()).label,
            None => label.trim().to_owned(),
        }
    }

    /// The recipe of the live stream `item`, when the seat may copy its login.
    pub(super) fn live(self, item: ItemId) -> Option<&'a LiveRecipe> {
        self.owner.then(|| self.recipe(item)).flatten()
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
