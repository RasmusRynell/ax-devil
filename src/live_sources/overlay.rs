//! The overlay side of a feed: the camera's documents (ONVIF metadata, MQTT messages, DataHub samples) into the
//! decoder of the live track, losslessly. When the decoder falls behind, or documents were lost on the way, the lane is
//! behind until the queue drains, then the decoder starts a new session and the Events list gets a gap marker.

use std::{
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use camera::{DataHubStamp, Feed, Message, MetadataDoc, Status};
use foundation::MediaTime;
use tokio::{
    sync::watch,
    time::{MissedTickBehavior, interval},
};

use super::{
    bridge::{Doc, Refused, Session, Sessions},
    connector::Docs,
    feed::Shared,
};

/// How many documents may wait for the decoder, in every session together: a few seconds of a busy camera.
pub const QUEUE: usize = 256;
/// How often a lane that is behind looks at whether its queue has drained.
const SETTLE_EVERY: Duration = Duration::from_millis(50);

/// What reached the overlay.
pub enum Arrival {
    /// A document of connection `epoch`, with the time the camera or this machine gave it, in microseconds.
    Doc {
        epoch: u32,
        doc: Doc,
        time_us: Option<i64>,
    },
    /// The connection lost documents.
    Lost,
}

/// What a document changed in what the viewer is told.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Effects {
    /// The lane became behind (`true`) or caught up (`false`).
    pub behind: Option<bool>,
    /// A gap ended: where it began, in microseconds on the video clock.
    pub gap: Option<i64>,
}

/// Documents lost since the decoder fell behind.
struct Trouble {
    since_us: Option<i64>,
}

/// The documents of one overlay, in order, into sessions of the decoder.
pub struct Intake {
    sessions: Sessions,
    capacity: usize,
    session: Session,
    epoch: Option<u32>,
    last_us: Option<i64>,
    trouble: Option<Trouble>,
}

impl Intake {
    /// An intake starting its first session; at most `capacity` documents wait for the decoder across its sessions.
    pub fn new(sessions: Sessions, capacity: usize) -> Self {
        let session = sessions.start(capacity);
        Self {
            sessions,
            capacity,
            session,
            epoch: None,
            last_us: None,
            trouble: None,
        }
    }

    /// Takes in what arrived. A new connection starts a new session, so the decoder never joins what one connection
    /// began with what the next one says. While the lane is behind, documents are lost, not queued behind the
    /// backlog.
    pub fn take(&mut self, arrival: Arrival, video_us: Option<i64>) -> Effects {
        match arrival {
            Arrival::Doc {
                epoch,
                doc,
                time_us,
            } => {
                // While behind, the documents of a new connection are lost like the rest, and the session that
                // follows the drain is the new start.
                let mut effects =
                    if self.trouble.is_none() && self.epoch.is_some_and(|seen| seen != epoch) {
                        // What is queued is read, then the decoder starts over.
                        self.begin_session()
                    } else {
                        self.settle()
                    };
                self.epoch = Some(epoch);
                self.queue(doc, time_us.or(video_us), &mut effects);
                effects
            }
            Arrival::Lost => {
                let mut effects = Effects::default();
                self.fall_behind(video_us, &mut effects);
                effects
            }
        }
    }

    /// Ends a lane's trouble once its queue has drained: a new session, so the decoder is reset, and the gap.
    pub fn settle(&mut self) -> Effects {
        if self.trouble.is_none() || self.sessions.backlog() > 0 {
            return Effects::default();
        }
        self.begin_session()
    }

    /// A new session for the decoder, unless the current one was never used; ends the trouble, if any, with its gap.
    fn begin_session(&mut self) -> Effects {
        if !self.session.is_unused() {
            self.session = self.sessions.start(self.capacity);
        }
        match self.trouble.take() {
            Some(trouble) => Effects {
                behind: Some(false),
                gap: Some(self.gap_time(&trouble)),
            },
            None => Effects::default(),
        }
    }

    fn gap_time(&self, trouble: &Trouble) -> i64 {
        trouble.since_us.or(self.last_us).unwrap_or(0)
    }

    fn fall_behind(&mut self, time_us: Option<i64>, effects: &mut Effects) {
        if self.trouble.is_none() {
            self.trouble = Some(Trouble {
                since_us: time_us.or(self.last_us),
            });
            effects.behind = Some(true);
        }
    }

    fn queue(&mut self, doc: Doc, time_us: Option<i64>, effects: &mut Effects) {
        if self.trouble.is_some() {
            log_lost(&doc);
            return;
        }
        match self.session.push(doc) {
            Ok(()) => self.last_us = time_us.or(self.last_us),
            Err(Refused::Full(doc)) => {
                log_lost(&doc);
                self.fall_behind(time_us, effects);
            }
            // The track stopped reading; the feed is being closed.
            Err(Refused::Closed) => {}
        }
    }
}

