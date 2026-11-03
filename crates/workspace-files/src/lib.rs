//! How a workspace lives on disk: the file format, atomic saves, recovery copies with their locks, and the recent
//! workspaces list. See the README for the rules.

mod disk;
mod document;
mod recents;
mod recovery;

pub use disk::{load, save};
pub use recents::{Recent, Recents};
pub use recovery::{Claim, RecoveryCopy, RecoveryFolder, file_stem};

/// The ending of every workspace file name.
pub const SUFFIX: &str = ".ax-devil.workspace";
