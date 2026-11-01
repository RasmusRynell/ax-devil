//! Building an index from a file: the packet scan, and the decode scan it falls back to.

use crate::ScanKind;
use crate::ffi;
use crate::source::{self, Opened};
use ffmpeg_next::Packet;
use ffmpeg_next::frame::Video;
use foundation::{CancelToken, ErrorCode, Interrupt, UserError};
use std::path::Path;
use tracing::debug;

/// Presentation-ordered pts of every frame, and the sequence ids of the keyframes.
type Scanned = (Vec<i64>, Vec<u64>);

/// Scans `path` by its packets, or by decoding when the packets cannot give every frame its own timestamp.
pub(super) fn build(
    opened: &mut Opened,
    path: &Path,
    file_size: u64,
    cancel: &CancelToken,
    progress: &mut dyn FnMut(f32),
) -> Result<(ScanKind, Vec<i64>, Vec<u64>), Interrupt<UserError>> {
    progress(0.0);
    if let Some((pts, keyframes)) = packet_scan(opened, path, file_size, cancel, progress)? {
        return Ok((ScanKind::Packet, pts, keyframes));
    }
    debug!(path = %path.display(), "packets lack unique timestamps; decoding to build the index");
    let mut again = source::open(path, cancel)?;
    progress(0.0);
    let (pts, keyframes) = decode_scan(&mut again, path, file_size, cancel, progress)?;
    Ok((ScanKind::Decode, pts, keyframes))
}

/// Orders the video packets, given as (pts, keyframe flag) in file order, into presentation order. `None` when a
/// packet has no pts or two share one, so the packets alone cannot say which frame is which.
pub(super) fn order(mut packets: Vec<(Option<i64>, bool)>) -> Option<Scanned> {
    let mut stamped = Vec::with_capacity(packets.len());
    for (pts, key) in packets.drain(..) {
        stamped.push((pts?, key));
    }
    stamped.sort_by_key(|(pts, _)| *pts);
    if stamped.windows(2).any(|pair| pair[0].0 == pair[1].0) {
        return None;
    }
    let keyframes = keyframes_of(stamped.iter().map(|(_, key)| *key));
    Some((stamped.into_iter().map(|(pts, _)| pts).collect(), keyframes))
}

/// Sequence ids of the flagged frames. Decoding always starts at the first frame, so it is a keyframe.
fn keyframes_of(flags: impl Iterator<Item = bool>) -> Vec<u64> {
    let mut keyframes: Vec<u64> = flags
        .enumerate()
        .filter_map(|(sequence_id, key)| key.then_some(sequence_id as u64))
        .collect();
    if keyframes.first() != Some(&0) {
        keyframes.insert(0, 0);
    }
    keyframes
}

/// Reports the share of the file read so far, when it grew by at least a hundredth.
struct Reporter<'a> {
    progress: &'a mut dyn FnMut(f32),
    file_size: u64,
    read: u64,
    reported: f32,
}

impl Reporter<'_> {
    fn read(&mut self, bytes: usize) {
        self.read = self.read.saturating_add(bytes as u64);
        let share = (self.read as f64 / self.file_size.max(1) as f64).min(1.0) as f32;
        if share - self.reported >= 0.01 {
            self.reported = share;
            (self.progress)(share);
        }
    }
}

/// Reads every packet of the video stream. Packets the container marks as discarded are left out: they never
/// produce a frame.
pub(super) fn packet_scan(
    opened: &mut Opened,
    path: &Path,
    file_size: u64,
    cancel: &CancelToken,
    progress: &mut dyn FnMut(f32),
) -> Result<Option<Scanned>, Interrupt<UserError>> {
    let mut reporter = Reporter {
        progress,
        file_size,
        read: 0,
        reported: 0.0,
    };
    let mut packets = Vec::new();
    loop {
        cancel.check()?;
        let mut packet = Packet::empty();
        match packet.read(&mut opened.input) {
            Ok(()) => {}
            Err(ffmpeg_next::Error::Eof) => break,
            Err(e) => {
                // A read that FFmpeg gave up because of the cancel is no failure of the file.
                cancel.check()?;
                return Err(Interrupt::Failed(source::error(
                    ErrorCode::IndexingFailed,
                    format!("The video cannot be read to the end: {e}"),
                    path,
                )));
            }
        }
        reporter.read(packet.size());
        if packet.stream() == opened.stream && !ffi::is_discard(&packet) {
            packets.push((packet.pts(), packet.is_key()));
        }
    }
    Ok(order(packets))
}

/// Decodes the whole video and takes each frame's pts and keyframe flag from the decoder, in output order.
pub(super) fn decode_scan(
    opened: &mut Opened,
    path: &Path,
    file_size: u64,
    cancel: &CancelToken,
    progress: &mut dyn FnMut(f32),
) -> Result<Scanned, Interrupt<UserError>> {
    let mut decoder = source::open_decoder(opened, 0, path).map_err(Interrupt::Failed)?;
    let mut reporter = Reporter {
        progress,
        file_size,
        read: 0,
        reported: 0.0,
    };
    let mut frames: Vec<(i64, bool)> = Vec::new();
    let mut missing_pts = false;
    let mut collect = |decoder: &mut ffmpeg_next::decoder::Video, frames: &mut Vec<(i64, bool)>| {
        let mut frame = Video::empty();
        while decoder.receive_frame(&mut frame).is_ok() {
            match frame.pts() {
                Some(pts) => frames.push((pts, frame.is_key())),
                None => missing_pts = true,
            }
        }
    };
    loop {
        cancel.check()?;
        let mut packet = Packet::empty();
        if packet.read(&mut opened.input).is_err() {
            break;
        }
        reporter.read(packet.size());
        if packet.stream() != opened.stream {
            continue;
        }
        // A packet the decoder rejects costs its frame; the frames that do decode are still indexed.
        if decoder.send_packet(&packet).is_ok() {
            collect(&mut decoder, &mut frames);
        }
    }
    // A read that FFmpeg gave up because of the cancel ends the loop like the end of the file does.
    cancel.check()?;
    let _ = decoder.send_eof();
    collect(&mut decoder, &mut frames);
    if missing_pts {
        return Err(Interrupt::Failed(source::error(
            ErrorCode::IndexingFailed,
            "A frame of the video has no timestamp",
            path,
        )));
    }
    let keyframes = keyframes_of(frames.iter().map(|(_, key)| *key));
    Ok((frames.into_iter().map(|(pts, _)| pts).collect(), keyframes))
}
