//! One RTSP connection: describe, set up the wanted streams, play, and read packets until it ends.

use std::future::pending;
use std::sync::Arc;
use std::time::Duration;

use bytes::{BufMut, Bytes, BytesMut};
use futures_util::StreamExt;
use retina::client::{
    Credentials, PacketItem, PlayOptions, Playing, Session, SessionOptions, SetupOptions, Stream,
};
use retina::codec::{ParametersRef, VideoParametersCodec};
use tokio::time::{Instant, sleep_until, timeout};

use super::Link;
use super::assemble::{Frame, VideoAssembler};
use super::media_url;
use super::metadata::{MetadataAssembler, Piece};
use super::packet::Packet;
use crate::error::{CameraError, ErrorKind};
use crate::live::{AccessUnit, CodecParams, Counters, MetadataDoc, VideoCodec};

const START_CODE: [u8; 4] = [0, 0, 0, 1];

/// A playing session and what to do with each of its streams, by retina's stream index.
pub(super) struct Opened {
    session: Session<Playing>,
    video: Option<(usize, VideoAssembler)>,
    metadata: Option<(usize, MetadataAssembler)>,
}

impl Link {
    /// Describes, sets up and plays the streams the request asks for.
    pub(super) async fn open(&self) -> Result<Opened, CameraError> {
        let url = media_url(&self.camera, &self.request)?;
        // The password goes to the RTSP client here and nowhere else; retina's types print it, so none is formatted.
        // Retina answers any challenge with it, Basic included, even over plain RTSP; it has no hook to refuse (README).
        let creds = self.camera.login.as_ref().map(|login| Credentials {
            username: login.user.clone(),
            password: login.password.expose().to_owned(),
        });
        let options = SessionOptions::default()
            .creds(creds)
            .session_group(Arc::clone(&self.sessions));
        let mut session = self
            .step(Session::describe(url, options), ErrorKind::Unreachable)
            .await?;
        let video = match self.request.video {
            Some(_) => Some(find_video(session.streams())?),
            None => None,
        };
        let metadata = match self.request.metadata {
            true => Some(find_metadata(session.streams())?),
            false => None,
        };
        for id in video.iter().map(|v| v.0).chain(metadata) {
            self.step(session.setup(id, SetupOptions::default()), ErrorKind::Lost)
                .await?;
        }
        let session = self
            .step(session.play(PlayOptions::default()), ErrorKind::Lost)
            .await?;
        Ok(Opened {
            session,
            video: video.map(|(id, params)| (id, VideoAssembler::new(params))),
            metadata: metadata.map(|id| (id, MetadataAssembler::new())),
        })
    }

    /// Runs one RTSP step within the camera's timeout. Retina reports only a response status in a typed way, so a
    /// failure without one is `otherwise`: what the step being at this point of the connection means.
    async fn step<T>(
        &self,
        future: impl Future<Output = Result<T, retina::Error>>,
        otherwise: ErrorKind,
    ) -> Result<T, CameraError> {
        match timeout(self.camera.timeout, future).await {
            Ok(result) => result.map_err(|error| self.classify(&error, otherwise)),
            Err(_) => Err(CameraError::new(
                ErrorKind::TimedOut,
                format!("no answer within {} s", self.camera.timeout.as_secs_f32()),
            )),
        }
    }

    /// The kind of a retina error: from the response status when it has one (a 401 is a rejected login when one was
    /// sent), else `otherwise`. The detail is retina's first line, which names no URL and no login.
    fn classify(&self, error: &retina::Error, otherwise: ErrorKind) -> CameraError {
        let kind = match error.status_code() {
            Some(401) if self.camera.login.is_some() => ErrorKind::LoginRejected,
            Some(401) => ErrorKind::LoginRequired,
            Some(status) => ErrorKind::of_status(status),
            None => otherwise,
        };
        let text = error.to_string();
        CameraError::new(kind, text.lines().next().unwrap_or_default())
    }