/// Logs a document dropped while the decoder is behind, with the MQTT or DataHub topic and the DataHub envelope it
/// carried.
fn log_lost(doc: &Doc) {
    let mut origin = doc
        .topic
        .iter()
        .map(|topic| format!("topic {topic}"))
        .collect::<Vec<_>>();
    if let Some(DataHubStamp {
        channel,
        timestamp,
        historical,
    }) = &doc.stamp
    {
        origin.push(format!(
            "channel {channel}, timestamp {timestamp}, historical {historical}"
        ));
    }
    tracing::debug!("a live overlay document was lost ({})", origin.join(", "));
}

fn micros(time: SystemTime) -> Option<i64> {
    let since = time.duration_since(UNIX_EPOCH).ok()?;
    i64::try_from(since.as_micros()).ok()
}

impl Docs {
    /// The next arrival; `None` when the connection's channel is closed.
    async fn arrival(&mut self) -> Option<Arrival> {
        match self {
            Docs::Metadata(docs) => Some(match docs.recv().await? {
                Feed::Item(MetadataDoc {
                    epoch,
                    capture,
                    xml,
                }) => Arrival::Doc {
                    epoch,
                    doc: Doc {
                        bytes: xml,
                        capture: capture.map(|time| MediaTime::from_us(time.0.div_euclid(1000))),
                        topic: None,
                        stamp: None,
                    },
                    time_us: capture.map(|time| time.0.div_euclid(1000)),
                },
                Feed::Gap { lost } => {
                    tracing::warn!("{lost} live overlay documents were lost");
                    Arrival::Lost
                }
            }),
            Docs::Messages(messages) => Some(match messages.recv().await? {
                Feed::Item(Message {
                    epoch,
                    topic,
                    payload,
                    stamp,
                    received,
                }) => Arrival::Doc {
                    epoch,
                    doc: Doc {
                        bytes: payload,
                        capture: None,
                        topic: Some(topic),
                        stamp,
                    },
                    time_us: micros(received),
                },
                Feed::Gap { lost } => {
                    tracing::warn!("{lost} live overlay documents were lost");
                    Arrival::Lost
                }
            }),
        }
    }
}

/// Reads one overlay connection until it is stopped.
pub struct OverlayTask {
    shared: Arc<Shared>,
    docs: Option<Docs>,
    /// The overlay's own connection; `None` when the metadata rides in the video's.
    link: Option<watch::Receiver<Status>>,
    stop: watch::Receiver<bool>,
}

impl OverlayTask {
    /// A task reading `docs` (a connection that never opened has none), reporting `link` when the overlay has a
    /// connection of its own.
    pub fn new(
        shared: Arc<Shared>,
        docs: Option<Docs>,
        link: Option<watch::Receiver<Status>>,
        stop: watch::Receiver<bool>,
    ) -> Self {
        Self {
            shared,
            docs,
            link,
            stop,
        }
    }

    fn apply(&self, effects: Effects) {
        if let Some(behind) = effects.behind {
            self.shared.board.behind(behind, self.shared.clock.now());
        }
        if let Some(time_us) = effects.gap {
            self.shared.gap(time_us);
        }
    }

    /// Runs until stopped.
    pub async fn run(mut self) {
        let Some(sessions) = self.shared.sessions.clone() else {
            return;
        };
        let mut intake = Intake::new(sessions, QUEUE);
        let mut settle = interval(SETTLE_EVERY);
        settle.set_missed_tick_behavior(MissedTickBehavior::Skip);
        let (mut docs, mut link, mut stop) =
            (self.docs.take(), self.link.take(), self.stop.clone());
        if let Some(link) = &mut link {
            self.report(link);
        }
        loop {
            tokio::select! {
                biased;
                _ = stop.changed() => break,
                arrival = async {
                    match &mut docs {
                        Some(docs) => docs.arrival().await,
                        None => std::future::pending().await,
                    }
                } => match arrival {
                    Some(arrival) => {
                        if let Arrival::Doc { epoch, .. } = arrival {
                            self.shared.board.overlay_data(epoch, self.shared.clock.now());
                        }
                        let effects = intake.take(arrival, self.shared.video_now());
                        self.apply(effects);
                    }
                    // The connection ended; its status says why.
                    None => docs = None,
                },
                changed = async {
                    match &mut link {
                        Some(link) => link.changed().await,
                        None => std::future::pending().await,
                    }
                } => match (changed, &mut link) {
                    (Ok(()), Some(link)) => self.report(link),
                    _ => link = None,
                },
                _ = settle.tick() => {
                    let effects = intake.settle();
                    self.apply(effects);
                }
            }
        }
    }

