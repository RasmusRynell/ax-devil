//! A viewer's wish to have the next entry readied: kept for as long as the stream lives and acted on while the
//! `playback.prepare_next_video` setting is on.

use crate::prepare::{Preparation, Preparer, Work};
use settings::{Settings, State};
use std::sync::Arc;
use tokio::sync::watch;
use workspace::Content;

const SETTING: &str = "playback.prepare_next_video";

/// What the stream's viewer asked to have prepared and what runs for it. The entry is looked up in the Content the
/// stream's lease holds, so a viewer can only have the files of its own Content read. Ends the preparation with it.
pub(super) struct Preparing<'a> {
    content: &'a Content,
    preparer: &'a Preparer,
    /// The settings, which the stream hears change instead of asking again and again.
    changes: watch::Receiver<Arc<State>>,
    wanted: Option<Work>,
    running: Option<(Work, Preparation)>,
}

impl<'a> Preparing<'a> {
    pub(super) fn new(content: &'a Content, preparer: &'a Preparer, settings: &Settings) -> Self {
        Self {
            content,
            preparer,
            changes: settings.subscribe(),
            wanted: None,
            running: None,
        }
    }

    /// Takes the viewer's latest wish: the entry `entry` names and the lanes labelled `lanes`. No entry, or one the
    /// Content does not have, wishes for nothing.
    pub(super) fn ask(&mut self, entry: Option<&str>, lanes: &[String]) {
        self.wanted = entry
            .and_then(|id| {
                self.content
                    .entries()
                    .iter()
                    .find(|entry| entry.id.as_str() == id)
            })
            .map(|entry| Work::of(entry, lanes));
        self.follow();
    }

    /// Resolves when the settings changed since they were last looked at; never resolves once they can no longer
    /// change.
    pub(super) async fn settings_changed(&mut self) {
        if self.changes.changed().await.is_err() {
            std::future::pending::<()>().await;
        }
    }

    /// Makes what runs what is wanted while the setting is on, and nothing otherwise.
    pub(super) fn follow(&mut self) {
        let on = self
            .changes
            .borrow_and_update()
            .stored(SETTING)
            .and_then(serde_json::Value::as_bool)
            == Some(true);
        match self.wanted.as_ref().filter(|_| on) {
            Some(work)
                if self
                    .running
                    .as_ref()
                    .is_some_and(|(running, _)| running == work) => {}
            Some(work) => {
                self.running = Some((work.clone(), self.preparer.start(work.clone())));
            }
            None => self.running = None,
        }
    }
}
