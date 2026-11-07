//! Pictures as JPEGs, shrunk to a long side of at most an edge: a video's first picture as a thumbnail, and a decoded
//! frame for a viewer on another machine, where its planes would not fit through the network.

use crate::encoder::{coded, copy_plane, fail};
use crate::ffi::Scaler;
use ffmpeg_next::codec::{self, Flags};
use ffmpeg_next::color::Range;
use ffmpeg_next::format::Pixel;
use ffmpeg_next::software::scaling::Flags as ScaleFlags;
use ffmpeg_next::{Error, Packet, Rational, encoder, ffi, frame};
use foundation::{Matrix, PixelFormat, UserError, VideoFrame};

/// The JPEG quantiser scale: 4 gives about the size and look of libjpeg quality 85.
const JPEG_QSCALE: i32 = 4;

/// A picture as a JPEG.
#[derive(Clone, Debug)]
pub struct Jpeg {
    /// The encoded JPEG.
    pub bytes: Vec<u8>,
    /// Width of the picture in pixels.
    pub width: u32,
    /// Height of the picture in pixels.
    pub height: u32,
}

/// `frame` shrunk so its long side is at most `edge` (never enlarged, keeping the aspect ratio) and encoded as a JPEG.
pub fn frame_jpeg(frame: &VideoFrame, edge: u32) -> Result<Jpeg, UserError> {
    let layout = frame.layout();
    let (pixel, planes) = match layout.format {
        PixelFormat::I420 => (Pixel::YUV420P, 3),
        PixelFormat::Nv12 => (Pixel::NV12, 2),
        PixelFormat::Rgba => (Pixel::RGBA, 1),
    };
    let mut picture = frame::Video::new(pixel, layout.width, layout.height);
    for index in 0..planes {
        // A frame's planes are tightly packed, so a row is its stride.
        let stride = layout.planes[index].stride;
        let rows = layout.planes[index]
            .len
            .checked_div(stride)
            .filter(|rows| *rows > 0)
            .ok_or_else(|| fail("the frame has an empty plane"))?;
        copy_plane(
            &mut picture,
            index,
            (frame.plane(index), stride as usize),
            (stride, rows),
        )
        .map_err(|error| fail(format!("the frame does not match its layout: {error}")))?;
    }
    let size = fitted((layout.width, layout.height), edge);
    let small = shrink(
        &picture,
        (layout.color.matrix, layout.color.full_range),
        size,
    )
    .map_err(coded)?;
    Ok(Jpeg {
        bytes: encode(&small)?,
        width: size.0,
        height: size.1,
    })
}

/// `size` shrunk so its long side is at most `edge`, in proportion and at least one pixel per side.
pub fn fitted((width, height): (u32, u32), edge: u32) -> (u32, u32) {
    let long = width.max(height);
    if long <= edge {
        return (width, height);
    }
    let shrink = |side: u32| {
        let scaled = (u64::from(side) * u64::from(edge) + u64::from(long) / 2) / u64::from(long);
        u32::try_from(scaled).unwrap_or(side).max(1)
    };
    (shrink(width), shrink(height))
}

/// `picture`, whose YUV samples follow `matrix` and span the full range when `full_range` says so, scaled by area to
/// `size` as full-range BT.601 YUV 4:2:0, which is what JPEG holds and viewers assume.
pub(crate) fn shrink(
    picture: &frame::Video,
    (matrix, full_range): (Matrix, bool),
    size: (u32, u32),
) -> Result<frame::Video, Error> {
    let mut scaler = Scaler::resizing(
        (picture.format(), (picture.width(), picture.height())),
        (Pixel::YUVJ420P, size),
        ScaleFlags::AREA,
        (matrix, Matrix::Bt601),
        (full_range, true),
    )?;
    let mut small = frame::Video::empty();
    scaler.run(picture, &mut small)?;
    small.set_pts(Some(0));
    Ok(small)
}

/// `picture`, full-range YUV 4:2:0, as a JPEG.
pub(crate) fn encode(picture: &frame::Video) -> Result<Vec<u8>, UserError> {
    let codec =
        encoder::find(codec::Id::MJPEG).ok_or_else(|| fail("this FFmpeg has no JPEG encoder"))?;
    let mut video = codec::context::Context::new_with_codec(codec)
        .encoder()
        .video()
        .map_err(coded)?;
    video.set_width(picture.width());
    video.set_height(picture.height());
    video.set_format(Pixel::YUVJ420P);
    video.set_color_range(Range::JPEG);
    video.set_time_base(Rational(1, 1));
    video.set_flags(Flags::QSCALE);
    video.set_global_quality(JPEG_QSCALE * ffi::FF_QP2LAMBDA);
    let mut video = video.open_as(codec).map_err(coded)?;
    video.send_frame(picture).map_err(coded)?;
    video.send_eof().map_err(coded)?;
    let mut packet = Packet::empty();
    video.receive_packet(&mut packet).map_err(coded)?;
    Ok(packet
        .data()
        .ok_or_else(|| fail("the JPEG is empty"))?
        .to_vec())
}
