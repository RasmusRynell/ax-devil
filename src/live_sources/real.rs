//! The connector over the camera crate and FFmpeg: what the app runs with. Nothing here contacts a host until a feed
//! opens a transport, which only an open viewer does.

use std::{future::Future, mem, pin::Pin, sync::Arc, time::Duration};

use bytes::Bytes;
use camera::{
    Broker as CameraBroker, Camera, CameraError, Head, Host, LiveMessages, LiveStream,
    Login as CameraLogin, Reconnect, Resolution, RtspRoute, Scheme, Secret, SourceKey, Status,
    StreamRequest, Url, VideoCodec, Web, open_datahub, open_mqtt, open_stream,
};
use foundation::{FrameId, UserError};
use media::{StreamCodec, StreamDecoder};
use tokio::sync::{mpsc, watch};
use workspace::{Login, Protocol, ResolvedCamera};

use super::{
    bridge::Waiting,
    connector::{Broker, Connector, Decoded, Docs, Inputs, Transport, UnitDecoder},
};

/// Talks to real cameras and brokers, with the connection timeout the settings hold when each one connects.
pub struct CameraConnector {
    settings: Arc<settings::Settings>,
}

impl CameraConnector {
    /// A connector that reads `camera.timeout` from `settings` at every connection.
    pub fn new(settings: Arc<settings::Settings>) -> Self {
        Self { settings }
    }

    fn timeout(&self) -> Duration {
        timeout(&self.settings.state())
    }
}

/// The seconds one request or connection attempt may take, as `camera.timeout` holds them.
pub fn timeout(state: &settings::State) -> Duration {
    Duration::from_secs(state.auto_or_whole("camera.timeout").unwrap_or(10))
}

/// A channel that never delivers, to leave in a connection whose real channel was taken out.
fn idle<T>() -> mpsc::Receiver<T> {
    mpsc::channel(1).1
}

/// A camera that cannot be used as given.
fn unusable() -> CameraError {
    // The host check is the one place that makes an `InvalidTarget` outside the camera crate.
    Host::parse("").expect_err("an empty host is refused")
}

fn login(login: &Login) -> CameraLogin {
    CameraLogin {
        user: login.user.clone(),
        password: Secret::new(login.password.expose()),
    }
}

/// The camera of `resolved` whose requests and connection attempts take at most `timeout`, or why it cannot be
/// addressed.
pub(crate) fn camera_of(
    resolved: &ResolvedCamera,
    timeout: Duration,
) -> Result<Camera, CameraError> {
    let mut camera = Camera::new(Host::parse(&resolved.host)?);
    camera.timeout = timeout;
    camera.web = Web {
        scheme: match resolved.protocol {
            Protocol::Http => Scheme::Http,
            Protocol::Https => Scheme::Https,
        },
        port: resolved.port,
    };
    camera.rtsp_port = resolved.rtsp_port;
    camera.login = resolved.login.as_ref().map(login);
    Ok(camera)
}

/// `1280x720` as a size; `None` for anything else.
fn resolution(text: &str) -> Option<Resolution> {
    let (width, height) = text.split_once('x')?;
    let (width, height): (u32, u32) = (width.parse().ok()?, height.parse().ok()?);
    (width > 0 && height > 0).then_some(Resolution { width, height })
}

/// A transport that is over before it began.
fn rejected(error: CameraError) -> Transport {
    let (_, status) = watch::channel(Status::Failed(error));
    Transport::new(
        Inputs {
            video: None,
            docs: None,
            status,
        },
        async {},
    )
}

fn stream_transport(mut stream: LiveStream, metadata: bool) -> Transport {
    let video = mem::replace(&mut stream.video, idle());
    let docs = metadata.then(|| Docs::Metadata(mem::replace(&mut stream.metadata, idle())));
    let status = stream.status.clone();
    Transport::new(
        Inputs {
            video: Some(video),
            docs,
            status,
        },
        stream.close(),
    )
}

fn messages_transport(mut live: LiveMessages, waiting: Waiting) -> Transport {
    let messages = mem::replace(&mut live.messages, idle());
    let status = live.status.clone();
    Transport::new(
        Inputs {
            video: None,
            docs: Some(Docs::Messages { messages, waiting }),
            status,
        },
        live.close(),
    )
}

impl Connector for CameraConnector {
    fn video(&self, resolved: &ResolvedCamera, metadata: bool) -> Transport {
        let opened = camera_of(resolved, self.timeout()).and_then(|camera| {
            let head = Head::new(resolved.head).ok_or_else(unusable)?;
            let size = match resolved.resolution.as_deref() {
                Some(text) => Some(resolution(text).ok_or_else(unusable)?),
                None => None,
            };
            let url = resolved
                .url
                .as_deref()
                .map(|text| Url::parse(text).map_err(|_| unusable()))
                .transpose()?;
            let request = StreamRequest {
                head,
                video: Some(size),
                metadata,
                route: match resolved.rtsp_route {
                    workspace::RtspRoute::Direct => RtspRoute::Direct,
                    workspace::RtspRoute::WebApi => RtspRoute::WebApi,
                },
                url,
                reconnect: Reconnect::RTSP,
            };
            Ok(open_stream(&camera, request))
        });
        match opened {
            Ok(stream) => stream_transport(stream, metadata),
            Err(error) => rejected(error),
        }
    }

