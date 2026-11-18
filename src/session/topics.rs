//! The revisions of the topics a Session socket announces with `Changed`, each read from the service that owns what the
//! topic's routes answer. Every topic's routes are the Owner's, so only an Owner's connection holds a [`Topics`].

use crate::AppState;
use api_types::Topic;
use std::{
    sync::{Arc, LazyLock},
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::sync::watch;

/// When this process started, in milliseconds. A topic's counter starts at 0 on every start, so adding this keeps a
/// restarted backend from repeating a revision a client already holds, as the settings' own revision does.
static STARTED_MS: LazyLock<u64> = LazyLock::new(|| {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| u64::try_from(since.as_millis()).unwrap_or(0))
});

/// One connection's view of every topic's source.
pub(super) struct Topics {
    settings: watch::Receiver<Arc<settings::State>>,
    sessions: watch::Receiver<u64>,
    recents: watch::Receiver<u64>,
    plugins: watch::Receiver<u64>,
}

impl Topics {
    /// Subscribes to every topic's source.
    pub(super) fn of(state: &AppState) -> Self {
        Self {
            settings: state.settings.subscribe(),
            sessions: state.sessions.listing_revisions(),
            recents: state.files.recent_revisions(),
            plugins: state.plugins.revisions(),
        }
    }

    /// The topic's revision now; the change that raised it no longer counts as new.
    pub(super) fn revision(&mut self, topic: Topic) -> u64 {
        match topic {
            Topic::Settings => self.settings.borrow_and_update().revision(),
            Topic::Sessions => *STARTED_MS + *self.sessions.borrow_and_update(),
            Topic::Recents => *STARTED_MS + *self.recents.borrow_and_update(),
            Topic::Plugins => *STARTED_MS + *self.plugins.borrow_and_update(),
        }
    }

    /// The next topic that changed. Never returns once every source is gone, which only happens when the backend
    /// stops.
    pub(super) async fn changed(&mut self) -> Topic {
        tokio::select! {
            Ok(()) = self.settings.changed() => Topic::Settings,
            Ok(()) = self.sessions.changed() => Topic::Sessions,
            Ok(()) = self.recents.changed() => Topic::Recents,
            Ok(()) = self.plugins.changed() => Topic::Plugins,
            else => std::future::pending().await,
        }
    }
}
