use super::*;
use crate::session::files::sent;
use crate::test_support::TestApp;
use api_types::{Audience, DocCommand as DocWire, bookmarks::BookmarkCommand as BookmarkWire};
use foundation::HostPath;
use serde_json::{Value, json};
use sessions::session::{SessionState, Use};
use std::collections::BTreeSet;
use workspace::Workspace;

/// Names every command a client can send. The match is exhaustive, so a new command does not compile until it has an
/// arm here; its name is then listed in `VARIANTS`, and the test fails until `cases` sends one.
macro_rules! variants {
    ($($pattern:pat => $name:literal,)*) => {
        fn variant(wire: &SessionCommand) -> &'static str {
            match wire {
                $($pattern => $name,)*
            }
        }

        const VARIANTS: &[&str] = &[$($name),*];
    };
}

variants! {
    SessionCommand::Content { command: DocWire::AddItems { .. } } => "addItems",
    SessionCommand::Content { command: DocWire::AddResolved { .. } } => "addResolved",
    SessionCommand::Content { command: DocWire::OpenViewer { .. } } => "openViewer",
    SessionCommand::Content { command: DocWire::CloseViewer { .. } } => "closeViewer",
    SessionCommand::Content { command: DocWire::Resolve { .. } } => "resolve",
    SessionCommand::Content { command: DocWire::Remove { .. } } => "remove",
    SessionCommand::Content { command: DocWire::RenameItem { .. } } => "renameItem",
    SessionCommand::Content { command: DocWire::EditItem { .. } } => "editItem",
    SessionCommand::Content { command: DocWire::EditResolved { .. } } => "editResolved",
    SessionCommand::Content { command: DocWire::SetHidden { .. } } => "setHidden",
    SessionCommand::Content { command: DocWire::SetLaneMatching { .. } } => "setLaneMatching",
    SessionCommand::Content { command: DocWire::RenameWorkspace { .. } } => "renameWorkspace",
    SessionCommand::Content { command: DocWire::Bookmark { command: BookmarkWire::Add { .. } } } => "bookmarkAdd",
    SessionCommand::Content { command: DocWire::Bookmark { command: BookmarkWire::Edit { .. } } } => "bookmarkEdit",
    SessionCommand::Content { command: DocWire::Bookmark { command: BookmarkWire::Remove { .. } } } => "bookmarkRemove",
    SessionCommand::Content { command: DocWire::Bookmark { command: BookmarkWire::SetReviewed { .. } } } => "bookmarkSetReviewed",
    SessionCommand::Follow => "follow",
    SessionCommand::SetViewport { .. } => "setViewport",
    SessionCommand::SetOverlay { .. } => "setOverlay",
    SessionCommand::Control { .. } => "control",
    SessionCommand::RequestTicket { .. } => "requestTicket",
    SessionCommand::Workspace { command: WorkspaceCommand::New } => "workspaceNew",
    SessionCommand::Workspace { command: WorkspaceCommand::Open { .. } } => "workspaceOpen",
    SessionCommand::Workspace { command: WorkspaceCommand::Save { .. } } => "workspaceSave",
    SessionCommand::Workspace { command: WorkspaceCommand::Keep } => "workspaceKeep",
    SessionCommand::Workspace { command: WorkspaceCommand::Discard } => "workspaceDiscard",
    SessionCommand::Workspace { command: WorkspaceCommand::ExportBookmarks { .. } } => "workspaceExportBookmarks",
}

/// How a sample's scope is checked.
enum Expect {
    /// Against `Command::needs` of the command it converts to.
    Converted,
    /// Against `Command::needs` of the command the files adapter sends after its file work (`files::sent`).
    Sent,
    /// Against this scope, written here: the exceptions in `scope`'s documentation, which have no comparable command
    /// (the staged item is converted only with its ticket; the export sends none).
    Fixed(Scope),
}

fn doc(kind: &str, fields: Value) -> Value {
    let mut command = fields;
    command["type"] = json!(kind);
    json!({ "type": "content", "command": command })
}

fn workspace(kind: &str, fields: Value) -> Value {
    let mut command = fields;
    command["type"] = json!(kind);
    json!({ "type": "workspace", "command": command })
}

