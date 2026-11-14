use crate::{ErrorCode, FrameId, MediaTime, UserError};
use bytes::Bytes;

/// How the samples of a frame are laid out in its planes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PixelFormat {
    /// 8-bit 4:2:0 with separate Y, U and V planes.
    I420,
    /// 8-bit 4:2:0 with a Y plane and an interleaved UV plane.
    Nv12,
    /// 8-bit RGBA in one plane.
    Rgba,
}

/// The YUV to RGB conversion matrix of a video.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Matrix {
    /// ITU-R BT.601.
    Bt601,
    /// ITU-R BT.709.
    Bt709,
}

/// What a decoder needs to turn YUV samples into RGB.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ColorDesc {
    /// The conversion matrix.
    pub matrix: Matrix,
    /// Whether samples use the full 0..=255 range instead of the limited video range.
    pub full_range: bool,
}

/// One plane of a frame. Planes are back to back in the frame's data: a plane starts where the one before ended.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Plane {
    /// Bytes from the start of one row to the next.
    pub stride: u32,
    /// Total bytes of the plane; 0 means the plane is unused.
    pub len: u32,
}

/// Size, format and plane positions of a frame.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FrameLayout {
    /// Width in pixels.
    pub width: u32,
    /// Height in pixels.
    pub height: u32,
    /// How the planes hold the samples.
    pub format: PixelFormat,
    /// How to convert the samples to RGB.
    pub color: ColorDesc,
    /// Up to three planes; unused ones have length 0.
    pub planes: [Plane; 3],
}

/// One decoded frame in transport-ready planes. Clone is a refcount.
#[derive(Clone, Debug)]
pub struct VideoFrame {
    id: FrameId,
    keyframe: bool,
    layout: FrameLayout,
    data: Bytes,
    duration: Option<MediaTime>,
}

impl VideoFrame {
    /// Builds a frame, checking that `data` is exactly the planes of `layout` back to back, and that every used
    /// plane is whole rows.
    pub fn new(
        id: FrameId,
        keyframe: bool,
        layout: FrameLayout,
        data: Bytes,
    ) -> Result<Self, UserError> {
        let mut total = 0usize;
        for (index, plane) in layout.planes.iter().enumerate() {
            if plane.len > 0 && (plane.stride == 0 || plane.len % plane.stride != 0) {
                return Err(UserError::new(
                    ErrorCode::DecodeFailed,
                    format!("plane {index} is not a whole number of rows"),
                ));
            }
            total = total.saturating_add(plane.len as usize);
        }
        if total != data.len() {
            return Err(UserError::new(
                ErrorCode::DecodeFailed,
                format!(
                    "the planes take {total} bytes but the frame has {}",
                    data.len()
                ),
            ));
        }
        Ok(Self {
            id,
            keyframe,
            layout,
            data,
            duration: None,
        })
    }

    /// Sets the duration the decoder reported for this frame.
    pub fn with_duration(mut self, duration: Option<MediaTime>) -> Self {
        self.duration = duration;
        self
    }

    /// The duration the decoder reported for this frame, when it did. This is the source's own word for the frame,
    /// distinct from the index's delay to the next frame.
    pub fn duration(&self) -> Option<MediaTime> {
        self.duration
    }

    /// Which frame of the video this is.
    pub fn id(&self) -> FrameId {
        self.id
    }

    /// Whether decoding can start at this frame.
    pub fn is_keyframe(&self) -> bool {
        self.keyframe
    }

    /// Size, format and planes.
    pub fn layout(&self) -> &FrameLayout {
        &self.layout
    }

    /// All planes back to back.
    pub fn data(&self) -> &Bytes {
        &self.data
    }

    /// The bytes of plane `index`; empty for an unused plane or an index past the three.
    pub fn plane(&self, index: usize) -> &[u8] {
        let Some(plane) = self.layout.planes.get(index) else {
            return &[];
        };
        let start: usize = self.layout.planes[..index]
            .iter()
            .map(|before| before.len as usize)
            .sum();
        &self.data[start..start + plane.len as usize]
    }

    /// Bytes this frame holds in memory, which is what a cache charges for it.
    pub fn reserved_bytes(&self) -> usize {
        self.data.len()
    }
}

/// One frame that could not be decoded; the stream stays usable.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FrameFailure {
    /// The frame that failed.
    pub sequence_id: u64,
    /// What went wrong, without the frame's number: the viewer names the frame itself.
    pub error: UserError,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::MediaTime;

    fn id() -> FrameId {
        FrameId {
            sequence_id: 3,
            timestamp: MediaTime::ZERO,
        }
    }

    fn layout(lens: [u32; 3]) -> FrameLayout {
        FrameLayout {
            width: 4,
            height: 2,
            format: PixelFormat::I420,
            color: ColorDesc {
                matrix: Matrix::Bt709,
                full_range: false,
            },
            planes: lens.map(|len| Plane {
                stride: if len > 0 { 2 } else { 0 },
                len,
            }),
        }
    }

    #[test]
    fn planes_are_back_to_back_and_the_data_must_be_exactly_their_sum() {
        let data = Bytes::from_static(&[1, 1, 1, 1, 2, 2, 3, 3]);
        let frame = VideoFrame::new(id(), true, layout([4, 2, 2]), data.clone()).unwrap();
        assert_eq!(frame.plane(0), [1, 1, 1, 1]);
        assert_eq!(frame.plane(1), [2, 2]);
        assert_eq!(frame.plane(2), [3, 3]);
        assert!(frame.plane(3).is_empty());
        assert_eq!(frame.reserved_bytes(), 8);
        assert_eq!(frame.duration(), None);
        let lasting = frame.with_duration(Some(MediaTime::from_us(40_000)));
        assert_eq!(lasting.duration(), Some(MediaTime::from_us(40_000)));

        assert!(VideoFrame::new(id(), false, layout([4, 2, 4]), data.clone()).is_err());
        assert!(VideoFrame::new(id(), false, layout([4, 2, 0]), data.clone()).is_err());
        assert!(
            VideoFrame::new(id(), false, layout([3, 2, 3]), data).is_err(),
            "not whole rows"
        );
    }
}
