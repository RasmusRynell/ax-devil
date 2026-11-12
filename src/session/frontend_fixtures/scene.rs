//! [`Scene`]: a session built only through the real reducers, with fixed ids so its Docs are byte-stable.

use super::outcomes::{Groups, Outcomes, mot_options, resolved};
use foundation::{BookmarkId, HostPath, ItemId};
use handler_api::ResolvedPlaylists;
use handlers::Handlers;
use sessions::{
    SessionId,
    session::{
        Command, Completion, Fresh, Input, Lease, SeatId, SeatKind, SessionState, Work, transition,
    },
};
use std::{collections::BTreeMap, path::PathBuf};
use workspace::store::{BookmarkCommand, DocCommand, HiddenRef, Saved};
use workspace::{
    BookmarkKind, ContentId, LiveOverlay, LiveRecipe, PlaylistRecipe, Protocol, Recipe, RtspRoute,
    Secret, Source, WorkspaceItem,
    intake::{NewOverlay, video},
};

/// The first Owner seat of `session`.
pub(super) fn owner_of(session: &SessionState) -> SeatId {
    session
        .seats
        .iter()
        .find(|(_, seat)| seat.kind == SeatKind::Owner)
        .map(|(id, _)| *id)
        .expect("a scene has an Owner seat")
}

/// Applies `command` to `session` as its owner and resolves the items it sends to be resolved with `outcomes`, as the
/// runner would. A refusal is a mistake in the scenario.
pub(super) fn run(session: &mut SessionState, outcomes: &Outcomes, command: Command) {
    let input = Input::Command {
        seat: owner_of(session),
        lease: Lease(0),
        command,
        fresh: Fresh::now(),
    };
    let (next, fx) = transition(session, input)
        .unwrap_or_else(|refusal| panic!("the scenario's command was refused: {refusal:?}"));
    *session = next;
    for spawn in fx.spawn {
        let Work::Resolve(job) = &spawn.work;
        let Some(outcome) = outcomes.answer(job) else {
            continue;
        };
        let input = Input::Complete {
            job: spawn.job,
            completion: Completion::Resolved {
                result: outcome,
                validated: Vec::new(),
            },
        };
        let (next, _) = transition(session, input)
            .unwrap_or_else(|refusal| panic!("a resolution was refused: {refusal:?}"));
        *session = next;
    }
}

/// A session under construction. Items are named by a key (`"mot"`, `"v"`); every key and every name inside an item
/// is registered in [`Scene::ids`] under the id the backend gives it: an item's key is its item id (a video's content
/// id too), `key/playlist` is a playlist's id and `key/entry` an entry's; only when another playlist of the item has
/// an entry of the same name is the entry `key/playlist/entry` instead.
#[derive(Clone)]
pub(super) struct Scene {
    session: SessionState,
    outcomes: Outcomes,
    items: BTreeMap<String, ItemId>,
    ids: BTreeMap<String, String>,
    bookmarks: u64,
}

impl Scene {
    /// An empty session: its owner holds the files grant and reads no environment.
    pub(super) fn new() -> Self {
        let mut session = SessionState::new(SessionId::generate(), false);
        session.seat_owner();
        Self {
            session,
            outcomes: Outcomes::default(),
            items: BTreeMap::new(),
            ids: BTreeMap::new(),
            bookmarks: 0,
        }
    }

    /// The session as built so far. Clone it to take a step from this state.
    pub(super) fn session(&self) -> &SessionState {
        &self.session
    }

    /// What each key names, as the text that crosses the wire.
    pub(super) fn ids(&self) -> &BTreeMap<String, String> {
        &self.ids
    }

    /// What each item resolves to, for [`run`].
    pub(super) fn outcomes(&self) -> &Outcomes {
        &self.outcomes
    }

    /// Applies any content command as the owner, resolving what it sends to be resolved.
    pub(super) fn apply(&mut self, command: DocCommand) {
        run(&mut self.session, &self.outcomes, Command::Content(command));
    }

