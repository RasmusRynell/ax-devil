//! Pictures to a file: an H.264 MP4 whose frames keep the times they are given, or a single PNG still.
//!
//! The encoder is probed at run time: `libx264`, else `libopenh264`, else `h264_videotoolbox` (macOS), so a build of
//! FFmpeg with none of them fails here with a message naming them. The MP4 has no B-frames and a microsecond time base, so
//! every frame's time is stored exactly and a variable frame rate survives.

use ffmpeg_next::codec::{self, Flags};
use ffmpeg_next::color::{Primaries, Range, Space, TransferCharacteristic};
use ffmpeg_next::error::EAGAIN;
use ffmpeg_next::format::context::Output;
use ffmpeg_next::format::{self, Pixel};
use ffmpeg_next::util::log::{self, Level};
use ffmpeg_next::{Dictionary, Error, Packet, Rational, encoder, frame};
use foundation::{ErrorCode, UserError};
use std::collections::VecDeque;
use std::path::{Path, PathBuf};

use crate::part::PartFile;

/// Ticks of the encoder's and the file's clock: one microsecond.
const TIME_BASE: Rational = Rational(1, 1_000_000);
/// What rate control is told when the real rate is unknown (it varies), and the key frame interval in frames.
const NOMINAL_FRAME_RATE: i32 = 30;
const KEYFRAME_INTERVAL: u32 = 60;
/// H.264 levels cannot signal a bit rate past this, and `bit_rate` is a 32-bit count in some encoders.
const MAX_BIT_RATE: u64 = 1 << 30;
/// The longest side of a picture, which also keeps every size calculation far from overflowing.
const MAX_SIDE: u32 = 16_384;

/// How much picture quality an MP4 keeps, against its size.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Quality {
    /// No loss (x264 CRF 0). The file is large and some players cannot play it.
    Lossless,
    /// Close to the source.
    High,
    /// A good picture at a moderate size.
    Balanced,
    /// The smallest file that still looks reasonable.
    Small,
}

impl Quality {
    /// x264's constant rate factor: lower keeps more.
    fn crf(self) -> &'static str {
        match self {
            Self::Lossless => "0",
            Self::High => "18",
            Self::Balanced => "23",
            Self::Small => "28",
        }
    }

    /// x264's speed preset: lossless needs no search, a small file gets a little more.
    fn preset(self) -> &'static str {
        match self {
            Self::Lossless => "ultrafast",
            Self::High | Self::Balanced => "veryfast",
            Self::Small => "fast",
        }
    }

    /// Bits per pixel per frame at the nominal frame rate, for an encoder that is only given a bit rate.
    /// OpenH264 has no lossless mode; `Lossless` gets the most it is given here.
    fn bits_per_pixel(self) -> f64 {
        match self {
            Self::Lossless => 0.8,
            Self::High => 0.2,
            Self::Balanced => 0.1,
            Self::Small => 0.05,
        }
    }
}

/// What a file holds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EncodeFormat {
    /// An H.264 video in an MP4 with the index up front, fed [`Picture::I420`].
    Mp4 {
        /// The quality to keep.
        quality: Quality,
    },
    /// One still PNG, fed one [`Picture::Rgba`].
    Png,
}

/// One picture to encode, rows top-down. A stride is the distance between rows in bytes and may exceed the row.
#[derive(Clone, Copy, Debug)]
pub enum Picture<'a> {
    /// 8-bit 4:2:0 planes, BT.709 limited range, of an even width and height.
    I420 {
        /// The luma plane.
        y: &'a [u8],
        /// The blue-difference chroma plane, half the width and height.
        u: &'a [u8],
        /// The red-difference chroma plane, half the width and height.
        v: &'a [u8],
        /// Bytes between luma rows.
        y_stride: usize,
        /// Bytes between chroma rows of both chroma planes.
        c_stride: usize,
    },
    /// Packed 8-bit R, G, B, A samples.
    Rgba {
        /// The samples.
        data: &'a [u8],
        /// Bytes between rows.
        stride: usize,
    },
}

