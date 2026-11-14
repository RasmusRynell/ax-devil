//! The form frames travel in, which the listener decides. A viewer on this machine is sent the decoded planes, which
//! cost nothing to make and are what it draws. A viewer on another machine is sent each frame as a JPEG no larger than
//! [`EDGE`]: a tenth to a twentieth of the bytes, which a network carries at full rate where the planes would not. The
//! JPEG is made once per frame, however many viewers are sent it (see [`SharedFrame::compressed`]).

use crate::access::Policy;
use api_types::playback::envelope;
use axum::extract::ws::Message;
use foundation::{ErrorCode, FrameSize, UserError};
use playback::SharedFrame;

/// The longest side of a frame sent as a JPEG, in pixels.
const EDGE: u32 = 1920;

/// How a listener's viewers are sent frames.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Wire {
    /// The decoded planes as they are.
    Raw,
    /// A JPEG of each frame, fitted to [`EDGE`].
    Jpeg,
}

impl Wire {
    /// The form for viewers of a listener with `policy`.
    pub(super) fn of(policy: &Policy) -> Self {
        match policy {
            Policy::Local => Self::Raw,
            Policy::Shared { .. } => Self::Jpeg,
        }
    }

    /// The bytes a frame of `size` takes once the viewer holds it: `raw`, the planes, or the RGBA it decodes a JPEG to.
    pub(super) fn frame_bytes(self, size: FrameSize, raw: u64) -> u64 {
        match self {
            Self::Raw => raw,
            Self::Jpeg => {
                let (width, height) = media::fitted((size.width, size.height), EDGE);
                u64::from(width) * u64::from(height) * 4
            }
        }
    }

    /// The message that sends `frame`; `last` marks the final frame of the video, the same for every viewer of it.
    pub(super) async fn message(
        self,
        frame: &SharedFrame,
        last: bool,
    ) -> Result<Message, UserError> {
        let bytes = match self {
            Self::Raw => envelope::video_frame(frame, last),
            Self::Jpeg => {
                let shared = frame.clone();
                frame
                    .compressed(async move {
                        tokio::task::spawn_blocking(move || {
                            let jpeg = media::frame_jpeg(&shared, EDGE)?;
                            let size = (jpeg.width, jpeg.height);
                            Ok(envelope::jpeg_frame(&shared, last, size, &jpeg.bytes))
                        })
                        .await
                        .unwrap_or_else(|_| {
                            Err(UserError::new(
                                ErrorCode::Internal,
                                "Compressing a frame stopped unexpectedly",
                            ))
                        })
                    })
                    .await?
            }
        };
        Ok(Message::Binary(bytes))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use api_types::playback::{LAYOUT_I420, LAYOUT_JPEG};
    use bytes::Bytes;
    use foundation::{
        ColorDesc, FrameId, FrameLayout, Matrix, MediaTime, PixelFormat, Plane, VideoFrame,
    };

    fn frame() -> SharedFrame {
        let plane = |stride, len| Plane { stride, len };
        let layout = FrameLayout {
            width: 16,
            height: 8,
            format: PixelFormat::I420,
            color: ColorDesc {
                matrix: Matrix::Bt709,
                full_range: false,
            },
            planes: [plane(16, 128), plane(8, 32), plane(8, 32)],
        };
        let id = FrameId {
            sequence_id: 3,
            timestamp: MediaTime::ZERO,
        };
        VideoFrame::new(id, true, layout, Bytes::from(vec![90; 192]))
            .unwrap()
            .into()
    }

    async fn sent(wire: Wire, frame: &SharedFrame) -> Bytes {
        match wire.message(frame, false).await.unwrap() {
            Message::Binary(bytes) => bytes,
            other => panic!("a frame is binary, not {other:?}"),
        }
    }

    #[tokio::test]
    async fn viewers_on_other_machines_share_one_jpeg_of_a_frame_and_one_here_gets_its_planes() {
        let frame = frame();
        let folder = tempfile::tempdir().unwrap();
        let settings = settings::Settings::load(&folder.path().join("default.json")).unwrap();
        let remote = Wire::of(&Policy::Shared {
            password: None,
            settings: std::sync::Arc::new(settings),
        });
        let (first, second) = (sent(remote, &frame).await, sent(remote, &frame).await);
        assert_eq!(first[36], LAYOUT_JPEG);
        assert_eq!(first.as_ptr(), second.as_ptr(), "the JPEG is made once");

        let local = sent(Wire::of(&Policy::Local), &frame).await;
        assert_eq!((local[36], local.len()), (LAYOUT_I420, 64 + 192));
    }
}
