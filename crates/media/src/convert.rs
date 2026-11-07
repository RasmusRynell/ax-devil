//! Decoded FFmpeg frames to transport-ready frames: which pixel layout a source format is delivered in, where its
//! planes sit, and which YUV matrix and range the receiver must use.

use crate::ffi::Scaler;
use bytes::Bytes;
use ffmpeg_next::color::{Range, Space};
use ffmpeg_next::format::Pixel;
use ffmpeg_next::frame::Video;
use ffmpeg_next::software::scaling::Flags;
use foundation::{ColorDesc, FrameId, FrameLayout, Matrix, PixelFormat, Plane, VideoFrame};

/// How a source pixel format reaches the wire: its layout, and the swscale target when it needs converting.
struct Delivery {
    format: PixelFormat,
    via: Option<Pixel>,
}

/// 8-bit 4:2:0 passes through as I420 (NV12 as NV12), deeper 4:2:0 becomes 8-bit I420, and everything else
/// becomes RGBA, so chroma that is not subsampled the 4:2:0 way stays exact.
fn delivery(source: Pixel) -> Delivery {
    let (format, via) = match source {
        Pixel::YUV420P | Pixel::YUVJ420P => (PixelFormat::I420, None),
        Pixel::NV12 => (PixelFormat::Nv12, None),
        Pixel::YUV420P9LE
        | Pixel::YUV420P9BE
        | Pixel::YUV420P10LE
        | Pixel::YUV420P10BE
        | Pixel::YUV420P12LE
        | Pixel::YUV420P12BE
        | Pixel::YUV420P14LE
        | Pixel::YUV420P14BE
        | Pixel::YUV420P16LE
        | Pixel::YUV420P16BE
        | Pixel::P010LE
        | Pixel::P010BE
        | Pixel::P012LE
        | Pixel::P012BE
        | Pixel::P016LE
        | Pixel::P016BE => (PixelFormat::I420, Some(Pixel::YUV420P)),
        _ => (PixelFormat::Rgba, Some(Pixel::RGBA)),
    };
    Delivery { format, via }
}

/// The layout a source pixel format is delivered in.
pub(crate) fn delivered_format(source: Pixel) -> PixelFormat {
    delivery(source).format
}

/// Matrix and range the receiver must use for a source.
///
/// BT.709 and BT.601 (BT.470 BG, SMPTE 170M) are taken from the stream. Anything else, including unspecified,
/// follows the usual convention: BT.709 from 720 lines up, BT.601 below. The range is full when the stream says so
/// or the format is a JPEG-range one (`yuvj420p`), limited otherwise. RGBA output ignores both.
pub(crate) fn color_desc(space: Space, range: Range, source: Pixel, height: u32) -> ColorDesc {
    let matrix = match space {
        Space::BT709 => Matrix::Bt709,
        Space::BT470BG | Space::SMPTE170M => Matrix::Bt601,
        _ if height >= 720 => Matrix::Bt709,
        _ => Matrix::Bt601,
    };
    ColorDesc {
        matrix,
        full_range: range == Range::JPEG || source == Pixel::YUVJ420P,
    }
}

/// Planes of a tightly packed frame, and the total bytes. `None` when a size overflows.
///
/// Rows have no padding: a stride is the row's bytes. Chroma of odd sizes rounds up.
pub(crate) fn plane_layout(
    format: PixelFormat,
    width: u32,
    height: u32,
) -> Option<([Plane; 3], usize)> {
    let (chroma_width, chroma_height) = (width.div_ceil(2), height.div_ceil(2));
    let unused = Plane { stride: 0, len: 0 };
    let rows: [(u32, u32); 3] = match format {
        PixelFormat::I420 => [
            (width, height),
            (chroma_width, chroma_height),
            (chroma_width, chroma_height),
        ],
        PixelFormat::Nv12 => [
            (width, height),
            (chroma_width.checked_mul(2)?, chroma_height),
            (0, 0),
        ],
        PixelFormat::Rgba => [(width.checked_mul(4)?, height), (0, 0), (0, 0)],
    };
    let mut planes = [unused; 3];
    let mut total = 0u32;
    for (plane, (stride, height)) in planes.iter_mut().zip(rows) {
        let len = stride.checked_mul(height)?;
        if len > 0 {
            *plane = Plane { stride, len };
        }
        total = total.checked_add(len)?;
    }
    Some((planes, usize::try_from(total).ok()?))
}

