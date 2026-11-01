//! What a window shows of the Doc, as the wire carries it: the types of `View` and `Seat` messages and of the View
//! lane. They mirror `sessions::session`; `mirror.rs` converts both ways.

use crate::SidebarView;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use ts_rs::TS;

/// What watchers may do while sharing is on.
#[derive(Serialize, Deserialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum Access {
    /// Follow the presenter.
    Watch,
    /// Follow, or explore a view of their own.
    Explore,
    /// Explore, and change the Doc's content.
    Edit,
}

/// What a presenter says the Session's clock is doing, for one viewer. Times are microseconds.
#[derive(Serialize, Deserialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ClockIntent {
    /// The frame the presenter asked for.
    #[ts(type = "number")]
    pub frame: u64,
    /// Whether the clock runs.
    pub playing: bool,
    /// Speed in hundredths: 100 is real time.
    pub speed_centi: u32,
    /// When the presenter stamped the intent, in server time.
    #[ts(type = "number")]
    pub at_us: i64,
}

/// What the sidebar shows.
#[derive(Serialize, Deserialize, TS, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Sidebar {
    /// Which view.
    pub view: SidebarView,
    /// Whether the sidebar is open.
    pub open: bool,
}

/// What one open viewer shows.
#[derive(Serialize, Deserialize, TS, Clone, Copy, Debug, Default, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ViewerView {
    /// What this window's clock for the viewer is doing; absent until it has one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub clock: Option<ClockIntent>,
}

/// What a window shows of the Doc. Items the Doc does not have open are dropped by the backend.
#[derive(Serialize, Deserialize, TS, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct View {
    /// The sidebar.
    pub sidebar: Sidebar,
    /// The item of the viewer in front; absent shows the welcome page.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub active: Option<String>,
    /// What each open viewer shows, by item id.
    pub viewers: BTreeMap<String, ViewerView>,
}
