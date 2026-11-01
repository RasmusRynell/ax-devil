//! The Session's rules: who may do what, what makes old authority die for good, and what a session of one does.
//!
//! `matrix` checks the role table on the pure transition; `revocation` and `streams` run real handles on paused time.

mod matrix;
mod revocation;
mod roster;
mod streams;

use playback::Authority;
use sessions::{
    CursorOptions,
    session::{
        Access, Command, Completer, Completion, ContentRef, Control, Handle, Reply, Runner,
        Service, SessionState, Spawn, StreamLease, Work,
    },
};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use tempfile::TempDir;
use workspace::ItemId;
use workspace_store::{DocCommand, Resolution};

pub(crate) const GRACE: Duration = Duration::from_secs(30);

/// Collects the jobs sessions ask for, so a test decides when each one finishes.
#[derive(Clone, Default)]
pub(crate) struct Jobs(Arc<Mutex<Vec<(Spawn, Completer)>>>);

impl Jobs {
    pub(crate) fn runner(&self) -> Arc<dyn Runner> {
        let jobs = self.clone();
        Arc::new(move |spawn, done| jobs.0.lock().unwrap().push((spawn, done)))
    }

    pub(crate) fn take(&self) -> Vec<(Spawn, Completer)> {
        std::mem::take(&mut *self.0.lock().unwrap())
    }

    /// Resolves every waiting job the way the real runner would.
    pub(crate) fn finish_all(&self) {
        for (spawn, done) in self.take() {
            let Work::Resolve(job) = &spawn.work;
            done.complete(Completion::Resolved(workspace_store::resolve(job)));
        }
    }
}

pub(crate) struct Rig {
    pub(crate) service: Service<u8>,
    pub(crate) jobs: Jobs,
    pub(crate) dir: TempDir,
}

impl Rig {
    pub(crate) fn new() -> Self {
        let jobs = Jobs::default();
        Self {
            service: Service::new(GRACE, jobs.runner()),
            jobs,
            dir: tempfile::tempdir().unwrap(),
        }
    }

    pub(crate) fn video(&self, name: &str) -> workspace::WorkspaceItem {
        let path = self.dir.path().join(name);
        std::fs::write(&path, "x").unwrap();
        workspace::intake::video(&path).unwrap()
    }

    /// An owner that shares with `access`, and a watcher that joined.
    pub(crate) fn shared(&self, access: Access) -> (Handle<u8>, Handle<u8>) {
        let owner = self.service.create(true, CursorOptions::default()).unwrap();
        owner
            .command(Command::Control(Control::Share { access }))
            .unwrap();
        let watcher = self.join(&owner);
        (owner, watcher)
    }

    pub(crate) fn join(&self, owner: &Handle<u8>) -> Handle<u8> {
        let link = link(owner);
        self.service.join(&link, CursorOptions::default()).unwrap()
    }

    /// Adds a video, opens its viewer and finishes resolving it.
    pub(crate) fn open_video(&self, owner: &Handle<u8>, name: &str) -> (ItemId, ContentRef) {
        let item = self.video(name);
        let id = item.id;
        owner
            .command(Command::Content(DocCommand::AddItems {
                items: vec![item],
                open: true,
            }))
            .unwrap();
        self.jobs.finish_all();
        (id, key(&owner.state(), id))
    }
}

pub(crate) fn link(owner: &Handle<u8>) -> sessions::JoinLink {
    owner.state().sharing.as_ref().unwrap().link.clone()
}

/// The key of the stream that carries `item`'s Content as it is resolved now.
pub(crate) fn key(state: &SessionState, item: ItemId) -> ContentRef {
    let Resolution::Resolved {
        content,
        generation,
    } = &state.doc.workspace.resolutions[&item]
    else {
        panic!("the item is not resolved");
    };
    ContentRef {
        content: content.id().clone(),
        generation: *generation,
    }
}

/// Asks `seat` for a ticket for `key` and redeems it.
pub(crate) fn stream(
    service: &Service<u8>,
    seat: &Handle<u8>,
    key: &ContentRef,
) -> Result<StreamLease, sessions::session::Refusal> {
    let Reply::Ticket(ticket) = seat.command(Command::RequestTicket(key.clone()))? else {
        panic!("a ticket request answers with a ticket");
    };
    service.redeem(&ticket)
}

pub(crate) fn authority(lease: &StreamLease) -> Authority {
    *lease.authority().borrow()
}
