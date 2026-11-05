//! The first picture of a video, as a small JPEG, read without an index.
//!
//! Building a [`FrameIndex`](crate::FrameIndex) scans every packet of the file, which is far more than a thumbnail
//! is worth, so this opens the container directly and decodes until one frame comes out. A stream that does not
//! start on a keyframe produces none for a while, so reading goes on until a frame or the end of the file; the
//! caller's [`CancelToken`] is what bounds the cost.

use crate::convert::color_desc;
use crate::encoder::{coded, fail};
use crate::ffi::Scaler;
use crate::fingerprint::Fingerprint;
use crate::input::VideoInput;
use crate::source::{self, Opened};
use ffmpeg_next::codec::{self, Flags};
use ffmpeg_next::color::Range;
use ffmpeg_next::error::EAGAIN;
use ffmpeg_next::format::Pixel;
use ffmpeg_next::software::scaling::Flags as ScaleFlags;
use ffmpeg_next::{Dictionary, Error, Packet, Rational, decoder, encoder, ffi, frame};
use foundation::{CancelToken, ErrorCode, Interrupt, Matrix, UserError};
use xxhash_rust::xxh3::Xxh3;

/// The JPEG quantiser scale: 4 gives about the size and look of libjpeg quality 85.
const JPEG_QSCALE: i32 = 4;

/// A first frame as a JPEG.
#[derive(Clone, Debug)]
pub struct FirstFrame {
    /// The encoded JPEG.
    pub jpeg: Vec<u8>,
    /// Width of the picture in pixels.
    pub width: u32,
    /// Height of the picture in pixels.
    pub height: u32,
}

/// A number that is equal while the first frame's source is unchanged: the video file, or for an image sequence its
/// first image together with every setting of the sequence (pattern, start number, frame rate and frame count), as
/// Python's cache key does. Like the frame index's cache check, it notices size, times and both ends of the file,
/// not an edit in the middle that keeps those.
pub fn first_frame_identity(input: &VideoInput) -> Result<u64, UserError> {
    let fingerprint = Fingerprint::of_input(input)
        .map(|fingerprint| fingerprint.digest())
        .map_err(|reason| {
            source::error(
                ErrorCode::FileUnreadable,
                format!("The video cannot be identified: {reason}"),
                input.location(),
            )
        })?;
    let VideoInput::ImageSequence(sequence) = input else {
        return Ok(fingerprint);
    };
    let mut hash = Xxh3::new();
    hash.update(&fingerprint.to_le_bytes());
    hash.update(sequence.pattern.as_os_str().as_encoded_bytes());
    hash.update(&sequence.start_number.to_le_bytes());
    hash.update(&sequence.frame_rate.num().to_le_bytes());
    hash.update(&sequence.frame_rate.den().to_le_bytes());
    hash.update(&sequence.frame_count.to_le_bytes());
    Ok(hash.digest())
}

/// Decodes the first picture of `input`, shrinks it to a long side of at most `edge` pixels (never enlarging it,
/// keeping the aspect ratio) and encodes it as a JPEG. Fails when the file has no frame to decode.
pub fn first_frame(
    input: &VideoInput,
    edge: u32,
    cancel: &CancelToken,
) -> Result<FirstFrame, Interrupt<UserError>> {
    let path = input.location();
    if edge == 0 {
        return Err(Interrupt::Failed(fail("a thumbnail needs a size above 0")));
    }
    let mut opened = source::open_input(input, cancel)?;
    let mut codec = source::open_decoder(&opened, 1, path).map_err(Interrupt::Failed)?;
    let picture =
        decode_first(&mut opened, &mut codec, cancel).map_err(|failure| match failure {
            Interrupt::Cancelled => Interrupt::Cancelled,
            Interrupt::Failed(reason) => {
                Interrupt::Failed(source::error(ErrorCode::UnsupportedFormat, reason, path))
            }
        })?;
    cancel.check()?;
    let (width, height) = fitted((picture.width(), picture.height()), edge);
    let small = scale(&picture, (width, height)).map_err(|reason| {
        Interrupt::Failed(source::error(ErrorCode::UnsupportedFormat, reason, path))
    })?;
    let jpeg = encode(&small).map_err(Interrupt::Failed)?;
    Ok(FirstFrame {
        jpeg,
        width,
        height,
    })
}

