//! The Session's rules: who may do what, what makes old authority die for good, and what a session of one does.
//!
//! `matrix` checks the role table on the pure transition; `revocation` and `streams` run real handles on paused time.

mod matrix;
mod owner_seats;
mod retirement;
mod revocation;
mod roster;
mod runner;
mod sources;
mod streams;
mod viewport;

use foundation::{CancelToken, Interrupt, UserError};
use handlers::Handlers;
use playback::Authority;
use sessions::{
    CursorOptions,
    session::{
        Access, Command, Completer, Completion, ContentRef, Control, Handle, Layout, Reply, Runner,
        Service, SessionState, Spawn, StreamLease, View, ViewerId, ViewerView, Work,
    },
};
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use tempfile::TempDir;
use workspace::store::{DocCommand, Resolution, ResolveJob};
use workspace::{Content, ItemId, WorkspaceItem};

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
            done.complete(Completion::Resolved(resolve(job)));
        }
    }
}

/// Resolves `job` with the built-in handlers, as the real runner does.
pub(crate) fn resolve(job: &ResolveJob) -> Result<Content, UserError> {
    match workspace::store::resolve(
        job,
        &Handlers::built_in(),
        &workspace::Environment::of_process(),
        &CancelToken::new(),
    ) {
        Ok(content) => Ok(content),
        Err(Interrupt::Failed(error)) => Err(error),
        Err(Interrupt::Cancelled) => panic!("nothing cancels a test job"),
    }
}

/// The item for a video file `name` in `dir`, created.
pub(crate) fn video_in(dir: &std::path::Path, name: &str) -> WorkspaceItem {
    let path = dir.join(name);
    std::fs::write(&path, "x").unwrap();
    workspace::intake::video(&path, &[], &Handlers::built_in()).unwrap()
}

/// The item for a folder-pair playlist in `dir` with one entry per name, in name order.
pub(crate) fn playlist_in(dir: &std::path::Path, names: &[&str]) -> WorkspaceItem {
    let (videos, overlays) = (dir.join("videos"), dir.join("overlays"));
    for folder in [&videos, &overlays] {
        std::fs::create_dir_all(folder).unwrap();
    }
    for name in names {
        std::fs::write(videos.join(format!("{name}.mp4")), "x").unwrap();
        std::fs::write(overlays.join(format!("{name}.txt")), "").unwrap();
    }
    let mut settings = serde_json::Map::new();
    settings.insert("videos_dir".into(), videos.display().to_string().into());
    settings.insert("overlays_dir".into(), overlays.display().to_string().into());
    settings.insert("handler_type".into(), "MOT_FILE".into());
    settings.insert(
        "options".into(),
        serde_json::json!({"width": 64, "height": 48}),
    );
    workspace::intake::playlist("folder_pair", settings, &Handlers::built_in()).unwrap()
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

    pub(crate) fn video(&self, name: &str) -> WorkspaceItem {
        video_in(self.dir.path(), name)
    }

    /// An owner that shares with `access`, and a guest that joined.
    pub(crate) fn shared(&self, access: Access) -> (Handle<u8>, Handle<u8>) {
        let owner = self
            .service
            .create(true, CursorOptions::default(), Default::default())
            .unwrap();
        owner
            .command(Command::Control(Control::Share { access }))
            .unwrap();
        let guest = self.join(&owner);
        (owner, guest)
    }

    pub(crate) fn join(&self, owner: &Handle<u8>) -> Handle<u8> {
        let link = link(owner);
        self.service.join(&link, CursorOptions::default()).unwrap()
    }

    /// Adds a video, opens its viewer and finishes resolving it, with the owner showing it in viewer 1.
    pub(crate) fn open_video(&self, owner: &Handle<u8>, name: &str) -> (ItemId, ContentRef) {
        self.open(owner, self.video(name))
    }

    /// Adds `item`, opens its viewer and finishes resolving it, with the owner showing it in viewer 1 (its view number
    /// 1); the key names viewer 1 and the first entry.
    pub(crate) fn open(&self, owner: &Handle<u8>, item: WorkspaceItem) -> (ItemId, ContentRef) {
        let id = item.id;
        owner
            .command(Command::Content(DocCommand::AddItems {
                items: vec![item],
                open: true,
            }))
            .unwrap();
        self.jobs.finish_all();
        owner.view(showing(id), 1).unwrap();
        (id, key(&owner.state(), id))
    }
}

pub(crate) fn link(owner: &Handle<u8>) -> sessions::JoinLink {
    owner.state().sharing.as_ref().unwrap().link.clone()
}

/// A viewer that shows `item` as it first opens: not pinned, no clock, no chosen entry.
pub(crate) fn looking(item: ItemId) -> ViewerView {
    ViewerView {
        item,
        pinned: false,
        clock: None,
        entry: None,
        viewport: None,
    }
}

/// The layout that is one pane, for `viewer`.
pub(crate) fn pane(viewer: u32) -> Box<Layout> {
    Box::new(Layout::Pane {
        viewer: ViewerId(viewer),
    })
}

/// The view of a window whose one pane, viewer 1, shows `item`.
pub(crate) fn showing(item: ItemId) -> View {
    View {
        active: Some(ViewerId(1)),
        viewers: [(ViewerId(1), looking(item))].into(),
        layout: Some(*pane(1)),
        ..View::default()
    }
}

/// The key of the stream viewer 1 plays: the first entry of `item`'s Content as it is resolved now.
pub(crate) fn key(state: &SessionState, item: ItemId) -> ContentRef {
    entry_key(state, item, 0)
}

/// The key of the stream viewer 1 plays: entry `index` of `item`'s Content as it is resolved now.
pub(crate) fn entry_key(state: &SessionState, item: ItemId, index: usize) -> ContentRef {
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
        entry: content.entries()[index].id.clone(),
        viewer: ViewerId(1),
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