/// One command of every variant, and of every shape of value that decides its scope.
fn cases() -> Vec<(Value, Expect)> {
    use Expect::{Converted, Fixed, Sent};
    let item = ItemId::generate().to_string();
    let ticket = ItemId::generate().to_string();
    let bookmark = BookmarkId::generate().to_string();
    let video = json!({ "type": "Video", "path": "/videos/a.mp4" });
    let live = json!({ "type": "Live", "host": "10.0.0.1", "overlay": { "mode": "none" } });
    let on_item = |kind: &str| doc(kind, json!({ "item": item }));
    let bookmarked = |command: Value| doc("bookmark", json!({ "command": command }));
    vec![
        (
            doc("addItems", json!({ "items": [video], "open": true })),
            Converted,
        ),
        (
            doc("addItems", json!({ "items": [live], "open": true })),
            Converted,
        ),
        (
            doc("addItems", json!({ "items": [live, video], "open": true })),
            Converted,
        ),
        (
            doc("addItems", json!({ "items": [], "open": true })),
            Converted,
        ),
        (
            doc("addResolved", json!({ "item": item })),
            Fixed(Scope::Host),
        ),
        (on_item("openViewer"), Converted),
        (on_item("closeViewer"), Converted),
        (on_item("resolve"), Converted),
        (on_item("remove"), Converted),
        (
            doc("renameItem", json!({ "item": item, "label": "x" })),
            Converted,
        ),
        (
            doc("editItem", json!({ "item": item, "new": video })),
            Converted,
        ),
        (
            doc("editItem", json!({ "item": item, "new": live })),
            Converted,
        ),
        (
            doc("editResolved", json!({ "item": item, "ticket": ticket })),
            Fixed(Scope::Host),
        ),
        (
            doc(
                "setHidden",
                json!({ "target": { "entry": item }, "hidden": true }),
            ),
            Converted,
        ),
        (
            doc(
                "setLaneMatching",
                json!({ "item": item, "slot": "a", "policy": {
                    "basis": null, "toleranceMs": null, "sticky": { "enabled": false, "timeoutMs": null } } }),
            ),
            Converted,
        ),
        (
            doc("renameWorkspace", json!({ "name": "x", "epoch": 0 })),
            Converted,
        ),
        (
            bookmarked(
                json!({ "type": "add", "item": item, "entry": item, "frame": 0, "timeUs": 0 }),
            ),
            Converted,
        ),
        (
            bookmarked(json!({ "type": "edit", "id": bookmark })),
            Converted,
        ),
        (
            bookmarked(json!({ "type": "remove", "id": bookmark })),
            Converted,
        ),
        (
            bookmarked(json!({ "type": "setReviewed", "item": item, "reviewed": true })),
            Converted,
        ),
        (json!({ "type": "follow" }), Converted),
        (json!({ "type": "setViewport", "viewer": 0 }), Converted),
        (
            json!({ "type": "setOverlay", "overlay": "laneNames", "shown": true }),
            Converted,
        ),
        (
            json!({ "type": "control", "control": { "type": "stop" } }),
            Converted,
        ),
        (
            json!({ "type": "requestTicket", "key": {
                "content": item, "generation": 0, "entry": item, "viewer": 0 } }),
            Converted,
        ),
        (workspace("new", json!({})), Sent),
        (workspace("open", json!({ "path": "/w.axw" })), Sent),
        (workspace("save", json!({})), Sent),
        (workspace("keep", json!({})), Sent),
        (workspace("discard", json!({})), Sent),
        (
            workspace("exportBookmarks", json!({ "path": "/b.csv" })),
            Fixed(Scope::Host),
        ),
    ]
}

/// The wire's scope is decided before the command is converted, so it is a copy of the rule in `Command::needs` that
/// must not drift: every command that converts to a Session command, or that the files adapter turns into one after
/// its file work, needs what that command needs. The rest are asserted as written (the exceptions in `scope`'s
/// documentation), and every variant must have a sample.
#[tokio::test]
async fn the_wire_scope_is_the_scope_the_converted_command_needs() {
    let app = TestApp::new();
    let session = SessionState::new(sessions::SessionId::generate(), false);
    let mut seen = BTreeSet::new();
    for (value, expect) in cases() {
        let wire: SessionCommand = serde_json::from_value(value.clone()).unwrap();
        seen.insert(variant(&wire));
        let wanted = match expect {
            Expect::Fixed(scope) => Some(scope),
            Expect::Sent => {
                let SessionCommand::Workspace { command } = &wire else {
                    panic!("{value} is not a workspace command");
                };
                let file = Some(HostPath::new("/w.axw"));
                let sent = sent(command, Workspace::default(), file);
                sent.as_ref().and_then(|sent| match sent.needs() {
                    Use::Command(scope) => Some(scope),
                    Use::Stream(_) => None,
                })
            }
            Expect::Converted => {
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
                let Ok(Request::Session(command, _)) = converted else {
                    panic!("{value} did not convert to a Session command");
                };
                match command.needs() {
                    Use::Command(scope) => Some(scope),
                    Use::Stream(_) => None,
                }
            }
        };
        assert_eq!(scope(&wire), wanted, "{value}");
    }
    let named: BTreeSet<_> = VARIANTS.iter().copied().collect();
    assert_eq!(named.len(), VARIANTS.len(), "a variant is named twice");
    assert_eq!(seen, named);
}
