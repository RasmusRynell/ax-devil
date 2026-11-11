use super::*;
use crate::test_support::TestApp;
use api_types::{Audience, DocCommand as DocWire, bookmarks::BookmarkCommand as BookmarkWire};
use foundation::HostPath;
use serde_json::{Value, json};
use sessions::session::{SessionState, Use};
use std::collections::BTreeSet;
use workspace::Workspace;
use workspace_store::Saved as SavedAs;

/// What the Session will need for a case, which is the rule `scope` mirrors.
enum Expect {
    /// What `Command::needs` says of the command the wire command converts to.
    Converted,
    /// What it says of the command the files adapter runs for a workspace command, after its file work.
    Adapter(Box<Command>),
    /// Not reachable here: a staged playlist, or a file written with no command after it.
    Only(Scope),
}

impl Expect {
    fn adapter(command: Command) -> Self {
        Self::Adapter(Box::new(command))
    }
}

/// Every variant a client can send, named so that a new one fails to compile here until it is listed.
fn variant(wire: &SessionCommand) -> &'static str {
    match wire {
        SessionCommand::Content { command } => match command {
            DocWire::AddItems { .. } => "addItems",
            DocWire::AddResolved { .. } => "addResolved",
            DocWire::OpenViewer { .. } => "openViewer",
            DocWire::CloseViewer { .. } => "closeViewer",
            DocWire::Resolve { .. } => "resolve",
            DocWire::Remove { .. } => "remove",
            DocWire::RenameItem { .. } => "renameItem",
            DocWire::EditItem { .. } => "editItem",
            DocWire::EditResolved { .. } => "editResolved",
            DocWire::SetHidden { .. } => "setHidden",
            DocWire::SetLaneMatching { .. } => "setLaneMatching",
            DocWire::RenameWorkspace { .. } => "renameWorkspace",
            DocWire::Bookmark { command } => match command {
                BookmarkWire::Add { .. } => "bookmarkAdd",
                BookmarkWire::Edit { .. } => "bookmarkEdit",
                BookmarkWire::Remove { .. } => "bookmarkRemove",
                BookmarkWire::SetReviewed { .. } => "bookmarkSetReviewed",
            },
        },
        SessionCommand::Follow => "follow",
        SessionCommand::SetViewport { .. } => "setViewport",
        SessionCommand::SetOverlay { .. } => "setOverlay",
        SessionCommand::Control { .. } => "control",
        SessionCommand::RequestTicket { .. } => "requestTicket",
        SessionCommand::Workspace { command } => match command {
            WorkspaceCommand::New => "workspaceNew",
            WorkspaceCommand::Open { .. } => "workspaceOpen",
            WorkspaceCommand::Save { .. } => "workspaceSave",
            WorkspaceCommand::Keep => "workspaceKeep",
            WorkspaceCommand::Discard => "workspaceDiscard",
            WorkspaceCommand::ExportBookmarks { .. } => "workspaceExportBookmarks",
        },
    }
}

const VARIANTS: [&str; 27] = [
    "addItems",
    "addResolved",
    "openViewer",
    "closeViewer",
    "resolve",
    "remove",
    "renameItem",
    "editItem",
    "editResolved",
    "setHidden",
    "setLaneMatching",
    "renameWorkspace",
    "bookmarkAdd",
    "bookmarkEdit",
    "bookmarkRemove",
    "bookmarkSetReviewed",
    "follow",
    "setViewport",
    "setOverlay",
    "control",
    "requestTicket",
    "workspaceNew",
    "workspaceOpen",
    "workspaceSave",
    "workspaceKeep",
    "workspaceDiscard",
    "workspaceExportBookmarks",
];

fn needed(command: &Command) -> Option<Scope> {
    match command.needs() {
        Use::Command(scope) => Some(scope),
        Use::Stream(_) => None,
    }
}

fn content(command: Value) -> Value {
    json!({ "type": "content", "command": command })
}

fn on_workspace(kind: &str, fields: Value) -> Value {
    let mut command = fields;
    command["type"] = json!(kind);
    json!({ "type": "workspace", "command": command })
}

