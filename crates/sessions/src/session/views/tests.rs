use super::{super::layout::Axis, *};
use serde_json::json;
use workspace_store::HiddenRef;

#[test]
fn a_view_reads_as_the_wire_writes_it_and_an_id_that_names_no_item_is_refused() {
    let (front, behind) = (ItemId::generate(), ItemId::generate());
    let message = json!({
        "sidebar": { "view": "sessions", "open": false },
        "active": 2,
        "viewers": {
            "1": { "item": behind.to_string(), "pinned": false },
            "2": {
                "item": front.to_string(),
                "pinned": true,
                "clock": { "frame": 7, "playing": true, "speedCenti": 150, "atUs": -3 },
            },
        },
        "layout": {
            "type": "split", "axis": "row", "share": 3000,
            "first": { "type": "pane", "viewer": 1 },
            "second": { "type": "pane", "viewer": 2 },
        },
        "overlays": { "objectDetails": true, "laneNames": false },
    });
    let view: View = serde_json::from_str(&message.to_string()).unwrap();
    assert_eq!(view.sidebar.view, SidebarView::Sessions);
    assert_eq!(view.active, Some(ViewerId(2)));
    assert_eq!(view.viewers[&ViewerId(1)].item, behind);
    assert!(view.viewers[&ViewerId(2)].pinned);
    assert_eq!(view.viewers[&ViewerId(2)].clock.unwrap().speed_centi, 150);
    assert!(!view.overlays.lane_names);
    assert_eq!(serde_json::to_value(&view).unwrap(), message);

    let mut bad = message;
    bad["viewers"]["1"]["item"] = json!("not an item");
    assert!(serde_json::from_value::<View>(bad).is_err());
}

fn pane(viewer: u32) -> Box<Layout> {
    Box::new(Layout::Pane {
        viewer: ViewerId(viewer),
    })
}

fn looking(item: ItemId) -> ViewerView {
    ViewerView {
        item,
        pinned: false,
        clock: None,
        entry: None,
        viewport: None,
    }
}

#[test]
fn restricting_drops_the_viewers_of_closed_items_and_unplaced_ones_and_keeps_the_other_shares() {
    let video = workspace::intake::video(
        std::path::Path::new("v.mp4"),
        &[],
        &handlers::Handlers::built_in(),
    )
    .unwrap();
    let (open, closed) = (video.id, ItemId::generate());
    let (workspace, _) = workspace_store::apply(
        &WorkspaceState::default(),
        workspace_store::DocCommand::AddItems {
            items: vec![video],
            open: false,
        },
    );
    let (workspace, _) =
        workspace_store::apply(&workspace, workspace_store::DocCommand::OpenViewer(open));
    let split = |axis, share, first, second| Layout::Split {
        axis,
        share,
        first,
        second,
    };
    // Two panes show the open item, one the closed item, and viewer 4 is in no pane.
    let view = View {
        active: Some(ViewerId(2)),
        viewers: (1..=4)
            .map(|id| (ViewerId(id), looking(if id == 2 { closed } else { open })))
            .collect(),
        layout: Some(split(
            Axis::Row,
            3000,
            pane(1),
            Box::new(split(Axis::Column, 7000, pane(2), pane(3))),
        )),
        ..View::default()
    };
    let restricted = restrict(&view, &Doc { workspace });
    assert_eq!(
        restricted.viewers.keys().copied().collect::<Vec<_>>(),
        [ViewerId(1), ViewerId(3)]
    );
    assert_eq!(restricted.active, None);
    assert_eq!(
        restricted.layout,
        Some(split(Axis::Row, 3000, pane(1), pane(3)))
    );
}

fn entry(item: &ItemId, label: &str) -> Entry {
    Entry {
        id: ContentId::of_entry(item, "P", label),
        label: label.into(),
        video: workspace::VideoRef::File(format!("{label}.mp4").into()),
        overlays: Vec::new(),
        starts_hidden: false,
        lanes_starting_hidden: Vec::new(),
        metadata: Default::default(),
    }
}

#[test]
fn a_viewer_keeps_its_entry_or_closes_when_it_disappears() {
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
            open: false,
        },
    );
    let (state, _) = workspace_store::apply(&state, workspace_store::DocCommand::OpenViewer(item));
    let viewer = |entry: Option<ContentId>| View {
        active: Some(ViewerId(1)),
        viewers: [(
            ViewerId(1),
            ViewerView {
                entry,
                ..looking(item)
            },
        )]
        .into(),
        layout: Some(*pane(1)),
        ..View::default()
    };
    let shown = |workspace: &WorkspaceState, asked: Option<ContentId>| {
        let doc = Doc {
            workspace: workspace.clone(),
        };
        restrict(&viewer(asked), &doc).viewers[&ViewerId(1)]
            .entry
            .clone()
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
            matches: None,
            sequence_groups: None,
            metadata: Default::default(),
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
    let mut edited = resolved.clone();
    if let Some(Resolution::Resolved {
        content: Content::Playlist(playlist),
        ..
    }) = edited.resolutions.get_mut(&item)
    {
        playlist.entries.retain(|entry| entry.id != b);
    }
    let closed = restrict(
        &viewer(Some(b.clone())),
        &Doc {
            workspace: edited.clone(),
        },
    );
    assert!(closed.viewers.is_empty());
    assert!(closed.layout.is_none());
    assert!(closed.active.is_none());
    assert_eq!(shown(&edited, Some(a.clone())), Some(a.clone()));

    let (hidden, _) = workspace_store::apply(
        &resolved,
        workspace_store::DocCommand::SetHidden {
            target: HiddenRef::Entry(a.clone()),
            hidden: true,
        },
    );
    assert_eq!(shown(&hidden, None), Some(b.clone()));
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