    fn report(&self, link: &mut watch::Receiver<Status>) {
        let now = self.shared.clock.now();
        self.shared
            .board
            .overlay_link(&link.borrow_and_update(), now);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::live_sources::bridge::sessions;
    use bytes::Bytes;
    use foundation::CancelToken;
    use scene_store::Feed;

    fn doc(n: u8) -> Doc {
        Doc {
            bytes: Bytes::from(vec![n]),
            capture: None,
            topic: None,
            stamp: None,
        }
    }

    fn arrival(epoch: u32, n: u8) -> Arrival {
        Arrival::Doc {
            epoch,
            doc: doc(n),
            time_us: Some(i64::from(n) * 1000),
        }
    }

    #[test]
    fn a_decoder_that_falls_behind_loses_documents_until_its_queue_drains_then_restarts_with_a_gap()
    {
        let (offers, mut feed) = sessions();
        let mut intake = Intake::new(offers, 2);
        let cancel = CancelToken::new();
        let mut first = feed.next_input(&cancel).expect("the first session");

        assert_eq!(intake.take(arrival(1, 1), None), Effects::default());
        assert_eq!(intake.take(arrival(1, 2), None), Effects::default());
        // The queue is full: the third is lost and the lane is behind from its time.
        assert_eq!(
            intake.take(arrival(1, 3), None),
            Effects {
                behind: Some(true),
                gap: None
            }
        );
        // Documents that come meanwhile are lost too, and the backlog stays whole and in order.
        assert_eq!(intake.take(arrival(1, 4), None), Effects::default());
        assert_eq!(intake.settle(), Effects::default());
        let read = |input: &mut Box<dyn handler_api::Input + Send>| {
            input
                .next(&cancel)
                .unwrap()
                .map(|record| record.bytes.to_vec())
        };
        assert_eq!(read(&mut first), Some(vec![1]));
        assert_eq!(intake.settle(), Effects::default());
        assert_eq!(read(&mut first), Some(vec![2]));

        // Drained: a new session, and the gap starts where the first loss did.
        assert_eq!(
            intake.settle(),
            Effects {
                behind: Some(false),
                gap: Some(3000)
            }
        );
        assert_eq!(
            read(&mut first),
            None,
            "the old session ends, which resets the decoder"
        );
        let mut second = feed.next_input(&cancel).expect("the next session");
        assert_eq!(intake.take(arrival(1, 5), None), Effects::default());
        assert_eq!(read(&mut second), Some(vec![5]));
    }

    #[test]
    fn lost_documents_and_new_connections_both_restart_the_decoder() {
        let (offers, mut feed) = sessions();
        let mut intake = Intake::new(offers, 8);
        let cancel = CancelToken::new();
        let mut first = feed.next_input(&cancel).expect("the first session");
        intake.take(arrival(1, 1), None);

        // The camera dropped documents: behind at once, and nothing queued remains, so the next look ends it.
        let lost = intake.take(Arrival::Lost, None);
        assert_eq!(lost.behind, Some(true));
        assert_eq!(
            first
                .next(&cancel)
                .unwrap()
                .map(|record| record.bytes.to_vec()),
            Some(vec![1])
        );
        let caught_up = intake.settle();
        assert_eq!((caught_up.behind, caught_up.gap), (Some(false), Some(1000)));

        // A reconnect is a new epoch, and a new session whatever the queue held.
        let mut second = feed
            .next_input(&cancel)
            .expect("the session after the loss");
        intake.take(arrival(1, 2), None);
        intake.take(arrival(2, 3), None);
        let mut third = feed.next_input(&cancel).expect("the session of epoch 2");
        let bytes = |input: &mut Box<dyn handler_api::Input + Send>| {
            input
                .next(&cancel)
                .unwrap()
                .map(|record| record.bytes.to_vec())
        };
        assert_eq!(bytes(&mut second), Some(vec![2]));
        assert_eq!(bytes(&mut second), None);
        assert_eq!(bytes(&mut third), Some(vec![3]));
    }

    #[test]
    fn reconnecting_while_the_decoder_is_behind_keeps_the_backlog_within_the_limit() {
        let (offers, mut feed) = sessions();
        let mut intake = Intake::new(offers, 4);
        let cancel = CancelToken::new();
        let mut behind = false;
        // Twenty reconnects, one document each, and the decoder reads nothing.
        for epoch in 1..=20 {
            behind |= intake.take(arrival(epoch, 1), None).behind == Some(true);
        }
        assert!(behind, "the lane fell behind");
        assert_eq!(intake.sessions.backlog(), 4);

        drop(intake);
        let (mut sessions, mut docs) = (0, 0);
        while let Some(mut input) = feed.next_input(&cancel) {
            sessions += 1;
            while input.next(&cancel).unwrap().is_some() {
                docs += 1;
            }
        }
        assert_eq!(docs, 4);
        assert!(sessions <= 5, "{sessions} sessions were kept");
    }
}