    /// A video `file` of item `key`, with one lane for each of `lanes`, made as the add dialog's route makes it.
    pub(super) fn video(&mut self, key: &str, file: &str, lanes: &[&str]) -> ItemId {
        let item = self.video_item(key, file, lanes);
        let id = item.id;
        self.add(item);
        id
    }

    fn video_item(&mut self, key: &str, file: &str, lanes: &[&str]) -> WorkspaceItem {
        let id = self.register(key);
        let overlays: Vec<NewOverlay> = lanes
            .iter()
            .map(|lane| NewOverlay {
                path: PathBuf::from(format!("/data/{key}/{lane}.txt")),
                handler: Some("MOT_FILE".into()),
                label: Some((*lane).into()),
                options: mot_options(),
            })
            .collect();
        let path = PathBuf::from(format!("/data/{key}/{file}"));
        let mut item = video(&path, &overlays, &Handlers::built_in())
            .unwrap_or_else(|refusal| panic!("the scenario's video was refused: {refusal:?}"));
        item.id = id;
        item
    }

    /// A playlist item `key`, named `key`, of playlists named by the first of each group, each with the entries
    /// named by the first of each pair and the lanes of each entry.
    pub(super) fn playlist(&mut self, key: &str, groups: Groups) -> ItemId {
        self.playlist_with(key, key, groups, |_| {})
    }

    /// A playlist as [`Scene::playlist`] adds it, read by the resolver `name`, with `describe` having the last word
    /// on what that resolver returns. Resolving it is the backend's: what it refuses leaves the item failed.
    pub(super) fn playlist_with(
        &mut self,
        key: &str,
        name: &str,
        groups: Groups,
        describe: impl FnOnce(&mut ResolvedPlaylists),
    ) -> ItemId {
        let id = self.register(key);
        self.serve(id, key, name, groups, describe);
        let item = WorkspaceItem {
            id,
            label: key.into(),
            recipe: Recipe::new(Source::Playlist(PlaylistRecipe {
                resolver: key.into(),
                settings: Default::default(),
            })),
        };
        self.add(item);
        id
    }

    /// What playlist item `key` resolves to from now on, read again: its files changed. Bookmarks of an entry it no
    /// longer has stay, as in production.
    pub(super) fn reread_playlist(&mut self, key: &str, groups: Groups) {
        let id = self.item(key);
        self.serve(id, key, key, groups, |_| {});
        self.resolve(key);
    }

    /// Has the resolver `key` return `groups`, and names what they hold as the backend ids them.
    fn serve(
        &mut self,
        item: ItemId,
        key: &str,
        name: &str,
        groups: Groups,
        describe: impl FnOnce(&mut ResolvedPlaylists),
    ) {
        let mut resolved = resolved(key, groups);
        describe(&mut resolved);
        self.outcomes.serve(key, name, resolved);
        for (playlist, members) in groups {
            self.name(
                format!("{key}/{playlist}"),
                ContentId::of_playlist(&item, playlist),
            );
            for (entry, _) in *members {
                let id = ContentId::of_entry(&item, playlist, entry);
                let named = groups.iter().flat_map(|(_, members)| *members);
                let alias = if named.filter(|(other, _)| other == entry).count() == 1 {
                    format!("{key}/{entry}")
                } else {
                    format!("{key}/{playlist}/{entry}")
                };
                self.name(alias, id);
            }
        }
    }

    /// A live stream of `host`, named `label` unless it has none.
    pub(super) fn live(&mut self, key: &str, label: Option<&str>, host: &str) -> ItemId {
        self.live_with(key, label, host, |_| {})
    }

    /// A live stream as [`Scene::live`] adds it, with `tweak` having the last word on its recipe. A recipe that does
    /// not resolve leaves the item failed, with the backend's reason.
    pub(super) fn live_with(
        &mut self,
        key: &str,
        label: Option<&str>,
        host: &str,
        tweak: impl FnOnce(&mut LiveRecipe),
    ) -> ItemId {
        let id = self.register(key);
        let mut live = LiveRecipe {
            host: host.into(),
            username: String::new(),
            password: Secret::new(""),
            protocol: Protocol::Https,
            port: 0,
            head: 1,
            rtsp_port: 554,
            rtsp_route: RtspRoute::Direct,
            resolution: None,
            url: None,
            overlay: LiveOverlay::None,
            kept: Default::default(),
        };
        tweak(&mut live);
        let recipe = Recipe::new(Source::Live(live));
        let label = label.unwrap_or_default();
        let item = WorkspaceItem {
            id,
            label: label.into(),
            recipe,
        };
        self.add(item);
        id
    }

