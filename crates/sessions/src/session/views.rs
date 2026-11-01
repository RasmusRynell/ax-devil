//! View state: what a window shows of the Doc, and the rules that keep it inside the Doc.
//!
//! The presenter's [`View`] is what the owner published; an exploring seat has its own. Both are cut down to what the
//! Doc has open by [`restrict`] after every Doc change.

use super::state::Doc;
use playback::ClockIntent;
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, time::Duration};
use tokio::time::Instant;
use ts_rs::TS;
use workspace::ItemId;

/// What the sidebar shows; part of the view a session shares.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "lowercase")]
pub enum SidebarView {
    /// The whole Workspace.
    #[default]
    Workspace,
    /// Live streams.
    Live,
    /// Offline videos.
    Offline,
    /// Bookmarks.
    Bookmarks,
    /// Plugins.
    Plugins,
    /// The session this client is in, and the sessions it can watch.
    Sessions,
}

/// What the sidebar shows.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Sidebar {
    /// Which view.
    pub view: SidebarView,
    /// Whether the sidebar is open.
    pub open: bool,
}

impl Default for Sidebar {
    fn default() -> Self {
        Self {
            view: SidebarView::default(),
            open: true,
        }
    }
}

/// What one open viewer shows.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ViewerView {
    /// What this window's clock for the viewer is doing; absent until it has one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub clock: Option<ClockIntent>,
}

/// What a window shows of the Doc. Items the Doc does not have open are dropped by the backend.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct View {
    /// The sidebar.
    pub sidebar: Sidebar,
    /// The item of the viewer in front; absent shows the welcome page.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional, as = "Option<String>")]
    pub active: Option<ItemId>,
    /// What each open viewer shows, by item id.
    #[ts(as = "BTreeMap<String, ViewerView>")]
    pub viewers: BTreeMap<ItemId, ViewerView>,
}

/// `view` cut down to what `doc` has open: no viewer, and no front viewer, for an item that is not open.
pub fn restrict(view: &View, doc: &Doc) -> View {
    // Destructured without `..`, so a new field does not compile until its restriction is decided.
    let View {
        sidebar,
        active,
        viewers,
    } = view;
    let Doc { workspace } = doc;
    let open = &workspace.open;
    View {
        // Nothing about the sidebar depends on the Doc.
        sidebar: *sidebar,
        active: active.filter(|item| open.contains(item)),
        viewers: viewers
            .iter()
            .filter(|(item, _)| open.contains(item))
            .map(|(item, viewer)| {
                // Destructured too: a new field of a viewer's view must decide here whether the Doc restricts it.
                let ViewerView { clock } = viewer;
                (*item, ViewerView { clock: *clock })
            })
            .collect(),
    }
}

/// The most View changes a seat has applied per second.
const VIEWS_PER_SECOND: u32 = 60;

/// The View lane of one seat: it keeps the latest view offered and releases it at most [`VIEWS_PER_SECOND`] times a
/// second. A view offered while another waits overwrites it, and an overwritten sequence number counts as done
/// because the view that replaced it carries a higher one. The lane never refuses and never closes the socket. A
/// command sent after a waiting view flushes it first, so a command never overtakes a view sent before it.
#[derive(Debug, Default)]
pub struct ViewLane {
    waiting: Option<(u64, View)>,
    last_taken: Option<Instant>,
}

impl ViewLane {
    /// Offers `view` with the client's `seq`. A `seq` not above the waiting one is itself overwritten.
    pub fn offer(&mut self, seq: u64, view: View) {
        if self
            .waiting
            .as_ref()
            .is_none_or(|(waiting, _)| seq > *waiting)
        {
            self.waiting = Some((seq, view));
        }
    }

    /// How long until the waiting view may be taken; `None` when nothing waits, zero when it may be taken now.
    pub fn wait(&self, now: Instant) -> Option<Duration> {
        self.waiting.as_ref()?;
        Some(self.last_taken.map_or(Duration::ZERO, |at| {
            (at + Duration::from_secs(1) / VIEWS_PER_SECOND).saturating_duration_since(now)
        }))
    }

    /// The waiting view if the rate allows applying one at `now`.
    pub fn take(&mut self, now: Instant) -> Option<(u64, View)> {
        if !self.wait(now)?.is_zero() {
            return None;
        }
        self.last_taken = Some(now);
        self.waiting.take()
    }

    /// The waiting view whatever the rate, for a caller that must apply it before something sent after it. The
    /// next [`take`](Self::take) still waits a full period after it.
    pub fn flush(&mut self, now: Instant) -> Option<(u64, View)> {
        self.last_taken = Some(now);
        self.waiting.take()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_view_reads_as_the_wire_writes_it_and_an_id_that_names_no_item_is_refused() {
        let (front, behind) = (ItemId::generate(), ItemId::generate());
        let message = json!({
            "sidebar": { "view": "sessions", "open": false },
            "active": front.to_string(),
            "viewers": {
                front.to_string(): {},
                behind.to_string(): {
                    "clock": { "frame": 7, "playing": true, "speedCenti": 150, "atUs": -3 },
                },
            },
        });
        let view: View = serde_json::from_value(message.clone()).unwrap();
        assert_eq!(view.sidebar.view, SidebarView::Sessions);
        assert_eq!(view.active, Some(front));
        assert_eq!(view.viewers[&front], ViewerView::default());
        assert_eq!(view.viewers[&behind].clock.unwrap().speed_centi, 150);
        assert_eq!(serde_json::to_value(&view).unwrap(), message);

        let mut bad = message;
        bad["active"] = json!("not an item");
        assert!(serde_json::from_value::<View>(bad).is_err());
    }
}
