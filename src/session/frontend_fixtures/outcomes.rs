//! What the items of a [`super::Scene`] resolve to.

use foundation::{CancelToken, Interrupt, ItemId, UserError};
use handler_api::{
    OverlayRef, PlaylistResolver, ResolvedEntry, ResolvedPlaylist, ResolvedPlaylists, ResolverSpec,
    Settings, Values, VideoRef,
};
use handlers::Handlers;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    sync::Arc,
};
use workspace::store::ResolveJob;
use workspace::{Content, Environment, Source};

/// The playlists of a playlist item: each a name and its entries, each an entry name and its lanes.
pub(super) type Groups<'a> = &'a [(&'a str, &'a [(&'a str, &'a [&'a str])])];

/// What each item resolves to: the backend's own resolution of the recipe the job carries, in a fixed environment. A
/// playlist's resolver returns what the scene gave it, and a video's files are taken to be readable. An item can be
/// held pending, or have its files missing, which production's own file check reports.
#[derive(Clone, Default)]
pub(super) struct Outcomes {
    missing: BTreeSet<ItemId>,
    held: BTreeSet<ItemId>,
    resolvers: BTreeMap<String, Arc<Canned>>,
}

impl Outcomes {
    /// The files of video item `item` are missing from now on, so resolving it fails as production's does. Fixed
    /// paths are under `/data`, which the scenario relies on not existing.
    pub(super) fn missing(&mut self, item: ItemId, video: &Path) {
        assert!(
            !video.exists(),
            "{} exists, so the scenario's missing file is not missing; the fixtures need a host without /data",
            video.display()
        );
        self.missing.insert(item);
    }

    /// Keeps `item` pending from now on.
    pub(super) fn hold(&mut self, item: ItemId) {
        self.held.insert(item);
    }

    /// The playlists the resolver named `key`, named `name`, returns from now on.
    pub(super) fn serve(&mut self, key: &str, name: &str, playlists: ResolvedPlaylists) {
        let spec = ResolverSpec {
            id: key.into(),
            plugin: "fixture".into(),
            name: name.into(),
            description: String::new(),
            form: Default::default(),
        };
        self.resolvers
            .insert(key.into(), Arc::new(Canned { spec, playlists }));
    }

    /// How `job` ends, or `None` while it stays pending.
    pub(super) fn answer(&self, job: &ResolveJob) -> Option<Result<Content, UserError>> {
        if self.held.contains(&job.item) {
            return None;
        }
        let cancel = CancelToken::new();
        let end = |outcome: Result<Content, Interrupt<UserError>>| {
            Some(outcome.map_err(|interrupt| match interrupt {
                Interrupt::Failed(error) => error,
                Interrupt::Cancelled => panic!("nothing cancels the scenario"),
            }))
        };
        match &job.recipe.source {
            Source::Video(video) if !self.missing.contains(&job.item) => {
                end(video.content(&job.item, &Handlers::built_in(), &cancel))
            }
            // A video whose files are missing fails in the real file check, like everything else.
            _ => {
                let resolvers = self
                    .resolvers
                    .values()
                    .map(|resolver| Arc::clone(resolver) as Arc<dyn PlaylistResolver>);
                let handlers = Handlers::built_in().with_resolvers(resolvers);
                end(job.recipe.resolve(
                    &job.item,
                    &job.label,
                    &handlers,
                    &Environment::literal(),
                    &cancel,
                ))
            }
        }
    }
}

/// A resolver that returns what it was given, as a plugin does.
struct Canned {
    spec: ResolverSpec,
    playlists: ResolvedPlaylists,
}

impl PlaylistResolver for Canned {
    fn spec(&self) -> &ResolverSpec {
        &self.spec
    }

    fn resolve(
        &self,
        _settings: &Values,
        _cancel: &CancelToken,
    ) -> Result<ResolvedPlaylists, Interrupt<UserError>> {
        Ok(self.playlists.clone())
    }
}

/// What a resolver returns for `groups` of item `key`.
pub(super) fn resolved(key: &str, groups: Groups) -> ResolvedPlaylists {
    let playlists = groups
        .iter()
        .map(|(name, members)| ResolvedPlaylist {
            name: (*name).into(),
            entries: members
                .iter()
                .map(|(entry, lanes)| ResolvedEntry {
                    name: (*entry).into(),
                    video: VideoRef::File(PathBuf::from(format!("/data/{key}/{entry}"))),
                    overlays: overlays(key, lanes),
                    starts_hidden: false,
                    lanes_starting_hidden: Vec::new(),
                    metadata: Default::default(),
                })
                .collect(),
            matches: None,
            sequence_groups: None,
            metadata: Default::default(),
        })
        .collect();
    ResolvedPlaylists {
        playlists,
        warnings: Vec::new(),
    }
}

/// A lane for each of `lanes`, of the files of item `key`.
pub(super) fn overlays(key: &str, lanes: &[&str]) -> Vec<OverlayRef> {
    lanes
        .iter()
        .map(|lane| OverlayRef {
            label: (*lane).into(),
            slot: (*lane).into(),
            source: workspace::LaneSource::File(PathBuf::from(format!("/data/{key}/{lane}.txt"))),
            handler: "MOT_FILE".into(),
            options: mot_options(),
            metadata: Default::default(),
        })
        .collect()
}

/// The options of every `MOT_FILE` lane: the image size the real MOT resolver reads from `seqinfo.ini`.
pub(super) fn mot_options() -> Settings {
    let mut options = Settings::new();
    options.insert("width".into(), 64.into());
    options.insert("height".into(), 48.into());
    options
}