    /// Reads item `key` again, as `resolve` does, but the new reading is still on its way.
    pub(super) fn resolving(&mut self, key: &str) {
        let id = self.item(key);
        self.outcomes.hold(id);
        self.resolve(key);
    }

    /// A video `file` of item `key` whose file is missing, so it fails as production's check does.
    pub(super) fn failed(&mut self, key: &str, file: &str) -> ItemId {
        let item = self.video_item(key, file, &[]);
        let id = item.id;
        self.lose(&item);
        self.add(item);
        id
    }

    /// The item `raw` of a workspace file, as reading that file leaves it: an item of a kind this build does not
    /// know stays unreadable, and a host loses its surrounding white space. `raw` gets the item's fixed id.
    pub(super) fn loaded(&mut self, key: &str, mut raw: serde_json::Value) -> ItemId {
        let id = self.register(key);
        raw["id"] = id.to_string().into();
        let document = serde_json::json!({ "version": 2, "name": null, "items": [raw] });
        let workspace = workspace_files::from_json(&document)
            .unwrap_or_else(|reason| panic!("the scenario's item was refused: {reason}"));
        let [item] = <[WorkspaceItem; 1]>::try_from(workspace.items)
            .unwrap_or_else(|_| panic!("one item was read"));
        self.add(item);
        id
    }

    /// A video `file` of item `key` that is still resolving.
    pub(super) fn pending(&mut self, key: &str, file: &str) -> ItemId {
        let item = self.video_item(key, file, &[]);
        let id = item.id;
        self.outcomes.hold(id);
        self.add(item);
        id
    }

    /// Another Owner seat publishes a view through the same transition as its window.
    pub(super) fn present(&mut self, view: sessions::session::View) {
        let seat = self.session.seat_owner();
        let (next, _) = transition(
            &self.session,
            Input::View {
                seat,
                lease: Lease(0),
                view,
                seq: 1,
                now_us: 0,
            },
        )
        .expect("the scenario's view is accepted");
        self.session = next;
    }

    /// Changes the Session through the first Owner seat's command lane.
    pub(super) fn command(&mut self, command: Command) {
        run(&mut self.session, &self.outcomes, command);
    }

    /// Requests the viewer of item `key`, as a client does.
    pub(super) fn open(&mut self, key: &str) {
        self.apply(DocCommand::OpenViewer(self.item(key)));
    }

    /// Withdraws the viewer of item `key`.
    pub(super) fn close(&mut self, key: &str) {
        self.apply(DocCommand::CloseViewer(self.item(key)));
    }

    /// Reads item `key` again, as the reload offer does: an open viewer keeps its Content until the new one is read.
    pub(super) fn resolve(&mut self, key: &str) {
        self.apply(DocCommand::Resolve(self.item(key)));
    }

    /// The video of item `key` is gone: every later resolution of it fails.
    pub(super) fn fail_resolving(&mut self, key: &str) {
        let id = self.item(key);
        let item = &self.session.doc.workspace.workspace.items;
        let found = item.iter().find(|item| item.id == id).cloned();
        self.lose(&found.unwrap_or_else(|| panic!("no item {key}")));
    }

    /// The files item `key` was read from changed.
    pub(super) fn source_changed(&mut self, key: &str) {
        self.apply(DocCommand::SourceChanged(self.item(key)));
    }

    /// Takes item `key` out of the workspace.
    pub(super) fn remove(&mut self, key: &str) {
        self.apply(DocCommand::Remove(self.item(key)));
    }

    /// Names item `key` as the user does; blank restores its default name.
    pub(super) fn rename(&mut self, key: &str, label: &str) {
        let item = self.item(key);
        self.apply(DocCommand::RenameItem {
            item,
            label: label.into(),
        });
    }

