//! Where a plugin's view is shown and what its command applies to: the closed sets the manifest names and the
//! listing sends to the page.

use serde::{Deserialize, Serialize};
use std::fmt;
use ts_rs::TS;

/// Where a view is shown.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum Place {
    /// A button in the activity bar opens it in the sidebar.
    Sidebar,
    /// A layer over one lane's viewer.
    Video,
}

/// What a command applies to.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum On {
    /// The application as a whole.
    App,
    /// A Video Item.
    Item,
    /// A lane.
    Lane,
}

/// The word the manifest and the wire use, as shown to the user.
impl fmt::Display for Place {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Sidebar => "sidebar",
            Self::Video => "video",
        })
    }
}

impl fmt::Display for On {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::App => "app",
            Self::Item => "item",
            Self::Lane => "lane",
        })
    }
}
