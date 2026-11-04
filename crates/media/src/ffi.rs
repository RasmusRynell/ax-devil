//! The only module with `unsafe`: the few FFmpeg calls `ffmpeg-next` has no safe form of. Everything else in the
//! crate uses the safe items exported here.

use ffmpeg_next::ffi;
use ffmpeg_next::format::Pixel;
use ffmpeg_next::format::context::Input;
use ffmpeg_next::packet::Ref;
use ffmpeg_next::software::scaling::{Context, Flags};
use ffmpeg_next::{Error, Packet, frame};
use foundation::Matrix;

/// Whether the demuxer marked the packet as one that must not produce output (decode-only pre-roll).
pub(crate) fn is_discard(packet: &Packet) -> bool {
    // SAFETY: `as_ptr` points at the `AVPacket` that `packet` owns, which is initialised and outlives this read.
    let flags = unsafe { (*packet.as_ptr()).flags };
    flags & ffi::AV_PKT_FLAG_DISCARD != 0
}

/// Seeks `input` to the last keyframe at or before `pts`, a timestamp in the time base of `stream`.
pub(crate) fn seek_to_pts(input: &mut Input, stream: usize, pts: i64) -> Result<(), Error> {
    let Ok(stream) = i32::try_from(stream) else {
        return Err(Error::StreamNotFound);
    };
    // SAFETY: `input.as_mut_ptr()` is the open `AVFormatContext` that `input` owns and `&mut` makes this the only
    // use of it; `stream` is an index FFmpeg validates, returning an error for one that does not exist.
    let code =
        unsafe { ffi::av_seek_frame(input.as_mut_ptr(), stream, pts, ffi::AVSEEK_FLAG_BACKWARD) };
    if code < 0 {
        Err(Error::from(code))
    } else {
        Ok(())
    }
}

/// A swscale conversion between two pixel formats of one size, with the YUV matrix and ranges set explicitly.
pub(crate) struct Scaler {
    context: Context,
}

// SAFETY: a `Scaler` exclusively owns its `SwsContext`, which has no thread affinity or shared state, so moving it
// to another thread is sound as long as one thread uses it at a time, which `&mut self` on every use guarantees.
// (`Sync` is not implemented.)
unsafe impl Send for Scaler {}

impl Scaler {
    /// Converts `width` x `height` frames from `source` to `target`. YUV sources use `matrix`; `source_full` says
    /// whether their samples span the full range, which `target` keeps when it is YUV and sets when it is RGB.
    pub(crate) fn new(
        source: Pixel,
        target: Pixel,
        (width, height): (u32, u32),
        flags: Flags,
        matrix: Matrix,
        source_full: bool,
    ) -> Result<Self, Error> {
        let target_full = source_full || is_rgb(target);
        Self::resizing(
            (source, (width, height)),
            (target, (width, height)),
            flags,
            (matrix, matrix),
            (source_full, target_full),
        )
    }

    /// Converts frames of the `source` format and size to the `target` format and size. YUV sources use the first
    /// matrix and YUV targets the second; the two flags say whether the source's and the target's samples span the
    /// full range.
    pub(crate) fn resizing(
        (source, (width, height)): (Pixel, (u32, u32)),
        (target, (target_width, target_height)): (Pixel, (u32, u32)),
        flags: Flags,
        (source_matrix, target_matrix): (Matrix, Matrix),
        (source_full, target_full): (bool, bool),
    ) -> Result<Self, Error> {
        let mut context = Context::get(
            source,
            width,
            height,
            target,
            target_width,
            target_height,
            flags,
        )?;
        let standard = |matrix| match matrix {
            Matrix::Bt601 => ffi::SWS_CS_ITU601,
            Matrix::Bt709 => ffi::SWS_CS_ITU709,
        };
        let (source_full, target_full) = (i32::from(source_full), i32::from(target_full));
        // SAFETY: `context` is the valid `SwsContext` created above and `&mut` makes this the only use. The
        // coefficient tables come from `sws_getCoefficients`, which returns pointers to static data for any
        // standard. swscale returns an error and leaves the context unchanged when the source is RGB, where there
        // is no matrix to set, so the result is deliberately ignored.
        unsafe {
            ffi::sws_setColorspaceDetails(
                context.as_mut_ptr(),
                ffi::sws_getCoefficients(standard(source_matrix)),
                source_full,
                ffi::sws_getCoefficients(standard(target_matrix)),
                target_full,
                0,
                1 << 16,
                1 << 16,
            );
        }
        Ok(Self { context })
    }

    /// Converts `input` into `output`, allocating `output` when it is empty.
    pub(crate) fn run(
        &mut self,
        input: &frame::Video,
        output: &mut frame::Video,
    ) -> Result<(), Error> {
        self.context.run(input, output)
    }
}

/// Whether swscale treats `format` as RGB, which has no YUV range.
fn is_rgb(format: Pixel) -> bool {
    format == Pixel::RGBA
}
