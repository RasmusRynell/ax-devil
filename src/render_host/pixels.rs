//! The Pixels messages: what a page may send back for a job, and the checks that stop a wrong one reaching the
//! encoder.

use api_types::render::PIXELS_HEADER_BYTES;
use export::{Output, Pixels};
use foundation::FrameSize;

/// Rows of a plane may be padded to a multiple of this many bytes.
const STRIDE_ALIGNMENT: usize = 256;

/// The widest row strides, luma and chroma, a page may use for `size`: a row padded to the alignment.
fn aligned(size: FrameSize, output: Output) -> (usize, usize) {
    let round = |row: usize| row.div_ceil(STRIDE_ALIGNMENT) * STRIDE_ALIGNMENT;
    let width = size.width as usize;
    match output {
        Output::I420 => (round(width), round(width / 2)),
        Output::Rgba => (round(width * 4), 0),
    }
}

/// The bytes of each plane for rows `strides.0` bytes apart (`strides.1` for the chroma planes).
fn plane_lengths(output: Output, size: FrameSize, strides: (usize, usize)) -> [usize; 3] {
    let height = size.height as usize;
    match output {
        Output::I420 => [
            strides.0 * height,
            strides.1 * height / 2,
            strides.1 * height / 2,
        ],
        Output::Rgba => [strides.0 * height, 0, 0],
    }
}

/// The most bytes a Pixels message of a job's output may have: a header and rows padded to the alignment.
pub(super) fn largest_message(output: Output, size: FrameSize) -> usize {
    PIXELS_HEADER_BYTES as usize
        + plane_lengths(output, size, aligned(size, output))
            .iter()
            .sum::<usize>()
}

/// Whether `pixels` is the answer to frame `expected` of a job drawing `output` of `size`; if not, why not.
pub(super) fn check(
    pixels: &Pixels,
    (output, size): (Output, FrameSize),
    expected: u64,
) -> Result<(), String> {
    let minimum = match output {
        Output::I420 => (size.width as usize, size.width as usize / 2),
        Output::Rgba => (size.width as usize * 4, 0),
    };
    let maximum = aligned(size, output);
    let strides = (pixels.stride0 as usize, pixels.stride1 as usize);
    let stride_ok = (minimum.0..=maximum.0).contains(&strides.0)
        && (minimum.1..=maximum.1).contains(&strides.1);
    if pixels.seq != expected {
        Err(format!(
            "the render page answered frame {} when frame {expected} was next",
            pixels.seq
        ))
    } else if pixels.output != output || (pixels.width, pixels.height) != (size.width, size.height)
    {
        Err("the render page sent a picture that is not the size and format of the job".to_owned())
    } else if !stride_ok
        || pixels.data.len() != plane_lengths(output, size, strides).iter().sum::<usize>()
    {
        Err("the render page sent a picture whose planes do not fit its layout".to_owned())
    } else {
        Ok(())
    }
}
