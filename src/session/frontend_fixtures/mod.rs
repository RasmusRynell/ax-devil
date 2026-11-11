//! The Session Docs the frontend tests read, written by the backend's own reducers and projection.
//!
//! Each scenario builds a session through `transition`, projects it with [`project`] for the owner and for a
//! watcher, and records the commands a client may send from a named state. `frontend_fixtures_match_the_backend`
//! writes them to `fixtures/session/<scenario>.json`, plus `seat-access.json`, and `make check` fails when that changes
//! them. Regenerate with `make test-backend TEST=frontend_fixtures`.
//!
//! A scenario lives in `base`, `tree`, `viewer`, `shell` or `live` and is built with [`Scene`]; a new shape is a new
//! scenario, or a new state or step in one.

mod base;
mod live;
mod outcomes;
mod scene;
mod shell;
mod tree;
mod viewer;

use super::{project::audience_of, project::project_in, saved::Saved, wire};
use crate::test_support::TestApp;
use api_types::{Audience, DocWire, SeatAccess, SessionCommand, WorkspaceCommand};
use outcomes::Outcomes;
use scene::Scene;
use serde::Serialize;
use serde_json::{Value, json};
use sessions::session::{
    Access, Command, Control, Fresh, Input, Lease, Outcome, SessionState, can, transition,
};
use std::{collections::BTreeMap, fs, io, path::Path};
use workspace::Environment;
use workspace_store::DocCommand;

/// One scenario's file: the ids its keys name, the Doc in each named state for both audiences, and the commands
/// that lead from one state to another.
#[derive(Serialize)]
struct Recording {
    ids: BTreeMap<String, String>,
    docs: BTreeMap<String, Audiences>,
    steps: Vec<Step>,
    #[serde(skip)]
    outcomes: Outcomes,
    /// Where `$NAME` references read their values when the Docs are projected: nowhere, unless a scenario says.
    #[serde(skip)]
    environment: Environment,
    #[serde(skip)]
    app: TestApp,
}

/// A Doc as each audience receives it.
#[derive(Serialize)]
struct Audiences {
    owner: DocWire,
    guest: DocWire,
}

/// A command a client sends in state `from`, and the state it leads to.
#[derive(Serialize)]
struct Step {
    from: String,
    command: Value,
    to: String,
}

impl Recording {
    /// A recording of `scene`'s ids, with no state yet. Needs a Tokio runtime for the app that converts commands.
    fn new(scene: &Scene) -> Self {
        Self {
            ids: scene.ids().clone(),
            docs: BTreeMap::new(),
            steps: Vec::new(),
            outcomes: scene.outcomes().clone(),
            environment: Environment::literal(),
            app: TestApp::new(),
        }
    }

    /// A recording of `scene` whose only state is `initial`.
    fn initial(scene: &Scene) -> Self {
        let mut recording = Self::new(scene);
        recording.state("initial", scene.session());
        recording
    }

    /// Records `session` as the state `name`, projected for the owner and for a Guest. A state recorded twice must
    /// be the same Doc: a step may lead to a state the scenario built directly.
    fn state(&mut self, name: &str, session: &SessionState) {
        let audience =
            |host_details| project_in(&session.doc, Audience { host_details }, &self.environment);
        let both = Audiences {
            owner: audience(true),
            guest: audience(false),
        };
        match self.docs.get(name) {
            Some(recorded) => assert_eq!(text(recorded), text(&both), "state {name} differs"),
            None => {
                self.docs.insert(name.to_owned(), both);
            }
        }
    }

    /// Records `command` sent in state `from`, which `session` is, ending in state `to`.
    fn branch(&mut self, from: &str, session: &SessionState, command: Value, to: &str) {
        self.step(from, &mut session.clone(), command, to);
    }

    /// Records each of `commands` one after the other, starting from state `from`, which `session` is, each leading to
    /// the state named with it.
    fn chain<'a>(
        &mut self,
        from: &str,
        session: &SessionState,
        commands: impl IntoIterator<Item = (Value, &'a str)>,
    ) {
        let mut session = session.clone();
        let mut at = from.to_owned();
        for (command, to) in commands {
            self.step(&at, &mut session, command, to);
            to.clone_into(&mut at);
        }
    }

    /// Applies `command`, the `cmd` JSON a client sends, to `session` as the owner and records the result as state
    /// `to`. It is deserialized as the wire command and converted as the socket does, so a command the backend
    /// would refuse fails here. `session` should be a clone of the state named `from`, which must be recorded.
    fn step(&mut self, from: &str, session: &mut SessionState, command: Value, to: &str) {
        assert!(
            self.docs.contains_key(from),
            "no state {from} to start from"
        );
        let wire_command: SessionCommand = serde_json::from_value(command.clone())
            .unwrap_or_else(|error| panic!("{command} is not a session command: {error}"));
        let state = &self.app.state;
        let defaults = super::live::defaults(&state.settings.state(), false);
        let saved = Saved::new(
            &session.doc.workspace.workspace,
            session.expands,
            audience_of(session, scene::owner_of(session), Lease(0)),
        );
        let request = wire::command(
            wire_command,
            &state.plugins.handlers(),
            &state.staged,
            &session.id.to_string(),
            &defaults,
            &state.network,
            saved,
        );
        let command_in = match request {
            Ok(wire::Request::Session(command_in, _)) => command_in,
            Ok(wire::Request::Workspace(file_command)) => workspace_input(file_command, session),
            Err(refusal) => panic!("{command} was refused: {refusal:?}"),
        };
        scene::run(session, &self.outcomes, command_in);
        self.state(to, session);
        self.steps.push(Step {
            from: from.to_owned(),
            command,
            to: to.to_owned(),
        });
    }
}