/// Writes pictures to the file `destination`.
///
/// The pictures go to a new file beside `destination` (see [`PartFile`]), which [`finish`](Self::finish) renames over
/// `destination`. Dropping the encoder before `finish` succeeds removes that file and leaves `destination` as it was.
pub struct Encoder {
    destination: PathBuf,
    part: PartFile,
    size: (u32, u32),
    sink: Sink,
}

/// What the pictures go to.
enum Sink {
    Mp4(Box<Mp4>),
    Png(Box<Png>),
}

impl Encoder {
    /// Starts a `width` x `height` file of `format` that will replace `destination`. An MP4 needs an even width and
    /// height.
    pub fn create(
        destination: &Path,
        format: EncodeFormat,
        width: u32,
        height: u32,
    ) -> Result<Self, UserError> {
        log::set_level(Level::Quiet);
        check_size(format, width, height)?;
        let part = PartFile::create(destination)?;
        let sink = match format {
            EncodeFormat::Mp4 { quality } => Sink::Mp4(Box::new(Mp4::open(
                &part.writable_path()?,
                quality,
                width,
                height,
            )?)),
            EncodeFormat::Png => Sink::Png(Box::new(Png::open(width, height)?)),
        };
        Ok(Self {
            destination: destination.to_path_buf(),
            part,
            size: (width, height),
            sink,
        })
    }

    /// Adds `picture`, shown from `pts_us` for `dur_us` microseconds. An MP4 takes pictures in order with
    /// strictly increasing times, and its first picture is time zero whatever `pts_us` it has; a PNG takes one.
    pub fn write(
        &mut self,
        picture: Picture<'_>,
        pts_us: i64,
        dur_us: i64,
    ) -> Result<(), UserError> {
        let (width, height) = self.size;
        match &mut self.sink {
            Sink::Mp4(mp4) => {
                let Picture::I420 {
                    y,
                    u,
                    v,
                    y_stride,
                    c_stride,
                } = picture
                else {
                    return Err(fail("a video takes I420 pictures"));
                };
                let planes = [
                    (y, y_stride, width, height),
                    (u, c_stride, width / 2, height / 2),
                    (v, c_stride, width / 2, height / 2),
                ];
                mp4.write(planes, pts_us, dur_us).map_err(coded)
            }
            Sink::Png(png) => {
                let Picture::Rgba { data, stride } = picture else {
                    return Err(fail("a still takes an RGBA picture"));
                };
                png.write(data, stride, width, height).map_err(coded)
            }
        }
    }

    /// Flushes the encoder and puts the finished file in place of the destination. Nothing more can be written.
    pub fn finish(self) -> Result<(), UserError> {
        let Self {
            destination,
            mut part,
            sink,
            ..
        } = self;
        match sink {
            Sink::Mp4(mut mp4) => {
                mp4.finish().map_err(coded)?;
                // Closes the file before it is renamed.
                drop(mp4);
            }
            Sink::Png(png) => part.write_all(png.bytes()?)?,
        }
        part.persist(&destination)
    }
}

/// Refuses a size `format` cannot be encoded at.
fn check_size(format: EncodeFormat, width: u32, height: u32) -> Result<(), UserError> {
    let in_range = |side: u32| (1..=MAX_SIDE).contains(&side);
    match format {
        EncodeFormat::Mp4 { .. }
            if !in_range(width)
                || !in_range(height)
                || !width.is_multiple_of(2)
                || !height.is_multiple_of(2) =>
        {
            Err(fail(format!(
                "a {width}x{height} video cannot be encoded: both sides must be even and at most {MAX_SIDE}"
            )))
        }
        EncodeFormat::Png if !in_range(width) || !in_range(height) => Err(fail(format!(
            "a {width}x{height} picture cannot be encoded: each side must be 1 to {MAX_SIDE}"
        ))),
        _ => Ok(()),
    }
}

/// An error that is the encoder's own.
pub(crate) fn fail(reason: impl Into<String>) -> UserError {
    UserError::new(ErrorCode::Internal, reason)
}