/// Turns decoded frames into transport frames, keeping its swscale context while the source does not change.
#[derive(Default)]
pub(crate) struct Converter {
    scaler: Option<(ScalerKey, Scaler)>,
}

/// What a swscale context was made for.
#[derive(PartialEq)]
struct ScalerKey {
    source: Pixel,
    size: (u32, u32),
    color: ColorDesc,
}

impl Converter {
    /// Packs `frame`, converting its pixel format first when the delivery rule says so.
    pub(crate) fn convert(
        &mut self,
        frame: &Video,
        id: FrameId,
        keyframe: bool,
    ) -> Result<VideoFrame, String> {
        let (width, height) = (frame.width(), frame.height());
        let source = frame.format();
        let color = color_desc(frame.color_space(), frame.color_range(), source, height);
        let delivery = delivery(source);
        let layout_error = || format!("a {width}x{height} frame is too large to deliver");
        let (planes, total) =
            plane_layout(delivery.format, width, height).ok_or_else(layout_error)?;
        let layout = FrameLayout {
            width,
            height,
            format: delivery.format,
            color,
            planes,
        };
        let mut data = Vec::with_capacity(total);
        match delivery.via {
            None => pack(frame, &layout, &mut data)?,
            Some(target) => {
                let mut converted = Video::empty();
                self.scaler(source, target, color, (width, height), delivery.format)?
                    .run(frame, &mut converted)
                    .map_err(|error| format!("the frame could not be converted: {error}"))?;
                pack(&converted, &layout, &mut data)?;
            }
        }
        VideoFrame::new(id, keyframe, layout, Bytes::from(data)).map_err(|error| error.reason)
    }

    fn scaler(
        &mut self,
        source: Pixel,
        target: Pixel,
        color: ColorDesc,
        size: (u32, u32),
        format: PixelFormat,
    ) -> Result<&mut Scaler, String> {
        let key = ScalerKey {
            source,
            size,
            color,
        };
        if self
            .scaler
            .as_ref()
            .is_none_or(|(made_for, _)| *made_for != key)
        {
            let flags = match format {
                PixelFormat::Rgba => Flags::BILINEAR | Flags::FULL_CHR_H_INT | Flags::ACCURATE_RND,
                PixelFormat::I420 | PixelFormat::Nv12 => Flags::BILINEAR,
            };
            let scaler =
                Scaler::new(source, target, size, flags, color.matrix, color.full_range)
                    .map_err(|error| format!("the pixel format cannot be converted: {error}"))?;
            self.scaler = Some((key, scaler));
        }
        match self.scaler.as_mut() {
            Some((_, scaler)) => Ok(scaler),
            None => Err("the pixel format cannot be converted".to_string()),
        }
    }
}