    /// Reads packets until the connection fails, which is the `Err`; `Ok` means the app stopped listening.
    pub(super) async fn read(
        &mut self,
        mut opened: Opened,
        epoch: u32,
        attempt: &mut u32,
    ) -> Result<(), CameraError> {
        let silence = self.request.reconnect.silence;
        let mut deadline = silence.map(|limit| Instant::now() + limit);
        let mut frames = Vec::new();
        let mut pieces = Vec::new();
        loop {
            let item = tokio::select! {
                item = opened.session.next() => item,
                () = lapse(deadline) => {
                    return Err(CameraError::new(
                        ErrorKind::Silent,
                        format!("nothing received for {} s", silence.unwrap_or(Duration::ZERO).as_secs_f32()),
                    ));
                }
            };
            let received = match item {
                Some(Ok(PacketItem::Rtp(received))) => received,
                Some(Ok(_)) => continue,
                Some(Err(error)) => return Err(self.classify(&error, ErrorKind::Lost)),
                None => {
                    return Err(CameraError::new(
                        ErrorKind::Lost,
                        "the camera ended the stream",
                    ));
                }
            };
            let id = received.stream_id();
            let packet = Packet::from_received(received);
            let mut primary = opened.video.is_none();
            if let Some((_, assembler)) = opened.video.as_mut().filter(|(v, _)| *v == id) {
                primary = true;
                assembler.push(&packet, &mut frames);
                Counters::add(&self.counters.video_dropped, assembler.take_dropped());
                for frame in frames.drain(..) {
                    let unit = access_unit(epoch, &assembler.params, frame);
                    if self.video.send(unit, &self.counters).is_err() {
                        return Ok(());
                    }
                }
            } else if let Some((_, assembler)) = opened.metadata.as_mut().filter(|(m, _)| *m == id)
            {
                assembler.push(&packet, &mut pieces);
                for piece in pieces.drain(..) {
                    let sent = match piece {
                        Piece::Doc { capture, xml } => {
                            let doc = MetadataDoc {
                                epoch,
                                capture,
                                xml,
                            };
                            self.metadata.send(doc, &self.counters)
                        }
                        Piece::Lost => {
                            self.metadata.lose(1, &self.counters);
                            Ok(())
                        }
                    };
                    if sent.is_err() {
                        return Ok(());
                    }
                }
            } else {
                continue;
            }
            // Data from the lane the stream is judged by (video, else metadata) proves the connection is alive.
            if primary {
                *attempt = 0;
                deadline = silence.map(|limit| Instant::now() + limit);
            }
        }
    }
}

fn access_unit(epoch: u32, codec: &Arc<CodecParams>, frame: Frame) -> AccessUnit {
    AccessUnit {
        epoch,
        codec: Arc::clone(codec),
        keyframe: frame.keyframe,
        capture: frame.capture,
        data: frame.data,
    }
}

/// Waits until `deadline`; forever when there is none.
async fn lapse(deadline: Option<Instant>) {
    match deadline {
        Some(deadline) => sleep_until(deadline).await,
        None => pending().await,
    }
}

/// The first H.264 or H.265 video stream, with the parameter sets the camera described.
fn find_video(streams: &[Stream]) -> Result<(usize, Arc<CodecParams>), CameraError> {
    let found = streams.iter().enumerate().find_map(|(id, stream)| {
        let codec = match (stream.media(), stream.encoding_name()) {
            ("video", "h264") => VideoCodec::H264,
            ("video", "h265") => VideoCodec::H265,
            _ => return None,
        };
        Some((id, codec, stream))
    });
    let Some((id, codec, stream)) = found else {
        return Err(CameraError::new(
            ErrorKind::Unsupported,
            "the camera offers no H.264 or H.265 video",
        ));
    };
    let parameter_sets = match stream.parameters() {
        Some(ParametersRef::Video(video)) => match video.codec_params() {
            VideoParametersCodec::H264 { sps, pps } => annex_b(&[sps, pps]),
            VideoParametersCodec::H265 { vps, sps, pps } => annex_b(&[vps, sps, pps]),
            _ => Bytes::new(),
        },
        _ => Bytes::new(),
    };
    Ok((
        id,
        Arc::new(CodecParams {
            codec,
            parameter_sets,
        }),
    ))
}

/// The first uncompressed ONVIF metadata stream.
fn find_metadata(streams: &[Stream]) -> Result<usize, CameraError> {
    streams
        .iter()
        .position(|s| s.media() == "application" && s.encoding_name() == "vnd.onvif.metadata")
        .ok_or_else(|| {
            CameraError::new(
                ErrorKind::Unsupported,
                "the camera offers no scene metadata (analytics)",
            )
        })
}

fn annex_b(nals: &[&Bytes]) -> Bytes {
    let mut out = BytesMut::new();
    for nal in nals {
        out.put_slice(&START_CODE);
        out.put_slice(nal);
    }
    out.freeze()
}
