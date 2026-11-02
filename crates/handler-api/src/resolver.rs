//! A playlist resolver: settings in, a list of videos with their overlay files out.

use foundation::{CancelToken, Interrupt, UserError, Warning};

use crate::{Form, OverlayRef, Values, VideoRef, invalid_field};

/// What the host needs to know about a resolver without running it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResolverSpec {
    /// The id workspaces and the CLI name the resolver by.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// One sentence on what the resolver does.
    pub description: String,
    /// The settings the resolver takes; the dialog's form and the command line are generated from it.
    pub form: Form,
}

/// One value a scan found.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Choice {
    /// What is stored in the settings.
    pub value: String,
    /// What the user reads.
    pub label: String,
    /// A second line about the value, such as its size; may be empty.
    pub detail: String,
}

/// Turns settings into a playlist.
///
/// A playlist's entry names are unique, and so are the overlay labels within an entry: they are the identity of
/// entries and lanes.
pub trait PlaylistResolver: Send + Sync {
    /// What the host needs to know about the resolver.
    fn spec(&self) -> &ResolverSpec;

    /// The values a [`FieldKind::Scan`](crate::FieldKind::Scan) field offers, found from the other settings. A
    /// `UserError` names the setting key or path the problem is about.
    fn scan(
        &self,
        field: &str,
        _settings: &Values,
        _cancel: &CancelToken,
    ) -> Result<Vec<Choice>, Interrupt<UserError>> {
        Err(Interrupt::Failed(invalid_field(
            "This setting has nothing to scan",
            field,
        )))
    }

    /// Reads the folders the settings name and returns the playlist. A `UserError` names the setting key or path
    /// the problem is about.
    fn resolve(
        &self,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<ResolvedPlaylist, Interrupt<UserError>>;
}

/// A resolved playlist: one video per entry.
#[derive(Clone, Debug, PartialEq)]
pub struct ResolvedPlaylist {
    /// What the user reads.
    pub display_name: String,
    /// The entries in playlist order.
    pub entries: Vec<ResolvedEntry>,
    /// Problems that left something out of the playlist without stopping it, such as a skipped sequence.
    pub warnings: Vec<Warning>,
}

/// One video and the overlays drawn on it.
#[derive(Clone, Debug, PartialEq)]
pub struct ResolvedEntry {
    /// What the user reads.
    pub name: String,
    /// The video.
    pub video: VideoRef,
    /// The overlays, each shown as its own lane.
    pub overlays: Vec<OverlayRef>,
}