/// An FFmpeg error as a user error.
pub(crate) fn coded(error: Error) -> UserError {
    fail(format!("encoding failed: {error}"))
}

/// Copies `rows` rows of `row_len` bytes from `source` (rows `stride` apart) into the frame plane `index`, after
/// checking that `source` holds them.
pub(crate) fn copy_plane(
    target: &mut frame::Video,
    index: usize,
    (source, stride): (&[u8], usize),
    (row_len, rows): (u32, u32),
) -> Result<(), Error> {
    let (row_len, rows) = (row_len as usize, rows as usize);
    let needed = stride.saturating_mul(rows - 1).saturating_add(row_len);
    if stride < row_len || source.len() < needed {
        return Err(Error::InvalidData);
    }
    let target_stride = target.stride(index);
    let destination = target.data_mut(index);
    for row in 0..rows {
        destination[row * target_stride..][..row_len]
            .copy_from_slice(&source[row * stride..][..row_len]);
    }
    Ok(())
}

/// The H.264 encoder and the MP4 it feeds.
struct Mp4 {
    output: Output,
    encoder: encoder::Video,
    stream_time_base: Rational,
    /// Durations of the pictures sent and not yet returned as packets, in order: with no B-frames packets come out
    /// in the order pictures went in.
    durations: VecDeque<i64>,
    /// Source time of the first picture and of the latest one.
    first_pts: Option<i64>,
    last_pts: Option<i64>,
}

impl Mp4 {
    fn open(tmp: &Path, quality: Quality, width: u32, height: u32) -> Result<Self, UserError> {
        let (codec, name) = ["libx264", "libopenh264", "h264_videotoolbox"]
            .into_iter()
            .find_map(|name| encoder::find_by_name(name).map(|codec| (codec, name)))
            .ok_or_else(|| {
                fail("this FFmpeg has no H.264 encoder: it needs libx264, libopenh264 or h264_videotoolbox")
            })?;
        let mut video = codec::context::Context::new_with_codec(codec)
            .encoder()
            .video()
            .map_err(coded)?;
        video.set_width(width);
        video.set_height(height);
        video.set_format(Pixel::YUV420P);
        video.set_time_base(TIME_BASE);
        video.set_frame_rate(Some(Rational(NOMINAL_FRAME_RATE, 1)));
        video.set_gop(KEYFRAME_INTERVAL);
        video.set_max_b_frames(0);
        video.set_colorspace(Space::BT709);
        video.set_color_range(Range::MPEG);
        video.set_color_primaries(Primaries::BT709);
        video.set_color_transfer_characteristic(TransferCharacteristic::BT709);
        video.set_flags(Flags::GLOBAL_HEADER);
        let mut options = Dictionary::new();
        if name == "libx264" {
            options.set("preset", quality.preset());
            options.set("crf", quality.crf());
        } else {
            let pixels_per_second =
                u64::from(width) * u64::from(height) * NOMINAL_FRAME_RATE as u64;
            let bits = (pixels_per_second as f64 * quality.bits_per_pixel()) as u64;
            video.set_bit_rate(bits.min(MAX_BIT_RATE) as usize);
        }
        let opened = video
            .open_as_with(codec, options)
            .map_err(|error| fail(format!("the {name} encoder did not start: {error}")))?;

        let mut output = format::output_as(tmp, "mp4")
            .map_err(|error| fail(format!("the video file could not be created: {error}")))?;
        {
            let mut stream = output.add_stream(codec).map_err(coded)?;
            stream.set_time_base(TIME_BASE);
            stream.set_parameters(&opened);
        }
        let mut header = Dictionary::new();
        header.set("movflags", "+faststart");
        output.write_header_with(header).map_err(coded)?;
        let stream_time_base = output
            .stream(0)
            .map_or(TIME_BASE, |stream| stream.time_base());
        Ok(Self {
            output,
            encoder: opened,
            stream_time_base,
            durations: VecDeque::new(),
            first_pts: None,
            last_pts: None,
        })
    }

