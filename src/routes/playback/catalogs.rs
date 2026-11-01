//! The catalogs a viewer hears about: it asks for a catalog by id and is answered with its document, and from then on
//! it is sent the new document whenever that changes, and the new list whenever the list of catalogs changes. The
//! messages are text and ungated: catalogs are the app's resources and their ids carry no host path.

use super::{Outgoing, text};
use api_types::{catalogs::CatalogInfoWire, playback::ServerMessage};
use catalogs::{CatalogId, Library, Served};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use tokio::sync::watch;

/// How many distinct catalogs a viewer may ask for; the rest are not answered.
const MAX_ASKED: usize = 64;

/// What one viewer has been told of the catalogs.
pub(super) struct CatalogFeed {
    library: Library,
    changes: watch::Receiver<u64>,
    /// The list the viewer was last told.
    listing: Vec<CatalogInfoWire>,
    /// The catalogs the viewer asked for, each with the revision it was last answered with.
    asked: BTreeMap<String, u32>,
    /// The ids the viewer asked for and has not been answered.
    unanswered: BTreeSet<String>,
    /// The ids whose document changed since the viewer was answered, in the order to push them.
    stale: VecDeque<String>,
}

impl CatalogFeed {
    /// A feed that has told the viewer the list [`listing`](Self::listing) gives.
    pub(super) fn new(library: &Library) -> Self {
        // Subscribe before listing, so a change between the two is pushed rather than missed.
        let changes = library.changes();
        Self {
            listing: infos(library),
            library: library.clone(),
            changes,
            asked: BTreeMap::new(),
            unanswered: BTreeSet::new(),
            stale: VecDeque::new(),
        }
    }

    /// The catalogs a lane may draw with, as `Opened` tells them.
    pub(super) fn listing(&self) -> Vec<CatalogInfoWire> {
        self.listing.clone()
    }

    /// Queues the answer to a request for the catalog `id`, after which the viewer is sent every change of it. Asking
    /// again before the answer collapses; ids beyond [`MAX_ASKED`] are not answered.
    pub(super) fn ask(&mut self, id: String) {
        if self.asked.len() < MAX_ASKED || self.asked.contains_key(&id) {
            self.asked.entry(id.clone()).or_insert(0);
            self.unanswered.insert(id);
        }
    }

    /// The next thing to send: answers, then the list when it changed, then the catalogs that changed. Waits while
    /// there is none. Safe to drop while waiting.
    pub(super) async fn next(&mut self) -> Outgoing {
        loop {
            if let Some(id) = self.unanswered.pop_first() {
                return self.reply(id);
            }
            if let Some(id) = self.stale.pop_front() {
                return self.reply(id);
            }
            if self.changes.changed().await.is_err() {
                return std::future::pending().await;
            }
            let listing = infos(&self.library);
            let listed = (listing != self.listing).then(|| {
                self.listing = listing.clone();
                text(&ServerMessage::Catalogs { catalogs: listing }).into()
            });
            self.stale = self
                .asked
                .iter()
                .filter(|(id, revision)| served(&self.library, id).revision() != **revision)
                .map(|(id, _)| id.clone())
                .collect();
            if let Some(listed) = listed {
                return listed;
            }
        }
    }

    /// The message about the catalog `id`, which the viewer is then up to date with.
    fn reply(&mut self, id: String) -> Outgoing {
        let served = served(&self.library, &id);
        self.asked.insert(id.clone(), served.revision());
        let (revision, document, problem) = match served {
            Served::Document { revision, text } => (revision, Some(text.to_string()), None),
            Served::Unreadable { revision, problem } => (revision, None, Some(problem)),
            Served::Missing { revision } => (revision, None, None),
        };
        text(&ServerMessage::Catalog {
            id,
            revision,
            document,
            problem,
        })
        .into()
    }
}

/// What `id` names now; nothing for an id that names no catalog here.
fn served(library: &Library, id: &str) -> Served {
    CatalogId::parse(id).map_or(Served::Missing { revision: 0 }, |parsed| {
        library.document(&parsed)
    })
}

fn infos(library: &Library) -> Vec<CatalogInfoWire> {
    library
        .documents()
        .into_iter()
        .map(|info| CatalogInfoWire {
            id: info.id.to_string(),
            name: info.name,
            description: info.description,
        })
        .collect()
}
