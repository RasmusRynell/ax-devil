//! Opening a video file with FFmpeg: its demuxer, its video stream and the decoder for it.

use crate::VideoInfo;
use crate::convert::{color_desc, delivered_format};
use crate::input::VideoInput;
use ffmpeg_next::Dictionary;
use ffmpeg_next::codec::{self, threading};
use ffmpeg_next::decoder;
use ffmpeg_next::format::context::Input;
use ffmpeg_next::format::{self};
use ffmpeg_next::media::Type;
use ffmpeg_next::util::log::{self, Level};
use foundation::{CancelToken, ErrorCode, FrameRate, HostPath, Interrupt, UserError};
use std::path::Path;
use std::sync::Once;

/// An opened file and the video stream chosen in it.
pub(crate) struct Opened {
    pub(crate) input: Input,
    pub(crate) stream: usize,
    /// Seconds per tick of the stream's timestamps, as numerator and denominator, both positive.
    pub(crate) time_base: (i32, i32),
}

/// The error for something that went wrong with the file at `path`.
pub(crate) fn error(code: ErrorCode, reason: impl Into<String>, path: &Path) -> UserError {
    UserError::new(code, reason).at(HostPath::new(path))
}

/// Opens `input` and picks its first video stream that is not cover art, not FFmpeg's "best" one, which can be another
/// stream. FFmpeg polls `cancel` while it opens the file and on every later read of the returned input, and stops with
/// an error when it is cancelled; that error is reported as [`Interrupt::Cancelled`]. FFmpeg can only poll where it
/// waits in its own code: a blocking system call, such as a read from a hung network mount, is not interrupted.
///
/// An image sequence is opened through the `image2` demuxer at its first image, which tells the decoder what the
/// images are; the demuxer is not used to read the frames (see [`crate::sequence`]).
pub(crate) fn open_input(
    video: &VideoInput,
    cancel: &CancelToken,
) -> Result<Opened, Interrupt<UserError>> {
    static INIT: Once = Once::new();
    INIT.call_once(|| {
        // Failures reach the caller as values, so FFmpeg's own log to stderr stays off.
        log::set_level(Level::Quiet);
    });
    let token = cancel.clone();
    let path = video.location();
    let opened = match video {
        VideoInput::File(path) => format::input_with_interrupt(path, move || token.is_cancelled()),
        VideoInput::ImageSequence(sequence) => {
            let mut options = Dictionary::new();
            options.set(
                "framerate",
                &format!(
                    "{}/{}",
                    sequence.frame_rate.num(),
                    sequence.frame_rate.den()
                ),
            );
            options.set("start_number", &sequence.start_number.to_string());
            options.set("pattern_type", "sequence");
            format::input_with_interrupt_and_dictionary(path, move || token.is_cancelled(), options)
        }
    };
    let input = opened.map_err(|e| {
        cancel.check().err().map_or_else(
            || {
                let (code, what) = match video {
                    VideoInput::File(_) => (ErrorCode::NotAVideo, "The file is not a video"),
                    VideoInput::ImageSequence(_) => (
                        ErrorCode::FileUnreadable,
                        "The first image cannot be opened",
                    ),
                };
                Interrupt::Failed(error(code, format!("{what}: {e}"), path))
            },
            Interrupt::from,
        )
    })?;
    cancel.check()?;
    if matches!(video, VideoInput::ImageSequence(_)) && input.format().name() != "image2" {
        return Err(Interrupt::Failed(error(
            ErrorCode::NotAVideo,
            "The first image is not a picture FFmpeg reads as an image sequence",
            path,
        )));
    }
    let Some(stream) = input.streams().find(|stream| {
        stream.parameters().medium() == Type::Video
            && !stream
                .disposition()
                .contains(format::stream::Disposition::ATTACHED_PIC)
    }) else {
        return Err(Interrupt::Failed(error(
            ErrorCode::NotAVideo,
            "The file has no video stream",
            path,
        )));
    };
    let rate = stream.time_base();
    if rate.numerator() <= 0 || rate.denominator() <= 0 {
        return Err(Interrupt::Failed(error(
            ErrorCode::UnsupportedFormat,
            "The video has no usable time base",
            path,
        )));
    }
    let stream = stream.index();
    Ok(Opened {
        input,
        stream,
        time_base: (rate.numerator(), rate.denominator()),
    })
}

/// Opens the video file at `path`; see [`open_input`].
pub(crate) fn open(path: &Path, cancel: &CancelToken) -> Result<Opened, Interrupt<UserError>> {
    open_input(&VideoInput::File(path.to_path_buf()), cancel)
}

/// Opens a decoder for the video stream with `threads` frame-parallel decode threads; 0 lets FFmpeg choose.
pub(crate) fn open_decoder(
    opened: &Opened,
    threads: usize,
    path: &Path,
) -> Result<decoder::Video, UserError> {
    let Some(stream) = opened.input.stream(opened.stream) else {
        return Err(error(
            ErrorCode::FileChanged,
            "The video stream disappeared",
            path,
        ));
    };
    let mut context =
        codec::context::Context::from_parameters(stream.parameters()).map_err(|e| {
            error(
                ErrorCode::UnsupportedFormat,
                format!("The video codec is not supported: {e}"),
                path,
            )
        })?;
    context.set_threading(threading::Config {
        kind: threading::Type::Frame,
        count: threads,
    });
    context.decoder().video().map_err(|e| {
        error(
            ErrorCode::UnsupportedFormat,
            format!("The video codec could not be opened: {e}"),
            path,
        )
    })
}

/// Size, delivered pixel layout, colour and declared frame rate of the video stream.
pub(crate) fn probe(opened: &Opened, path: &Path) -> Result<VideoInfo, UserError> {
    let decoder = open_decoder(opened, 1, path)?;
    let (width, height) = (decoder.width(), decoder.height());
    if width == 0 || height == 0 {
        return Err(error(
            ErrorCode::UnsupportedFormat,
            "The video has no picture size",
            path,
        ));
    }
    let source = decoder.format();
    let frame_rate = opened.input.stream(opened.stream).and_then(|stream| {
        [stream.avg_frame_rate(), stream.rate()]
            .into_iter()
            .find_map(|rate| {
                FrameRate::new(
                    u32::try_from(rate.numerator()).ok()?,
                    u32::try_from(rate.denominator()).ok()?,
                )
            })
    });
    Ok(VideoInfo {
        width,
        height,
        frame_rate,
        format: delivered_format(source),
        color: color_desc(decoder.color_space(), decoder.color_range(), source, height),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::sync::mpsc;
    use std::time::Duration;

    /// FFmpeg waits for the bytes of a server that accepted and then says nothing, as it does for a stalled network
    /// source. (A named pipe would not do: FFmpeg reads a local file with a blocking system call, which nothing
    /// interrupts.)
    #[test]
    fn opening_a_source_that_never_delivers_stops_when_cancelled_and_is_not_a_failure() {
        let silent = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = format!("tcp://{}", silent.local_addr().unwrap());
        let cancel = CancelToken::new();
        let (done, finished) = mpsc::channel();
        let token = cancel.clone();
        std::thread::spawn(move || done.send(open(Path::new(&address), &token).err()).unwrap());
        let _accepted = silent.accept().unwrap();

        // The server never answers, so opening can only end by the cancel: any other outcome is a failure below.
        cancel.cancel();
        let outcome = finished
            .recv_timeout(Duration::from_secs(10))
            .expect("a cancelled open returns");
        assert!(matches!(outcome, Some(Interrupt::Cancelled)), "{outcome:?}");
    }
}
