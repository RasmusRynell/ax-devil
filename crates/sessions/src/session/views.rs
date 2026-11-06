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
use workspace::{Content, ContentId, Entry, ItemId};
use workspace_store::{HiddenRef, Resolution, WorkspaceState};

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
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ViewerView {
    /// What this window's clock for the viewer is doing; absent until it has one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub clock: Option<ClockIntent>,
    /// The entry of the item's Content the viewer shows. The backend keeps it naming an entry of the Content as it is
    /// resolved now; absent while the item is not resolved.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional, as = "Option<String>")]
    pub entry: Option<ContentId>,
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

/// `view` cut down to what `doc` has open: no viewer, and no front viewer, for an item that is not open. Each viewer's
/// entry is made one of its item's Content, as [`shown_entry`] picks it: this is the one place that decides which
/// entry a viewer shows.
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
                let ViewerView { clock, entry } = viewer;
                // While the item resolves again the entry asked for is kept: whether it is gone is for the new Content
                // to say, so a reload does not send the viewer back to the first entry.
                let entry = match workspace.resolutions.get(item) {
                    Some(Resolution::Pending { .. }) => entry.clone(),
                    _ => shown_entry(workspace, item, entry.as_ref()).map(|shown| shown.id.clone()),
                };
                (
                    *item,
                    ViewerView {
                        clock: *clock,
                        entry,
                    },
                )
            })
            .collect(),
    }
}

/// The entry a viewer of `item` shows when it asks for `requested`: that entry while the item's Content has it, else
/// its first entry the user has not hidden, else its first. `None` while the item is not resolved.
pub(super) fn shown_entry<'a>(
    workspace: &'a WorkspaceState,
    item: &ItemId,
    requested: Option<&ContentId>,
) -> Option<&'a Entry> {
    let content: &Content = match workspace.resolutions.get(item)? {
        Resolution::Resolved { content, .. } => content,
        Resolution::Pending { .. } | Resolution::Failed(_) => return None,
    };
    let entries = content.entries();
    requested
        .and_then(|id| content.entry(id))
        .or_else(|| {
            entries
                .iter()
                .find(|entry| !workspace.is_hidden(&HiddenRef::Entry(entry.id.clone())))
        })
        .or_else(|| entries.first())
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

    fn entry(item: &ItemId, label: &str) -> Entry {
        Entry {
            id: ContentId::of_entry(item, "P", label),
            label: label.into(),
            video: workspace::VideoRef::File(format!("{label}.mp4").into()),
            overlays: Vec::new(),
        }
    }

    #[test]
    fn a_viewer_shows_the_entry_it_asked_for_while_it_exists_else_the_first_one_not_hidden() {
        let playlist = workspace::intake::video(
            std::path::Path::new("p.mp4"),
            &[],
            &handlers::Handlers::built_in(),
        )
        .unwrap();
        let item = playlist.id;
        let (state, jobs) = workspace_store::apply(
            &WorkspaceState::default(),
            workspace_store::DocCommand::AddItems {
                items: vec![playlist],
                open: true,
            },
        );
        let viewer = |entry: Option<ContentId>| View {
            viewers: [(item, ViewerView { clock: None, entry })].into(),
            ..View::default()
        };
        let shown = |workspace: &WorkspaceState, asked: Option<ContentId>| {
            let doc = Doc {
                workspace: workspace.clone(),
            };
            restrict(&viewer(asked), &doc).viewers[&item].entry.clone()
        };
        let (a, b) = (
            ContentId::of_entry(&item, "P", "A"),
            ContentId::of_entry(&item, "P", "B"),
        );
        assert_eq!(
            shown(&state, Some(a.clone())),
            Some(a.clone()),
            "the entry asked for is kept while resolving"
        );
        assert_eq!(shown(&state, None), None, "and none is chosen for it");

        let content = Content::Playlist(workspace::PlaylistContent {
            id: ContentId::of_item(&item),
            item_id: item,
            display_name: "P".into(),
            entries: vec![entry(&item, "A"), entry(&item, "B")],
            playlists: vec![workspace::PlaylistSpan {
                id: ContentId::of_playlist(&item, "P"),
                name: "P".into(),
                entries: 2,
            }],
            warnings: Vec::new(),
        });
        let resolved = workspace_store::complete(&state, item, jobs[0].attempt, Ok(content));
        assert_eq!(shown(&resolved, Some(b.clone())), Some(b.clone()));
        let (reloading, _) =
            workspace_store::apply(&resolved, workspace_store::DocCommand::Resolve(item));
        assert_eq!(
            shown(&reloading, Some(b.clone())),
            Some(b.clone()),
            "a reload keeps the entry on screen"
        );
        assert_eq!(shown(&resolved, None), Some(a.clone()));
        let gone = ContentId::of_entry(&item, "P", "GONE");
        assert_eq!(shown(&resolved, Some(gone.clone())), Some(a.clone()));

        let (hidden, _) = workspace_store::apply(
            &resolved,
            workspace_store::DocCommand::SetHidden {
                target: HiddenRef::Entry(a.clone()),
                hidden: true,
            },
        );
        assert_eq!(shown(&hidden, Some(gone)), Some(b.clone()));
        assert_eq!(
            shown(&hidden, Some(a.clone())),
            Some(a),
            "asking for a hidden entry shows it"
        );
        let (all, _) = workspace_store::apply(
            &hidden,
            workspace_store::DocCommand::SetHidden {
                target: HiddenRef::Entry(b),
                hidden: true,
            },
        );
        assert_eq!(
            shown(&all, None),
            Some(ContentId::of_entry(&item, "P", "A"))
        );
    }
}
