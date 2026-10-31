use crate::item::WorkspaceItem;

/// The items a user works with, in order, and the name they gave the workspace.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Workspace {
    /// The user's name for the workspace; `None` is Untitled.
    pub name: Option<String>,
    /// Every item, resolved or not, in the order they were added.
    pub items: Vec<WorkspaceItem>,
}