    /// Hides the entry `key` names (`"mot/A"`), or its lane `lane`.
    pub(super) fn hide(&mut self, key: &str, lane: Option<&str>) {
        let entry = self.content_id(key);
        let target = match lane {
            Some(lane) => HiddenRef::Lane(entry, lane.to_owned()),
            None => HiddenRef::Entry(entry),
        };
        self.apply(DocCommand::SetHidden {
            target,
            hidden: true,
        });
    }

    /// Bookmarks `frame` of the video `key` or of the playlist entry `key` names (`"mot/A"`), as a client does, with
    /// a fixed id, then gives it `kind` and `note`.
    pub(super) fn bookmark(&mut self, key: &str, frame: u64, kind: BookmarkKind, note: &str) {
        self.bookmarks += 1;
        let id = BookmarkId::parse(&format!("00000000-0000-4000-9000-{:012x}", self.bookmarks))
            .expect("a fixed bookmark id parses");
        let time = foundation::MediaTime::from_ticks(frame as i64, 1, 25)
            .expect("a frame time is representable");
        let item = self.item(key.split('/').next().unwrap_or(key));
        let entry = self.content_id(key);
        self.apply(DocCommand::Bookmarks(BookmarkCommand::Add {
            id,
            item,
            entry,
            frame,
            time,
        }));
        self.apply(DocCommand::Bookmarks(BookmarkCommand::Edit {
            id,
            note: Some(note.into()),
            kind: Some(kind),
        }));
    }

    /// Replaces the workspace with a copy of itself read from a file, as Open does: one epoch later, every viewer
    /// closed and every item read again.
    pub(super) fn reopen(&mut self) {
        let workspace = self.session.doc.workspace.workspace.clone();
        self.apply(DocCommand::Replace {
            workspace: workspace.clone(),
            file: Some(HostPath::new("/data/saved.ax-devil.workspace")),
            saved: Saved::As(workspace),
        });
    }

    /// Replaces the workspace with a copy of itself restored from a recovery copy, with Keep and Discard on offer.
    /// Startup does this through `Files::restore`, which also claims the copy file, so the reducer input is built
    /// here.
    pub(super) fn restore(&mut self) {
        let workspace = self.session.doc.workspace.workspace.clone();
        self.apply(DocCommand::Replace {
            workspace,
            file: None,
            saved: Saved::Recovered { offer: true },
        });
    }

    /// Takes away the file of video `item`.
    fn lose(&mut self, item: &WorkspaceItem) {
        let Source::Video(video) = &item.recipe.source else {
            panic!("only a video's file goes missing");
        };
        self.outcomes.missing(item.id, video.video.as_path());
    }

    /// The next item id, registered as `key`.
    fn register(&mut self, key: &str) -> ItemId {
        let id = ItemId::parse(&format!(
            "00000000-0000-4000-8000-{:012x}",
            self.items.len() + 1
        ))
        .expect("a fixed item id parses");
        assert!(
            self.items.insert(key.to_owned(), id).is_none(),
            "{key} is added twice"
        );
        self.ids.insert(key.to_owned(), id.to_string());
        id
    }

    /// Names `id` as `key`; naming it again as the same thing is fine.
    fn name(&mut self, key: String, id: ContentId) {
        let previous = self.ids.insert(key.clone(), id.to_string());
        assert!(
            previous.is_none_or(|was| was == id.to_string()),
            "{key} names two things"
        );
    }

    fn content_id(&self, key: &str) -> ContentId {
        serde_json::from_value(serde_json::Value::String(self.id(key)))
            .expect("a registered id is a content id")
    }

    fn id(&self, key: &str) -> String {
        self.ids
            .get(key)
            .unwrap_or_else(|| panic!("nothing is named {key}"))
            .clone()
    }

    fn item(&self, key: &str) -> ItemId {
        *self
            .items
            .get(key)
            .unwrap_or_else(|| panic!("no item {key}"))
    }

    /// Adds `item` through the reducer, resolving it as the outcomes say.
    fn add(&mut self, item: WorkspaceItem) {
        self.apply(DocCommand::AddItems {
            items: vec![item],
            open: false,
        });
    }
}