    fn mqtt(&self, resolved: &ResolvedCamera, broker: &Broker, source_key: &str) -> Transport {
        let opened = camera_of(resolved, self.timeout()).and_then(|camera| {
            let broker = CameraBroker {
                host: Host::parse(&broker.host)?,
                port: broker.port,
                login: broker.login.as_ref().map(login),
            };
            Ok(open_mqtt(
                &camera,
                &broker,
                SourceKey(source_key.to_owned()),
                Reconnect::MQTT,
            ))
        });
        match opened {
            Ok(live) => messages_transport(live, Waiting::Newest),
            Err(error) => rejected(error),
        }
    }

    fn datahub(&self, resolved: &ResolvedCamera, topic: &str, channel: u32) -> Transport {
        match camera_of(resolved, self.timeout()) {
            Ok(camera) => messages_transport(open_datahub(&camera, topic, channel), Waiting::Queue),
            Err(error) => rejected(error),
        }
    }

    fn decoder(
        &self,
        codec: VideoCodec,
        parameter_sets: &[u8],
    ) -> Result<Box<dyn UnitDecoder>, UserError> {
        let codec = match codec {
            VideoCodec::H264 => StreamCodec::H264,
            VideoCodec::H265 => StreamCodec::H265,
        };
        Ok(Box::new(Blocking(Some(StreamDecoder::open(
            codec,
            parameter_sets,
        )?))))
    }
}

/// A codec that decodes on a blocking thread, so a frame of a large picture never holds an async worker.
struct Blocking(Option<StreamDecoder>);

impl UnitDecoder for Blocking {
    fn decode<'a>(
        &'a mut self,
        unit: Bytes,
        id: FrameId,
    ) -> Pin<Box<dyn Future<Output = Decoded> + Send + 'a>> {
        Box::pin(async move {
            let stopped = |id: FrameId| foundation::FrameFailure {
                sequence_id: id.sequence_id,
                error: UserError::new(
                    foundation::ErrorCode::DecodeFailed,
                    "The video decoder stopped",
                ),
            };
            let Some(mut decoder) = self.0.take() else {
                return Err(stopped(id));
            };
            let (decoder, decoded) = tokio::task::spawn_blocking(move || {
                let decoded = decoder.decode(&unit, id);
                (decoder, decoded)
            })
            .await
            .map_err(|error| {
                tracing::error!("The live video decoder stopped: {error}");
                stopped(id)
            })?;
            self.0 = Some(decoder);
            decoded
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use workspace::ResolvedCamera;

    /// A camera that accepts connections and never answers.
    async fn silent_camera() -> (tokio::net::TcpListener, ResolvedCamera) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let resolved = ResolvedCamera {
            host: "127.0.0.1".into(),
            protocol: Protocol::Http,
            port: listener.local_addr().unwrap().port(),
            rtsp_port: listener.local_addr().unwrap().port(),
            rtsp_route: workspace::RtspRoute::Direct,
            head: 1,
            resolution: None,
            url: None,
            login: None,
        };
        (listener, resolved)
    }

    #[tokio::test]
    async fn a_connection_gives_up_after_the_timeout_setting_as_it_stands_when_it_connects() {
        let folder = tempfile::tempdir().unwrap();
        let settings =
            Arc::new(settings::Settings::load(&folder.path().join("settings.json")).unwrap());
        let connector = CameraConnector::new(settings.clone());
        let (_camera, resolved) = silent_camera().await;

        // The setting changes after the connector exists; the default would leave the attempt waiting for ten seconds.
        let mut values = settings.state().values();
        values.insert("camera.timeout".into(), 1.into());
        let change = settings::Change {
            values,
            shortcuts: Default::default(),
        };
        settings.replace(&change).unwrap();

        let broker = Broker {
            host: "127.0.0.1".into(),
            port: 1883,
            login: None,
        };
        let transports = [
            connector.video(&resolved, false),
            connector.mqtt(&resolved, &broker, "scene"),
            connector.datahub(&resolved, "topic", 1),
        ];
        for transport in transports {
            let (inputs, close) = transport.split();
            let mut status = inputs.status;
            let failed = tokio::time::timeout(Duration::from_secs(6), async {
                loop {
                    if let Status::Reconnecting { error, .. } = &*status.borrow_and_update() {
                        return error.kind;
                    }
                    status.changed().await.unwrap();
                }
            })
            .await
            .expect("the attempt ended within the changed timeout");
            assert_eq!(failed, camera::ErrorKind::TimedOut);
            close.await;
        }
    }
}