fn cases() -> Vec<(Value, Expect)> {
    let item = ItemId::generate().to_string();
    let video = json!({ "type": "Video", "path": "/videos/a.mp4" });
    let live = json!({ "type": "Live", "host": "10.0.0.1", "overlay": { "mode": "none" } });
    let replace = || {
        Command::Content(workspace_store::DocCommand::Replace {
            workspace: Workspace::default(),
            file: None,
            saved: SavedAs::As(Workspace::default()),
        })
    };
    let doc = |kind: &str, fields: Value| {
        let mut command = fields;
        command["type"] = json!(kind);
        content(command)
    };
    let on_item = |kind: &str| doc(kind, json!({ "item": item }));
    let bookmark = |command: Value| doc("bookmark", json!({ "command": command }));
    vec![
        (
            doc("addItems", json!({ "items": [video], "open": true })),
            Expect::Converted,
        ),
        (
            doc("addItems", json!({ "items": [live], "open": true })),
            Expect::Converted,
        ),
        (
            doc("addItems", json!({ "items": [live, video], "open": true })),
            Expect::Converted,
        ),
        (
            doc("addItems", json!({ "items": [], "open": true })),
            Expect::Converted,
        ),
        (
            doc("addResolved", json!({ "item": item })),
            Expect::Only(Scope::Host),
        ),
        (on_item("openViewer"), Expect::Converted),
        (on_item("closeViewer"), Expect::Converted),
        (on_item("resolve"), Expect::Converted),
        (on_item("remove"), Expect::Converted),
        (
            doc("renameItem", json!({ "item": item, "label": "x" })),
            Expect::Converted,
        ),
        (
            doc("editItem", json!({ "item": item, "new": video })),
            Expect::Converted,
        ),
        (
            doc("editItem", json!({ "item": item, "new": live })),
            Expect::Converted,
        ),
        (
            doc(
                "editResolved",
                json!({ "item": item, "ticket": ItemId::generate().to_string() }),
            ),
            Expect::Only(Scope::Host),
        ),
        (
            doc(
                "setHidden",
                json!({ "target": { "entry": item }, "hidden": true }),
            ),
            Expect::Converted,
        ),
        (
            doc(
                "setLaneMatching",
                json!({ "item": item, "slot": "a", "policy": {
                    "basis": null, "toleranceMs": null, "sticky": { "enabled": false, "timeoutMs": null } } }),
            ),
            Expect::Converted,
        ),
        (
            doc("renameWorkspace", json!({ "name": "x", "epoch": 0 })),
            Expect::Converted,
        ),
        (
            bookmark(
                json!({ "type": "add", "item": item, "entry": item, "frame": 0, "timeUs": 0 }),
            ),
            Expect::Converted,
        ),
        (
            bookmark(json!({ "type": "edit", "id": BookmarkId::generate().to_string() })),
            Expect::Converted,
        ),
        (
            bookmark(json!({ "type": "remove", "id": BookmarkId::generate().to_string() })),
            Expect::Converted,
        ),
        (
            bookmark(json!({ "type": "setReviewed", "item": item, "reviewed": true })),
            Expect::Converted,
        ),
        (json!({ "type": "follow" }), Expect::Converted),
        (
            json!({ "type": "setViewport", "viewer": 0 }),
            Expect::Converted,
        ),
        (
            json!({ "type": "setOverlay", "overlay": "laneNames", "shown": true }),
            Expect::Converted,
        ),
        (
            json!({ "type": "control", "control": { "type": "stop" } }),
            Expect::Converted,
        ),
        (
            json!({ "type": "requestTicket", "key": {
                "content": item, "generation": 0, "entry": item, "viewer": 0 } }),
            Expect::Converted,
        ),
        (on_workspace("new", json!({})), Expect::adapter(replace())),
        (
            on_workspace("open", json!({ "path": "/w.axw" })),
            Expect::adapter(replace()),
        ),
        (
            on_workspace("save", json!({})),
            Expect::adapter(Command::Content(workspace_store::DocCommand::MarkSaved {
                workspace: Workspace::default(),
                file: HostPath::new("/w.axw"),
            })),
        ),
        (
            on_workspace("keep", json!({})),
            Expect::adapter(Command::Content(workspace_store::DocCommand::Keep)),
        ),
        (
            on_workspace("discard", json!({})),
            Expect::adapter(replace()),
        ),
        (
            on_workspace("exportBookmarks", json!({ "path": "/b.csv" })),
            Expect::Only(Scope::Host),
        ),
    ]
}

/// The wire's scope is decided before the command is converted, so it is a copy of the rule in `Command::needs` that
/// must not drift. A scanned camera's stream is the one difference, kept apart: its `found` address is gone once
/// converted, so only the wire can ask the Owner's `Control` of it (`adding_a_found_camera_*` in the reader tests).
#[tokio::test]
async fn the_wire_scope_is_the_scope_the_converted_command_needs() {
    let app = TestApp::new();
    let session = SessionState::new(sessions::SessionId::generate(), false);
    let mut seen = BTreeSet::new();
    for (value, expect) in cases() {
        let wire: SessionCommand = serde_json::from_value(value.clone()).unwrap();
        seen.insert(variant(&wire));
        let state = &app.state;
        let defaults = crate::session::live::defaults(&state.settings.state(), false);
        let saved = Saved::new(
            &session.doc.workspace.workspace,
            false,
            Audience { host_details: true },
        );
        let converted = command(
            wire.clone(),
            &state.plugins.handlers(),
            &state.staged,
            "session",
            &defaults,
            &state.network,
            saved,
        );
        let needs = match (expect, converted) {
            (Expect::Converted, Ok(Request::Session(command, _))) => needed(&command),
            (Expect::Adapter(command), Ok(Request::Workspace(_))) => needed(&command),
            (Expect::Only(scope), _) => Some(scope),
            (_, other) => panic!("{value} was not converted as expected: {}", other.is_ok()),
        };
        assert_eq!(scope(&wire), needs, "{value}");
    }
    assert_eq!(seen, BTreeSet::from(VARIANTS));
}
