//! The catalogs a viewer hears about: it asks for a catalog by id and is answered with its document, and from then on
//! it is sent the new document whenever that changes, and the new list whenever the list of catalogs changes. The
//! messages are text. The built-ins go to everyone. A user catalog is the host's file: the host's own viewers ask for
//! the ones their lanes use, and a Guest is shown a user catalog only while a host viewer of its own Session has
//! asked for it. The host's choice per lane is client state, so "asked for" stands for "draws with".

use super::{Outgoing, text};
use api_types::{Audience, catalogs::CatalogInfoWire, playback::ServerMessage};
use catalogs::{CatalogId, Lend, Library, Served};
use sessions::SessionId;
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
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
    /// The Session of a Guest, who is shown only the catalogs that Session's host lanes use; `None` for a host.
    guest: Option<String>,
    /// The Session of a host viewer, whose Guests are shown the user catalogs it asked for while they are held.
    session: String,
    lends: BTreeMap<String, Lend>,
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
    pub(super) fn new(library: &Library, audience: Audience, session: SessionId) -> Self {
        // Subscribe before listing, so a change between the two is pushed rather than missed.
        let changes = library.changes();
        let session = session.to_string();
        let guest = (!audience.host_details).then(|| session.clone());
        Self {
            listing: infos(library, guest.as_deref()),
            library: library.clone(),
            guest,
            session,
            lends: BTreeMap::new(),
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
            self.lends.remove(&dropped);
            self.unanswered.remove(&dropped);
            self.stale.retain(|stale| *stale != dropped);
        }
        if self.guest.is_none()
            && let Some(parsed) = CatalogId::parse(&id)
        {
            self.lends
                .entry(id.clone())
                .or_insert_with(|| self.library.lend(&self.session, &parsed));
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
            let listing = infos(&self.library, self.guest.as_deref());
            let listed = (listing != self.listing).then(|| {
                self.listing = listing.clone();
                text(&ServerMessage::Catalogs { catalogs: listing }).into()
            });
            self.stale = self
                .asked
                .iter()
                .filter(|(id, revision)| {
                    served(&self.library, id, self.guest.as_deref()).revision() != *revision
                })
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
        let served = served(&self.library, &id, self.guest.as_deref());
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

/// What `id` names now; nothing for an id that names no catalog here, or, to a Guest, no catalog of the host's lanes.
fn served(library: &Library, id: &str, guest: Option<&str>) -> Served {
    CatalogId::parse(id).map_or(Served::Missing { revision: 0 }, |parsed| match guest {
        Some(session) => library.shared_document(session, &parsed),
        None => library.document(&parsed),
    })
}

fn infos(library: &Library, guest: Option<&str>) -> Vec<CatalogInfoWire> {
    let documents = match guest {
        Some(session) => library.shared_documents(session),
        None => library.documents(),
    };
    documents
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

    #[test]
    fn reloading_one_catalog_many_times_keeps_the_others_documents_for_export() {
        let mut feed =
            CatalogFeed::new(&Library::built_in(), Audience::HOST, SessionId::generate());
        let document: Arc<str> = "{}".into();
        feed.keep("user:a.json", 1, &document);
        for revision in 1..=(2 * MAX_KEPT as u32) {
            feed.keep("user:b.json", revision, &document);
        }

        assert!(feed.kept("user:a.json", 1).is_some());
        assert!(feed.kept("user:b.json", 2 * MAX_KEPT as u32).is_some());
        assert_eq!(feed.kept.len(), 16);
    }

    /// Records real feed replies, including the revision-zero withdrawal, for the frontend's ordering regression.
    #[tokio::test]
    async fn last_owner_lend_withdraws_and_restores_the_catalog_frontend_fixture() {
        use std::{fs, path::Path};
        let folder = tempfile::tempdir().unwrap();
        let document =
            r#"{"schema":4,"name":"Shared","description":"","vars":{},"rules":[],"layers":[]}"#;
        fs::write(folder.path().join("shared.json"), document).unwrap();
        // Load the selected document first: directory iteration order must not choose its recorded revision.
        let library = Library::with_folder(folder.path().to_path_buf());
        for number in 0..64 {
            fs::write(folder.path().join(format!("{number}.json")), document).unwrap();
            library.reread(&CatalogId::User(format!("{number}.json")));
        }
        let session = SessionId::generate();
        let mut first = CatalogFeed::new(&library, Audience::HOST, session);
        let mut second = CatalogFeed::new(&library, Audience::HOST, session);
        let mut guest = CatalogFeed::new(
            &library,
            Audience {
                host_details: false,
            },
            session,
        );
        let id = "user:shared.json";
        first.ask(id.into());
        second.ask(id.into());
        guest.ask(id.into());
        let initial = next_catalog(&mut guest).await;
        // Each Owner seat has its own feed; exhausting one cannot withdraw the other seat's lend.
        for number in 0..63 {
            first.ask(format!("user:{number}.json"));
        }
        // The original plus 63 distinct catalogs fit; the 65th subscription evicts the oldest lend.
        guest.ask(id.into());
        assert_eq!(next_catalog(&mut guest).await, initial);
        first.ask("user:63.json".into());
        guest.ask(id.into());
        let one_owner = next_catalog(&mut guest).await;
        assert_eq!(initial, one_owner);
        for number in 0..63 {
            second.ask(format!("user:{number}.json"));
        }
        guest.ask(id.into());
        assert_eq!(
            next_catalog(&mut guest).await,
            initial,
            "64 subscriptions retain the original"
        );
        second.ask("user:63.json".into());
        assert!(matches!(
            library.shared_document(&session.to_string(), &CatalogId::User("shared.json".into())),
            Served::Missing { revision: 0 }
        ));
        // Only the oldest lend was released: all 64 more recently requested catalogs remain shared.
        for number in 0..64 {
            assert!(matches!(
                library.shared_document(
                    &session.to_string(),
                    &CatalogId::User(format!("{number}.json"))
                ),
                Served::Document { .. }
            ));
        }
        let withdrawn = next_catalog(&mut guest).await;
        assert_eq!(withdrawn["revision"], 0);
        assert!(withdrawn["document"].is_null());
        // Re-lending unchanged text restores the same document revision, not a fabricated higher one.
        second.ask(id.into());
        let restored = next_catalog(&mut guest).await;
        assert_eq!(initial, restored);
        let mut recorded = serde_json::json!({"initial": initial, "oneOwner": one_owner,
            "withdrawn": withdrawn, "restored": restored});
        // Library revisions use a process-wide clock shared with other tests. Canonicalize only its offset;
        // preserve zero withdrawals and every relative revision from the real feed.
        let offset = recorded["initial"]["revision"].as_u64().unwrap() - 1;
        for reply in recorded.as_object_mut().unwrap().values_mut() {
            let revision = reply["revision"].as_u64().unwrap();
            if revision > 0 {
                reply["revision"] = (revision - offset).into();
            }
        }
        crate::test_support::fixtures::check(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures"),
            "catalogs",
            &std::collections::BTreeMap::from([(
                "lend-feed.json".into(),
                format!("{}\n", serde_json::to_string_pretty(&recorded).unwrap()),
            )]),
        );
    }

    async fn next_catalog(feed: &mut CatalogFeed) -> serde_json::Value {
        loop {
            let outgoing = tokio::time::timeout(std::time::Duration::from_secs(2), feed.next())
                .await
                .unwrap();
            let Message::Text(message) = outgoing.message else {
                panic!("expected text");
            };
            let value: serde_json::Value = serde_json::from_str(message.as_str()).unwrap();
            if value["type"] == "Catalog" {
                return value;
            }
        }
    }
}