/// The input the adapter (`files::Files::run`) gives the Session for a workspace command, after the file work it does
/// first. Only the commands that need no file are supported: Keep, and New or Discard of a workspace without one.
fn workspace_input(command: WorkspaceCommand, session: &SessionState) -> Command {
    let empty = workspace::Workspace::default();
    let replace = Command::Content(DocCommand::Replace {
        workspace: empty.clone(),
        file: None,
        saved: workspace_store::Saved::As(empty),
    });
    match command {
        WorkspaceCommand::Keep => Command::Content(DocCommand::Keep),
        WorkspaceCommand::New | WorkspaceCommand::Discard
            if session.doc.workspace.file.is_none() =>
        {
            replace
        }
        other => panic!("a recorded step cannot run {other:?}, which reads or writes a file"),
    }
}

/// The command a client sends to open the viewer of `item`.
fn open_viewer(item: &str) -> Value {
    content("openViewer", json!({ "item": item }))
}

/// The command a client sends to close the viewer of `item`.
fn close_viewer(item: &str) -> Value {
    content("closeViewer", json!({ "item": item }))
}

/// The command a client sends to remove `item`.
fn remove(item: &str) -> Value {
    content("remove", json!({ "item": item }))
}

/// The command a client sends to hide or show an entry, or one of its lanes.
fn set_hidden(entry: &str, lane: Option<&str>, hidden: bool) -> Value {
    let target = match lane {
        Some(lane) => json!({ "entry": entry, "lane": lane }),
        None => json!({ "entry": entry }),
    };
    content("setHidden", json!({ "target": target, "hidden": hidden }))
}

/// A content command of type `kind` with `fields`.
fn content(kind: &str, fields: Value) -> Value {
    let mut command = fields;
    command["type"] = json!(kind);
    json!({ "type": "content", "command": command })
}

/// Builds one scenario.
type Build = fn() -> Recording;

/// Every scenario, by file name without the extension.
const GROUPS: &[&[(&str, Build)]] = &[
    base::SCENARIOS,
    tree::SCENARIOS,
    viewer::SCENARIOS,
    shell::SCENARIOS,
    live::SCENARIOS,
];

/// What each kind of seat may do, as `can` decides it in a session shared at that access.
fn seat_access() -> BTreeMap<String, sessions::session::Can> {
    let mut owner = SessionState::new(sessions::SessionId::generate(), false);
    let seat = owner.seat_owner();
    let mut rights = BTreeMap::new();
    let key = |access: SeatAccess| match serde_json::to_value(access) {
        Ok(serde_json::Value::String(name)) => name,
        other => panic!("a seat access is a string, not {other:?}"),
    };
    rights.insert(
        key(SeatAccess::Owner),
        can(&owner, seat, Lease(0)).expect("the owner has a seat"),
    );
    for access in [Access::Watch, Access::Explore] {
        let shared = |input| match transition(&owner, input) {
            Ok((next, _)) => next,
            Err(refusal) => panic!("{refusal:?}"),
        };
        let shared_session = shared(Input::Command {
            seat,
            lease: Lease(0),
            command: Command::Control(Control::Share { access }),
            fresh: Fresh::now(),
        });
        let (joined, fx) = transition(&shared_session, Input::Join { secret: None })
            .unwrap_or_else(|refusal| panic!("{refusal:?}"));
        let Outcome::Seated { seat, lease } = fx.outcome else {
            panic!("a Guest joins a shared session");
        };
        rights.insert(
            key(access.into()),
            can(&joined, seat, lease).expect("the Guest has a seat"),
        );
    }
    rights
}

/// `value` as the committed text: sorted keys, two-space indent, LF, a final newline.
fn text(value: &impl Serialize) -> String {
    let value = serde_json::to_value(value).expect("a fixture is JSON");
    let mut text = serde_json::to_string_pretty(&value).expect("a fixture is JSON");
    text.push('\n');
    text
}

