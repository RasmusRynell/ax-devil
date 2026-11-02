//! Built-in playlist resolvers: settings turned into a list of videos with their overlay files.
//!
//! Resolvers read folders only; opening the media and decoding the overlays belong to their own crates, and item
//! and content ids are assigned by the host.

mod folder_pair;
mod mot_challenge;
mod schema;

use std::{collections::BTreeSet, path::PathBuf};

use foundation::{CancelToken, Cancelled, FrameRate, Interrupt, UserError};

pub use schema::{Choice, Cli, Field, FieldKind, ResolverSchema};

/// What the host needs to know about a resolver without running it.
#[derive(Debug, PartialEq, Eq)]
pub struct ResolverSpec {
    /// The id workspaces and the CLI name the resolver by.
    pub id: &'static str,
    /// What the user reads.
    pub display_name: &'static str,
    /// One sentence on what the resolver does.
    pub description: &'static str,
}

/// A resolver's settings, as the user gave them; [`PlaylistResolver::schema`] declares the keys.
pub type Settings = serde_json::Map<String, serde_json::Value>;

/// Turns settings into a playlist.
///
/// A playlist's entry names are unique, and so are the overlay labels within an entry: they are the identity of
/// entries and lanes.
pub trait PlaylistResolver: Send + Sync {
    /// What the host needs to know about the resolver.
    fn spec(&self) -> &'static ResolverSpec;

    /// The settings the resolver takes; the dialog's form and the command line are generated from it.
    fn schema(&self) -> &'static ResolverSchema;

    /// The values a [`FieldKind::Scan`] field offers, found from the other settings. A `UserError` names the setting
    /// key or path the problem is about.
    fn scan(
        &self,
        field: &str,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<Vec<Choice>, Interrupt<UserError>>;

    /// Reads the folders the settings name and returns the playlist. A `UserError` names the setting key or path
    /// the problem is about.
    fn resolve(
        &self,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<ResolvedPlaylist, Interrupt<UserError>>;

    /// The files and folders whose changes can change the playlist.
    fn watch_paths(
        &self,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<WatchPaths, Cancelled>;
}

/// A resolved playlist: one video per entry.
#[derive(Clone, Debug, PartialEq)]
pub struct ResolvedPlaylist {
    /// What the user reads.
    pub display_name: String,
    /// The entries in playlist order.
    pub entries: Vec<ResolvedEntry>,
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

/// Where a video is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum VideoRef {
    /// A video file.
    File(PathBuf),
    /// A numbered run of image files.
    ImageSequence(ImageSequence),
}

/// A numbered run of image files that plays as a video.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ImageSequence {
    /// The file name pattern, such as `<seq>/img1/%06d.jpg`.
    pub pattern: PathBuf,
    /// The number in the first file's name.
    pub start_number: u32,
    /// Frames per second.
    pub frame_rate: FrameRate,
    /// Image width in pixels, as the dataset declares it; only compared with the decoded size.
    pub width: u32,
    /// Image height in pixels, as the dataset declares it; only compared with the decoded size.
    pub height: u32,
    /// How many images the run holds.
    pub frame_count: u64,
}

/// An overlay file and how to read it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OverlayRef {
    /// What the user reads on the lane.
    pub label: String,
    /// The overlay file.
    pub path: PathBuf,
    /// The decoder handler id that reads the file.
    pub handler: String,
    /// Options handed to the decoder.
    pub options: Settings,
}

/// Where a change can change a playlist.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct WatchPaths {
    /// Files whose content matters.
    pub files: BTreeSet<PathBuf>,
    /// Folders whose entries matter.
    pub folders: BTreeSet<PathBuf>,
}

static RESOLVERS: [&dyn PlaylistResolver; 2] = [
    &folder_pair::FolderPairResolver,
    &mot_challenge::MotChallengeResolver,
];

/// Every built-in resolver.
pub fn resolvers() -> &'static [&'static dyn PlaylistResolver] {
    &RESOLVERS
}

/// The resolver with this id.
pub fn resolver(id: &str) -> Option<&'static dyn PlaylistResolver> {
    resolvers()
        .iter()
        .copied()
        .find(|resolver| resolver.spec().id == id)
}