    fn write(
        &mut self,
        planes: [(&[u8], usize, u32, u32); 3],
        pts_us: i64,
        dur_us: i64,
    ) -> Result<(), Error> {
        if dur_us <= 0 || self.last_pts.is_some_and(|last| pts_us <= last) {
            return Err(Error::InvalidData);
        }
        let mut picture =
            frame::Video::new(Pixel::YUV420P, self.encoder.width(), self.encoder.height());
        for (index, (source, stride, width, height)) in planes.into_iter().enumerate() {
            copy_plane(&mut picture, index, (source, stride), (width, height))?;
        }
        let first = *self.first_pts.get_or_insert(pts_us);
        picture.set_pts(Some(pts_us - first));
        self.encoder.send_frame(&picture)?;
        self.last_pts = Some(pts_us);
        self.durations.push_back(dur_us);
        self.drain()
    }

    fn finish(&mut self) -> Result<(), Error> {
        if self.first_pts.is_none() {
            return Err(Error::InvalidData);
        }
        self.encoder.send_eof()?;
        self.drain()?;
        self.output.write_trailer()
    }

    /// Writes every packet the encoder has ready.
    fn drain(&mut self) -> Result<(), Error> {
        loop {
            let mut packet = Packet::empty();
            match self.encoder.receive_packet(&mut packet) {
                Ok(()) => {
                    packet.set_stream(0);
                    packet.set_duration(self.durations.pop_front().ok_or(Error::Bug)?);
                    packet.rescale_ts(TIME_BASE, self.stream_time_base);
                    packet.write_interleaved(&mut self.output)?;
                }
                Err(Error::Eof) => return Ok(()),
                Err(Error::Other { errno }) if errno == EAGAIN => return Ok(()),
                Err(error) => return Err(error),
            }
        }
    }
}

/// The PNG encoder and the one still it made.
struct Png {
    encoder: encoder::Video,
    bytes: Option<Vec<u8>>,
}

impl Png {
    fn open(width: u32, height: u32) -> Result<Self, UserError> {
        let codec =
            encoder::find(codec::Id::PNG).ok_or_else(|| fail("this FFmpeg has no PNG encoder"))?;
        let mut video = codec::context::Context::new_with_codec(codec)
            .encoder()
            .video()
            .map_err(coded)?;
        video.set_width(width);
        video.set_height(height);
        video.set_format(Pixel::RGBA);
        video.set_time_base(Rational(1, 1));
        let encoder = video.open_as(codec).map_err(coded)?;
        Ok(Self {
            encoder,
            bytes: None,
        })
    }

    fn write(&mut self, data: &[u8], stride: usize, width: u32, height: u32) -> Result<(), Error> {
        if self.bytes.is_some() {
            return Err(Error::InvalidData);
        }
        let mut picture = frame::Video::new(Pixel::RGBA, width, height);
        copy_plane(&mut picture, 0, (data, stride), (width * 4, height))?;
        picture.set_pts(Some(0));
        self.encoder.send_frame(&picture)?;
        self.encoder.send_eof()?;
        let mut packet = Packet::empty();
        self.encoder.receive_packet(&mut packet)?;
        self.bytes = Some(packet.data().ok_or(Error::InvalidData)?.to_vec());
        Ok(())
    }

    /// The encoded still.
    fn bytes(&self) -> Result<&[u8], UserError> {
        self.bytes
            .as_deref()
            .ok_or_else(|| fail("no picture was written"))
    }
}

#[cfg(test)]
mod tests {
    use super::Quality;

    #[test]
    fn each_quality_has_its_rate_factor_and_speed_preset() {
        let table = [
            (Quality::Lossless, "0", "ultrafast"),
            (Quality::High, "18", "veryfast"),
            (Quality::Balanced, "23", "veryfast"),
            (Quality::Small, "28", "fast"),
        ];
        for (quality, crf, preset) in table {
            assert_eq!((quality.crf(), quality.preset()), (crf, preset));
        }
    }
}