/// The first frame the codec produces, or why there is none.
fn decode_first(
    opened: &mut Opened,
    codec: &mut decoder::Video,
    cancel: &CancelToken,
) -> Result<frame::Video, Interrupt<String>> {
    let mut last_error = None;
    loop {
        cancel.check()?;
        let mut packet = Packet::empty();
        match packet.read(&mut opened.input) {
            Ok(()) => {}
            Err(Error::Eof) => break,
            Err(error) => {
                cancel.check()?;
                return Err(Interrupt::Failed(format!(
                    "The video cannot be read: {error}"
                )));
            }
        }
        if packet.stream() != opened.stream {
            continue;
        }
        // A damaged packet costs only itself; a later one can still decode.
        if let Err(error) = codec.send_packet(&packet) {
            last_error = Some(error);
        }
        match receive(codec) {
            Ok(Some(picture)) => return Ok(picture),
            Ok(None) => {}
            Err(error) => last_error = Some(error),
        }
    }
    // The codec may hold the frame until it is told no more packets come.
    if codec.send_eof().is_ok()
        && let Ok(Some(picture)) = receive(codec)
    {
        return Ok(picture);
    }
    Err(Interrupt::Failed(match last_error {
        Some(error) => format!("The video has no picture to show: {error}"),
        None => "The video has no picture to show".to_string(),
    }))
}

/// The most bytes a cached thumbnail may have. A JPEG fitted to a few hundred pixels is far smaller; a larger file is
/// not one this module wrote, and is not read or decoded.
pub const MAX_JPEG_BYTES: u64 = 4 * 1024 * 1024;

/// Whether `bytes` are a JPEG of a thumbnail at `edge` that decodes without error: at most [`MAX_JPEG_BYTES`], with
/// its start (`FFD8`) and end (`FFD9`) markers, which a picture cut short by a crash or a full disk lacks, and a
/// picture no larger than `edge` by `edge` pixels that a decoder reads whole, which damage inside the file stops.
/// The cache folder is not trusted, so the size limits apply before anything is allocated for the picture.
pub fn whole_jpeg(bytes: &[u8], edge: u32) -> bool {
    u64::try_from(bytes.len()).is_ok_and(|len| len <= MAX_JPEG_BYTES)
        && bytes.starts_with(&[0xFF, 0xD8])
        && bytes.ends_with(&[0xFF, 0xD9])
        && decodes_cleanly(bytes, edge).is_ok()
}

/// Decodes the JPEG `bytes`; an error when the decoder fails, flags damage in the picture, finds it larger than
/// `edge` by `edge` pixels, or gives none.
fn decodes_cleanly(bytes: &[u8], edge: u32) -> Result<(), Error> {
    let codec = decoder::find(codec::Id::MJPEG).ok_or(Error::DecoderNotFound)?;
    // The decoder hides damage in the picture data by default; `explode` makes it fail instead. `max_pixels` makes
    // it refuse a larger picture from its header, before it allocates for it.
    let mut options = Dictionary::new();
    options.set("err_detect", "explode");
    options.set(
        "max_pixels",
        &(u64::from(edge) * u64::from(edge)).to_string(),
    );
    let mut video = codec::context::Context::new_with_codec(codec)
        .decoder()
        .open_as_with(codec, options)?
        .video()?;
    video.send_packet(&Packet::copy(bytes))?;
    video.send_eof()?;
    let picture = receive(&mut video)?.ok_or(Error::InvalidData)?;
    if picture.is_corrupt() || picture.has_decode_errors() {
        return Err(Error::InvalidData);
    }
    Ok(())
}

/// The frame the codec has ready, `None` when it needs more packets.
fn receive(codec: &mut decoder::Video) -> Result<Option<frame::Video>, Error> {
    let mut picture = frame::Video::empty();
    match codec.receive_frame(&mut picture) {
        Ok(()) => Ok(Some(picture)),
        Err(Error::Eof) => Ok(None),
        Err(Error::Other { errno }) if errno == EAGAIN => Ok(None),
        Err(error) => Err(error),
    }
}

/// `size` shrunk so its long side is at most `edge`, in proportion and at least one pixel per side.
fn fitted((width, height): (u32, u32), edge: u32) -> (u32, u32) {
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

/// `picture` scaled by area to `size`, as full-range BT.601 YUV 4:2:0, which is what JPEG holds and viewers assume.
fn scale(picture: &frame::Video, size: (u32, u32)) -> Result<frame::Video, String> {
    let source = picture.format();
    let color = color_desc(
        picture.color_space(),
        picture.color_range(),
        source,
        picture.height(),
    );
    let mut scaler = Scaler::resizing(
        (source, (picture.width(), picture.height())),
        (Pixel::YUVJ420P, size),
        ScaleFlags::AREA,
        (color.matrix, Matrix::Bt601),
        (color.full_range, true),
    )
    .map_err(|error| format!("The picture cannot be scaled: {error}"))?;
    let mut small = frame::Video::empty();
    scaler
        .run(picture, &mut small)
        .map_err(|error| format!("The picture cannot be scaled: {error}"))?;
    small.set_pts(Some(0));
    Ok(small)
}

/// `picture` as a JPEG.
fn encode(picture: &frame::Video) -> Result<Vec<u8>, UserError> {
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