/// Checks, before anything is written, that `fixtures` holds a real `session` folder (or none yet) of regular files
/// only: a symlink or a sub-folder there would make the writes and the pruning reach outside it.
fn check_folder(fixtures: &Path) -> Result<(), String> {
    let plain = |path: &Path| match fs::symlink_metadata(path) {
        Ok(found) if found.is_dir() => Ok(true),
        Ok(_) => Err(format!("{} is not a plain folder", path.display())),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(format!("{} cannot be read: {error}", path.display())),
    };
    if !plain(fixtures)? {
        return Err(format!("{} does not exist", fixtures.display()));
    }
    let session = fixtures.join("session");
    if !plain(&session)? {
        return Ok(());
    }
    let entries = fs::read_dir(&session).map_err(|error| format!("{session:?}: {error}"))?;
    for entry in entries {
        let entry = entry.map_err(|error| format!("{session:?}: {error}"))?;
        let kind = entry
            .file_type()
            .map_err(|error| format!("{:?}: {error}", entry.path()))?;
        if !kind.is_file() {
            let path = entry.path();
            return Err(format!("{} is not a regular file", path.display()));
        }
    }
    Ok(())
}

/// The file written besides the scenarios.
const SEAT_ACCESS: &str = "seat-access";

/// The scenario names, at least one, which with the reserved `seat-access` are file stems and are not repeated.
fn check_names(scenarios: &[&str]) -> Result<(), String> {
    if scenarios.is_empty() {
        return Err("no scenario is registered".into());
    }
    let names = [scenarios, &[SEAT_ACCESS]].concat();
    for (at, name) in names.iter().enumerate() {
        let stem = !name.is_empty()
            && name
                .chars()
                .all(|letter| letter.is_ascii_alphanumeric() || letter == '-');
        if !stem {
            return Err(format!("scenario {name:?} is not a file stem"));
        }
        if names[..at].contains(name) {
            return Err(format!("scenario {name} is built twice"));
        }
    }
    Ok(())
}

#[tokio::test]
async fn frontend_fixtures_match_the_backend() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures");
    let scenarios: Vec<(&str, Build)> = GROUPS.iter().copied().flatten().copied().collect();
    let names: Vec<&str> = scenarios.iter().map(|(name, _)| *name).collect();
    check_names(&names).unwrap_or_else(|reason| panic!("{reason}"));
    check_folder(&fixtures).unwrap_or_else(|reason| panic!("{reason}"));
    let mut files: BTreeMap<String, String> = BTreeMap::new();
    for (name, build) in scenarios {
        files.insert(format!("{name}.json"), text(&build()));
    }
    files.insert(format!("{SEAT_ACCESS}.json"), text(&seat_access()));
    let folder = fixtures.join("session");
    fs::create_dir_all(&folder).expect("the fixtures folder exists");
    for (name, wanted) in &files {
        let path = folder.join(name);
        if fs::read_to_string(&path).ok().as_ref() != Some(wanted) {
            fs::write(&path, wanted).expect("a fixture is written");
        }
    }
    // Only plain files are here (checked above), so removing one never leaves the folder.
    for entry in fs::read_dir(&folder).expect("the fixtures folder reads") {
        let path = entry.expect("a fixture entry reads").path();
        let named = path
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| files.contains_key(name));
        let json = path
            .extension()
            .is_some_and(|extension| extension == "json");
        if json && !named {
            fs::remove_file(&path).expect("a stale fixture is removed");
        }
    }
}

#[cfg(unix)]
#[test]
fn a_fixtures_folder_that_leaves_itself_is_refused_untouched() {
    use std::os::unix::fs::symlink;
    let listing = |folder: &Path| {
        let mut found: Vec<String> = fs::read_dir(folder)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        found.sort();
        found
    };
    let temp = tempfile::tempdir().unwrap();
    let outside = temp.path().join("outside");
    fs::create_dir(&outside).unwrap();
    fs::write(outside.join("kept.json"), "kept").unwrap();
    let refused = |name: &str, setup: &dyn Fn(&Path)| {
        let fixtures = temp.path().join(name);
        fs::create_dir_all(fixtures.join("session")).unwrap();
        setup(&fixtures);
        let before = listing(&fixtures.join("session"));
        assert!(check_folder(&fixtures).is_err(), "{name} is accepted");
        assert_eq!(listing(&fixtures.join("session")), before);
        assert_eq!(listing(&outside), ["kept.json"]);
    };
    refused("file", &|fixtures| {
        symlink(outside.join("kept.json"), fixtures.join("session/a.json")).unwrap();
    });
    refused("folder", &|fixtures| {
        fs::remove_dir(fixtures.join("session")).unwrap();
        symlink(&outside, fixtures.join("session")).unwrap();
    });
    refused("parent", &|fixtures| {
        let real = fixtures.with_extension("real");
        fs::rename(fixtures, &real).unwrap();
        symlink(&real, fixtures).unwrap();
    });
    refused("sub", &|fixtures| {
        fs::create_dir(fixtures.join("session/sub")).unwrap();
    });
    let fresh = temp.path().join("fresh");
    fs::create_dir(&fresh).unwrap();
    assert!(check_folder(&fresh).is_ok());
    assert!(check_names(&["a"]).is_ok());
    assert!(check_names(&[]).is_err());
    assert!(check_names(&["a", "a"]).is_err());
    assert!(check_names(&[SEAT_ACCESS]).is_err());
    assert!(check_names(&["../a"]).is_err());
}
