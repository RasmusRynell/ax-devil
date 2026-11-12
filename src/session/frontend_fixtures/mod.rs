//! The Session Docs the frontend tests read, written by the backend's own reducers and projection.
//!
//! Each scenario builds a session through `transition`, projects it with [`project`] for the owner and for a
//! watcher, and records the commands a client may send from a named state. `frontend_fixtures_match_the_backend`
//! compares them with `fixtures/session/<scenario>.json`, plus `seat-access.json`. Regenerate explicitly with
//! `UPDATE_GOLDENS=1 make test-backend TEST=frontend_fixture`.
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
use std::{collections::BTreeMap, path::Path};
use workspace::Environment;

/// One scenario's file: the ids its keys name, the Doc in each named state for both audiences, and the commands
/// that lead from one state to another.
#[derive(Serialize)]
struct Recording {
    ids: BTreeMap<String, String>,
    docs: BTreeMap<String, Audiences>,
    steps: Vec<Step>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    views: BTreeMap<String, sessions::session::View>,
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
            views: BTreeMap::new(),
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

    /// Records the Doc and reconciled presenter together for frontend placement tests.
    fn view_state(&mut self, name: &str, session: &SessionState) {
        self.state(name, session);
        self.views.insert(name.into(), (*session.presenter).clone());
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
    match &command {
        WorkspaceCommand::Keep => {}
        WorkspaceCommand::New | WorkspaceCommand::Discard
            if session.doc.workspace.file.is_none() => {}
        other => panic!("a recorded step cannot run {other:?}, which reads or writes a file"),
    }
    super::files::sent(&command, workspace::Workspace::default(), None)
        .expect("Keep, New and Discard send a command")
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
    let mut files: BTreeMap<String, String> = BTreeMap::new();
    for (name, build) in scenarios {
        files.insert(format!("{name}.json"), text(&build()));
    }
    files.insert(format!("{SEAT_ACCESS}.json"), text(&seat_access()));
    crate::test_support::fixtures::check(&fixtures, "session", &files);
}

#[test]
fn scenario_names_are_unique_file_stems() {
    assert!(check_names(&["a"]).is_ok());
    assert!(check_names(&[]).is_err());
    assert!(check_names(&["a", "a"]).is_err());
    assert!(check_names(&[SEAT_ACCESS]).is_err());
    assert!(check_names(&["../a"]).is_err());
}
