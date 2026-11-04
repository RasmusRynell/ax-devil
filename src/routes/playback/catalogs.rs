//! The catalogs a viewer hears about: it asks for a catalog by id and is answered with its document, and from then on
//! it is sent the new document whenever that changes, and the new list whenever the list of catalogs changes. The
//! messages are text and ungated: catalogs are the app's resources and their ids carry no host path.

use super::{Outgoing, text};
use api_types::{catalogs::CatalogInfoWire, playback::ServerMessage};
use catalogs::{CatalogId, Library, Served};
use std::{
    collections::{BTreeSet, VecDeque},
    sync::Arc,
};
use tokio::sync::watch;

/// How many distinct catalogs a viewer is kept up to date with; asking for one more drops the one asked for longest ago.
const MAX_ASKED: usize = 64;

/// How many documents a viewer was served are kept for an export to draw with. Past it a document a newer one of the
/// same catalog replaced is dropped first, the oldest of those; a catalog's newest document goes only when none is left.
const MAX_KEPT: usize = 16;

/// What one viewer has been told of the catalogs.
pub(super) struct CatalogFeed {
    library: Library,
    changes: watch::Receiver<u64>,
    /// The list the viewer was last told.
    listing: Vec<CatalogInfoWire>,
    /// The catalogs the viewer asked for, the one asked for longest ago first, each with the revision it was last
    /// answered with.
    asked: Vec<(String, u32)>,
    /// The ids the viewer asked for and has not been answered.
    unanswered: BTreeSet<String>,
    /// The ids whose document changed since the viewer was answered, in the order to push them.
    stale: VecDeque<String>,
    /// The newest documents served, oldest first: an export draws with exactly the text its viewer holds.
    kept: VecDeque<(String, u32, Arc<str>)>,
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
            asked: Vec::new(),
            unanswered: BTreeSet::new(),
            stale: VecDeque::new(),
            kept: VecDeque::new(),
        }
    }

    /// The document of catalog `id` at `revision` as this viewer was served it, if it still keeps it.
    pub(super) fn kept(&self, id: &str, revision: u32) -> Option<Arc<str>> {
        self.kept
            .iter()
            .find(|(kept, at, _)| kept == id && *at == revision)
            .map(|(_, _, text)| Arc::clone(text))
    }

    /// The catalogs a lane may draw with, as `Opened` tells them.
    pub(super) fn listing(&self) -> Vec<CatalogInfoWire> {
        self.listing.clone()
    }

    /// Queues the answer to a request for the catalog `id`, after which the viewer is sent every change of it. Asking
    /// again before the answer collapses. Past [`MAX_ASKED`] catalogs the one asked for longest ago is no longer
    /// followed, so the catalogs in use keep updating.
    pub(super) fn ask(&mut self, id: String) {
        let revision = match self.asked.iter().position(|(asked, _)| *asked == id) {
            Some(at) => self.asked.remove(at).1,
            None => 0,
        };
        if self.asked.len() == MAX_ASKED {
            let (dropped, _) = self.asked.remove(0);
            self.unanswered.remove(&dropped);
            self.stale.retain(|stale| *stale != dropped);
        }
        self.asked.push((id.clone(), revision));
        self.unanswered.insert(id);
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
                .filter(|(id, revision)| served(&self.library, id).revision() != *revision)
                .map(|(id, _)| id.clone())
                .collect();
            if let Some(listed) = listed {
                return listed;
            }
        }
    }

    /// Keeps the document of `id` at `revision`, making room by dropping, in this order, the oldest document that a
    /// newer one of its catalog replaced, else the oldest. The viewer draws each catalog with the newest document it
    /// was served, so reloading one catalog must not push out the others.
    fn keep(&mut self, id: &str, revision: u32, text: &Arc<str>) {
        if self.kept(id, revision).is_some() {
            return;
        }
        if self.kept.len() == MAX_KEPT {
            let replaced = (0..self.kept.len()).find(|&at| {
                self.kept
                    .iter()
                    .skip(at + 1)
                    .any(|(newer, _, _)| *newer == self.kept[at].0)
            });
            match replaced {
                Some(at) => self.kept.remove(at),
                None => self.kept.pop_front(),
            };
        }
        self.kept
            .push_back((id.to_owned(), revision, Arc::clone(text)));
    }

    /// The message about the catalog `id`, which the viewer is then up to date with.
    fn reply(&mut self, id: String) -> Outgoing {
        let served = served(&self.library, &id);
        if let Some((_, revision)) = self.asked.iter_mut().find(|(asked, _)| *asked == id) {
            *revision = served.revision();
        }
        let (revision, document, problem) = match served {
            Served::Document { revision, text } => {
                self.keep(&id, revision, &text);
                (revision, Some(text.to_string()), None)
            }
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
            problem: info.problem,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::extract::ws::Message;

    /// The id of the catalog the next message answers.
    async fn answered(feed: &mut CatalogFeed) -> String {
        let Message::Text(message) = feed.next().await.message else {
            panic!("a catalog is a text message");
        };
        let ServerMessage::Catalog { id, .. } = serde_json::from_str(message.as_str()).unwrap()
        else {
            panic!("expected a catalog");
        };
        id
    }

    #[tokio::test]
    async fn asking_for_a_65th_catalog_is_answered_and_drops_the_one_asked_for_longest_ago() {
        let mut feed = CatalogFeed::new(&Library::built_in());
        for number in 0..MAX_ASKED {
            feed.ask(format!("user:{number}.json"));
            answered(&mut feed).await;
        }
        feed.ask("user:0.json".into());
        answered(&mut feed).await;

        feed.ask("user:new.json".into());

        assert_eq!(answered(&mut feed).await, "user:new.json");
        let held: Vec<&str> = feed.asked.iter().map(|(id, _)| id.as_str()).collect();
        assert_eq!(held.len(), MAX_ASKED);
        assert!(held.contains(&"user:0.json") && held.contains(&"user:new.json"));
        assert!(!held.contains(&"user:1.json"));
    }

    #[test]
    fn reloading_one_catalog_many_times_keeps_the_others_documents_for_export() {
        let mut feed = CatalogFeed::new(&Library::built_in());
        let document: Arc<str> = "{}".into();
        feed.keep("user:a.json", 1, &document);
        for revision in 1..=(2 * MAX_KEPT as u32) {
            feed.keep("user:b.json", revision, &document);
        }

        assert!(feed.kept("user:a.json", 1).is_some());
        assert!(feed.kept("user:b.json", 2 * MAX_KEPT as u32).is_some());
        assert_eq!(feed.kept.len(), MAX_KEPT);
    }
}