/// Copies the rows of `frame`, whose planes already have the delivered format, tightly into `data`.
fn pack(frame: &Video, layout: &FrameLayout, data: &mut Vec<u8>) -> Result<(), String> {
    for (index, plane) in layout.planes.iter().enumerate() {
        if plane.len == 0 {
            continue;
        }
        if index >= frame.planes() {
            return Err(format!("the frame has no plane {index}"));
        }
        let (stride, row_bytes) = (frame.stride(index), plane.stride as usize);
        let rows = (plane.len / plane.stride) as usize;
        let source = frame.data(index);
        if stride < row_bytes || source.len() < stride * (rows - 1) + row_bytes {
            return Err(format!(
                "plane {index} of the frame is smaller than expected"
            ));
        }
        for row in 0..rows {
            let start = row * stride;
            data.extend_from_slice(&source[start..start + row_bytes]);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use foundation::MediaTime;

    /// A frame whose sample at (x, y) of plane p is `value(p, x, y)`, in rows padded the way FFmpeg pads them.
    fn frame(
        format: Pixel,
        width: u32,
        height: u32,
        sample_bytes: usize,
        value: impl Fn(usize, usize, usize) -> u8,
    ) -> Video {
        let mut frame = Video::new(format, width, height);
        for plane in 0..frame.planes() {
            let (stride, rows) = (frame.stride(plane), frame.plane_height(plane) as usize);
            let chroma_pairs = if plane == 1 && format == Pixel::NV12 {
                2
            } else {
                1
            };
            let row_bytes = frame.plane_width(plane) as usize * chroma_pairs * sample_bytes;
            let data = frame.data_mut(plane);
            for y in 0..rows {
                for x in 0..stride {
                    // Padding gets a value no test pattern uses, to show it is not copied.
                    data[y * stride + x] = if x < row_bytes {
                        value(plane, x, y)
                    } else {
                        0xEE
                    };
                }
            }
        }
        frame
    }

    fn id() -> FrameId {
        FrameId {
            sequence_id: 0,
            timestamp: MediaTime::ZERO,
        }
    }

    fn pattern(plane: usize, x: usize, y: usize) -> u8 {
        (plane * 80 + x * 3 + y * 7) as u8
    }

    /// The tightly packed bytes of plane `p` of the pattern, `row_bytes` wide and `rows` high.
    fn expected_plane(plane: usize, row_bytes: usize, rows: usize) -> Vec<u8> {
        (0..rows)
            .flat_map(|y| (0..row_bytes).map(move |x| pattern(plane, x, y)))
            .collect()
    }

    #[test]
    fn eight_bit_420_is_copied_tightly_at_widths_16_and_18_whatever_the_padding_of_the_source() {
        for (width, height) in [(16u32, 16u32), (18, 16), (17, 15)] {
            let (cw, ch) = (width.div_ceil(2) as usize, height.div_ceil(2) as usize);
            let source = frame(Pixel::YUV420P, width, height, 1, pattern);
            assert!(
                source.stride(0) > width as usize || width % 32 == 0,
                "the source rows are padded"
            );

            let out = Converter::default()
                .convert(&source, id(), true)
                .expect("converts");

            let layout = *out.layout();
            assert_eq!(
                (layout.format, layout.planes[0].stride),
                (PixelFormat::I420, width)
            );
            assert_eq!(
                (layout.planes[1].stride, layout.planes[2].stride),
                (cw as u32, cw as u32)
            );
            let total = width as usize * height as usize + 2 * cw * ch;
            assert_eq!(
                out.reserved_bytes(),
                total,
                "{width}x{height}: the reservation is the plane bytes"
            );
            assert_eq!(
                layout.planes.iter().map(|p| p.len as usize).sum::<usize>(),
                total
            );
            let mut expected = expected_plane(0, width as usize, height as usize);
            expected.extend(expected_plane(1, cw, ch));
            expected.extend(expected_plane(2, cw, ch));
            assert_eq!(out.data(), &expected);
        }
    }

    #[test]
    fn nv12_is_kept_as_nv12() {
        let source = frame(Pixel::NV12, 18, 16, 1, pattern);
        let out = Converter::default()
            .convert(&source, id(), false)
            .expect("converts");
        assert_eq!(out.layout().format, PixelFormat::Nv12);
        assert_eq!(
            (out.layout().planes[1].stride, out.layout().planes[2].len),
            (18, 0)
        );
        let mut expected = expected_plane(0, 18, 16);
        expected.extend(expected_plane(1, 18, 8));
        assert_eq!(out.data(), &expected);
    }

    #[test]
    fn ten_bit_420_becomes_eight_bit_i420() {
        // Flat 10-bit mid-grey: Y, U and V all 512, as little-endian 16-bit samples.
        let source = frame(Pixel::YUV420P10LE, 18, 16, 2, |_, x, _| {
            if x % 2 == 0 { 0 } else { 2 }
        });
        let out = Converter::default()
            .convert(&source, id(), false)
            .expect("converts");
        assert_eq!(out.layout().format, PixelFormat::I420);
        assert_eq!(out.reserved_bytes(), 18 * 16 + 2 * 9 * 8);
        assert!(
            out.data().iter().all(|sample| sample.abs_diff(128) <= 1),
            "512 of 1023 is 128 of 255"
        );
    }

    #[test]
    fn the_matrix_follows_the_stream_and_otherwise_the_picture_height() {
        let desc =
            |space, height| color_desc(space, Range::Unspecified, Pixel::YUV420P, height).matrix;
        assert_eq!(desc(Space::BT709, 480), Matrix::Bt709);
        assert_eq!(desc(Space::SMPTE170M, 1080), Matrix::Bt601);
        assert_eq!(desc(Space::Unspecified, 719), Matrix::Bt601);
        assert_eq!(desc(Space::Unspecified, 720), Matrix::Bt709);
        assert!(color_desc(Space::BT709, Range::JPEG, Pixel::YUV420P, 48).full_range);
        assert!(color_desc(Space::BT709, Range::Unspecified, Pixel::YUVJ420P, 48).full_range);
        assert!(!color_desc(Space::BT709, Range::MPEG, Pixel::YUV420P, 48).full_range);
    }
}
