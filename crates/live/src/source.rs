//! The live sources of the app: every open camera pipeline, keyed by what it connects to, so two windows on one
//! camera share one connection. Nothing connects until a viewer opens, and a pipeline ends with its last viewer.

use std::{
    collections::{HashMap, HashSet},
    hash::{Hash, Hasher},
    sync::{
        Arc, Mutex, PoisonError,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

use foundation::{ErrorCode, UserError};
use futures_util::future::{join, join_all};
use handlers::Handlers;
use playback::SharedFrame;
use scene::matching::MatchPolicy;
use tokio::{runtime::Handle, sync::watch};
use workspace::{LiveOverlay, LiveVideoContent, ResolvedCamera, url_without_password};

use super::{connector::Connector, feed::Feed, real::CameraConnector, viewer::LiveViewer};

/// What a pipeline connects to, as resolved: the camera, with its login, and the overlay. Two contents of the same
/// spec share a pipeline.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Spec {
    /// The camera.
    pub camera: ResolvedCamera,
    /// The overlay.
    pub overlay: LiveOverlay,
}

impl Spec {
    /// The spec of `content`.
    pub fn of(content: &LiveVideoContent) -> Self {
        Self {
            camera: content.camera.clone(),
            overlay: content.overlay.clone(),
        }
    }
}

impl Hash for Spec {
    /// Hashes what tells specs apart without touching a credential; equality decides the rest.
    fn hash<H: Hasher>(&self, state: &mut H) {
        let camera = &self.camera;
        (
            &camera.host,
            camera.port,
            camera.rtsp_port,
            camera.rtsp_route.name(),
            camera.head,
            &camera.resolution,
            // The URL's login is a credential; equality still tells two URLs apart.
            camera.url.as_deref().map(url_without_password),
            self.overlay.mode_name(),
        )
            .hash(state);
    }
}

impl Spec {
    /// The camera endpoint (host and web port) whose MQTT client this spec holds, or `None` for a spec without an
    /// MQTT overlay. A camera has one MQTT client per endpoint, whatever else differs between specs.
    fn mqtt_endpoint(&self) -> Option<(String, u16)> {
        matches!(self.overlay, LiveOverlay::Mqtt { .. })
            .then(|| (self.camera.host.to_ascii_lowercase(), self.camera.port))
    }
}

pub(super) enum Slot {
    Open(Arc<Feed>),
    /// Being closed; the receiver turns true when it is. A new open of the spec waits for that, and so does a new
    /// open of any other MQTT spec of the same camera endpoint, because a camera has one MQTT client.
    Closing(watch::Receiver<bool>),
}

pub(super) struct Inner {
    connector: Arc<dyn Connector>,
    pub(super) slots: Mutex<HashMap<Spec, Slot>>,
    /// The hosts a viewer was ever opened for, lower case: the ones the user started.
    started: Mutex<HashSet<String>>,
    runtime: Handle,
    stopped: AtomicBool,
}

impl Inner {
    /// A viewer of `feed` has gone. The last one out closes the pipeline in the background; its `Closing` slot is
    /// what [`LiveSources::shutdown`] waits on.
    pub(super) fn release(self: &Arc<Self>, feed: &Arc<Feed>) {
        let (done, closing) = watch::channel(false);
        {
            let mut slots = self.slots.lock().unwrap_or_else(PoisonError::into_inner);
            if feed.viewers.fetch_sub(1, Ordering::AcqRel) != 1 {
                return;
            }
            slots.insert(feed.spec.clone(), Slot::Closing(closing));
        }
        let (inner, feed) = (Arc::clone(self), Arc::clone(feed));
        self.runtime.spawn(async move {
            feed.close().await;
            let mut slots = inner.slots.lock().unwrap_or_else(PoisonError::into_inner);
            if matches!(slots.get(&feed.spec), Some(Slot::Closing(_))) {
                slots.remove(&feed.spec);
            }
            drop(slots);
            done.send_replace(true);
        });
    }
}

/// Every live pipeline of the app.
#[derive(Clone)]
pub struct LiveSources(pub(super) Arc<Inner>);

impl LiveSources {
    /// Live sources over `connector`. Must be made inside a Tokio runtime, which runs their tasks.
    pub fn new(connector: Arc<dyn Connector>) -> Self {
        Self(Arc::new(Inner {
            connector,
            slots: Mutex::default(),
            started: Mutex::default(),
            runtime: Handle::current(),
            stopped: AtomicBool::new(false),
        }))
    }

    /// Live sources over real cameras and brokers.
    pub fn cameras(settings: Arc<settings::Settings>) -> Self {
        Self::new(Arc::new(CameraConnector::new(settings)))
    }

    /// A viewer of `content`, starting its pipeline when no other viewer has one. Opening a viewer is the first thing
    /// that contacts the camera. `matching` is how the lane starts out matching samples to frames.
    pub async fn open(
        &self,
        content: &LiveVideoContent,
        handlers: &Handlers,
        matching: MatchPolicy,
    ) -> Result<LiveViewer, UserError> {
        let spec = Spec::of(content);
        self.0
            .started
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .insert(spec.camera.host.to_ascii_lowercase());
        loop {
            let closing = {
                let mut slots = self.0.slots.lock().unwrap_or_else(PoisonError::into_inner);
                if self.0.stopped.load(Ordering::Acquire) {
                    return Err(UserError::new(
                        ErrorCode::VideoClosed,
                        "The backend is stopping",
                    ));
                }
                match slots.get(&spec) {
                    Some(Slot::Open(feed)) => {
                        feed.viewers.fetch_add(1, Ordering::AcqRel);
                        return Ok(LiveViewer::attach(
                            Arc::clone(&self.0),
                            Arc::clone(feed),
                            matching,
                        ));
                    }
                    Some(Slot::Closing(closing)) => closing.clone(),
                    None => {
                        // Another spec of the camera's MQTT client may still be held by a feed that is closing.
                        match spec.mqtt_endpoint().and_then(|endpoint| {
                            slots.iter().find_map(|(other, slot)| match slot {
                                Slot::Closing(closing)
                                    if other.mqtt_endpoint().as_ref() == Some(&endpoint) =>
                                {
                                    Some(closing.clone())
                                }
                                _ => None,
                            })
                        }) {
                            Some(closing) => closing,
                            None => {
                                let feed = self.start(&spec, handlers)?;
                                feed.viewers.fetch_add(1, Ordering::AcqRel);
                                slots.insert(spec, Slot::Open(Arc::clone(&feed)));
                                return Ok(LiveViewer::attach(Arc::clone(&self.0), feed, matching));
                            }
                        }
                    }
                }
            };
            // A feed of the same camera is being closed: its MQTT client is free only after that.
            let mut closing = closing;
            let _ = closing.wait_for(|closed| *closed).await;
        }
    }

    /// What the open viewers of `content` have on screen: `None` when none is open, `Some(None)` while the stream has
    /// shown no picture yet, else the newest one. Asking connects to nothing.
    pub fn open_picture(&self, content: &LiveVideoContent) -> Option<Option<SharedFrame>> {
        let slots = self.0.slots.lock().unwrap_or_else(PoisonError::into_inner);
        match slots.get(&Spec::of(content)) {
            Some(Slot::Open(feed)) => Some(feed.shared.picture.borrow().clone()),
            Some(Slot::Closing(_)) | None => None,
        }
    }

    /// Whether a viewer was opened for `host` since the app started: the user started a stream there.
    pub fn has_started(&self, host: &str) -> bool {
        self.0
            .started
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .contains(&host.to_ascii_lowercase())
    }

    fn start(&self, spec: &Spec, handlers: &Handlers) -> Result<Arc<Feed>, UserError> {
        let decoder = match spec.overlay.handler() {
            Some(id) => Some(handlers.decoder(id).cloned().ok_or_else(|| {
                UserError::new(
                    ErrorCode::UnknownHandler,
                    format!("Unknown live overlay handler type: {id}"),
                )
            })?),
            None => None,
        };
        Feed::start(spec.clone(), Arc::clone(&self.0.connector), decoder)
    }

    /// Closes every pipeline and waits up to `deadline` for the connections to end, so a camera's temporary MQTT
    /// publisher is removed before the app exits. Anything still open after that is logged. Nothing opens afterwards.
    pub async fn shutdown(&self, deadline: Duration) {
        self.0.stopped.store(true, Ordering::Release);
        // A feed whose last viewer just left is `Closing`, not open: its close is already under way, and the slot
        // is the one record of it, so the shutdown waits on that too.
        let (feeds, mut closing) = {
            let slots = self.0.slots.lock().unwrap_or_else(PoisonError::into_inner);
            let mut feeds = Vec::new();
            let mut closing = Vec::new();
            for slot in slots.values() {
                match slot {
                    Slot::Open(feed) => feeds.push(Arc::clone(feed)),
                    Slot::Closing(done) => closing.push(done.clone()),
                }
            }
            (feeds, closing)
        };
        let everything = async {
            let closed = join_all(
                closing
                    .iter_mut()
                    .map(|done| done.wait_for(|closed| *closed)),
            );
            join(join_all(feeds.iter().map(|feed| feed.close())), closed).await;
        };
        if tokio::time::timeout(deadline, everything).await.is_err() {
            let left = feeds.iter().filter(|feed| !feed.is_closed()).count()
                + closing.iter().filter(|done| !*done.borrow()).count();
            tracing::warn!(
                "{left} live sources did not close within {} s, so a camera may keep a temporary MQTT publisher",
                deadline.as_secs()
            );
        }
    }
}

#[cfg(test)]
mod spec_tests {
    use super::*;
    use workspace::{Protocol, RtspRoute};

    /// A hasher that keeps what it is fed, so the test can look for a credential in it.
    #[derive(Default)]
    struct Recorder(Vec<u8>);

    impl Hasher for Recorder {
        fn finish(&self) -> u64 {
            0
        }
        fn write(&mut self, bytes: &[u8]) {
            self.0.extend_from_slice(bytes);
        }
    }

    fn spec(url: &str) -> Spec {
        Spec {
            camera: ResolvedCamera {
                host: "cam".into(),
                protocol: Protocol::Https,
                port: 443,
                rtsp_port: 554,
                rtsp_route: RtspRoute::Direct,
                head: 1,
                resolution: None,
                url: Some(url.into()),
                login: None,
            },
            overlay: LiveOverlay::None,
        }
    }

    #[test]
    fn a_spec_hashes_and_prints_without_its_url_password_in_any_spelling_but_still_tells_urls_apart()
     {
        for url in [
            "http://root:SECRET@cam/s",
            "http:root:SECRET@cam/s",
            "http:////root:SECRET@cam/s",
            "rtsp://root:SE%43RET@cam/s",
            "mailto:root:SECRET@cam",
        ] {
            let spec = spec(url);
            let mut recorder = Recorder::default();
            spec.hash(&mut recorder);
            let hashed = String::from_utf8_lossy(&recorder.0).into_owned();
            for text in [hashed, format!("{spec:?}"), format!("{spec:#?}")] {
                assert!(!text.contains("SECRET"), "{url}: {text}");
            }
        }
        assert_ne!(spec("http://root:one@cam/s"), spec("http://root:two@cam/s"));
    }
}
