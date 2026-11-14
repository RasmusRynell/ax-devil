//! One RTP packet as the assemblers need it, with the capture time Axis puts in the header extension.

use bytes::Bytes;
use retina::rtp::ReceivedPacket;

use crate::live::CaptureTime;

/// The RTP header extension profile Axis uses for the capture time (`onvifreplayext=1`).
const AXIS_EXTENSION: u16 = 0xABAC;
const NTP_UNIX_OFFSET: i64 = 2_208_988_800;

/// The fields of an RTP packet the assemblers use.
#[derive(Clone, Debug)]
pub(crate) struct Packet {
    /// The RTP marker bit: the last packet of a frame or document.
    pub(crate) marker: bool,
    /// The RTP timestamp, which is the same for every packet of a frame.
    pub(crate) timestamp: u32,
    /// How many packets retina saw go missing just before this one.
    pub(crate) loss: u16,
    /// When the camera captured what this packet carries, if it said.
    pub(crate) capture: Option<CaptureTime>,
    /// The payload, without header and extension.
    pub(crate) payload: Bytes,
}

impl Packet {
    /// Reads a packet retina received.
    pub(crate) fn from_received(packet: ReceivedPacket) -> Self {
        let capture = capture_time(packet.raw());
        let header = packet.header();
        Self {
            marker: header.mark(),
            timestamp: header.timestamp(),
            loss: packet.loss(),
            capture,
            payload: packet.into_payload_bytes(),
        }
    }
}

/// The capture time in the Axis header extension of the raw RTP packet `raw`, if there is one.
fn capture_time(raw: &[u8]) -> Option<CaptureTime> {
    let first = *raw.first()?;
    if first & 0x10 == 0 {
        return None;
    }
    let extension = 12 + 4 * usize::from(first & 0x0F);
    let head = raw.get(extension..extension + 4)?;
    let profile = u16::from_be_bytes([head[0], head[1]]);
    let words = u16::from_be_bytes([head[2], head[3]]);
    if profile != AXIS_EXTENSION || words < 3 {
        return None;
    }
    let ntp = raw.get(extension + 4..extension + 12)?;
    let seconds = u32::from_be_bytes([ntp[0], ntp[1], ntp[2], ntp[3]]);
    let fraction = u32::from_be_bytes([ntp[4], ntp[5], ntp[6], ntp[7]]);
    Some(ntp_to_unix(seconds, fraction))
}

/// A 64-bit NTP timestamp as Unix nanoseconds. Seconds below 2^31 are in NTP era 1, which starts on 2036-02-07
/// (RFC 4330 section 3).
fn ntp_to_unix(seconds: u32, fraction: u32) -> CaptureTime {
    let era = if seconds < 0x8000_0000 { 1i64 << 32 } else { 0 };
    let nanos = (u64::from(fraction) * 1_000_000_000) >> 32;
    CaptureTime((i64::from(seconds) + era - NTP_UNIX_OFFSET) * 1_000_000_000 + nanos as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// An RTP packet with an extension of `profile` and `words` words, the first two being `ntp`.
    fn packet(profile: u16, words: u16, ntp: [u32; 2]) -> Vec<u8> {
        let mut raw = vec![0x90, 0x60, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0];
        raw.extend(profile.to_be_bytes());
        raw.extend(words.to_be_bytes());
        raw.extend(ntp[0].to_be_bytes());
        raw.extend(ntp[1].to_be_bytes());
        raw.extend(vec![0; usize::from(words.saturating_sub(2)) * 4]);
        raw.extend([0x65, 1, 2]);
        raw
    }

    #[test]
    fn ntp_converts_to_unix_nanoseconds() {
        // 2025-03-11T08:11:54.5Z is 1741680714.5 s after the Unix epoch.
        let ntp_seconds = 1_741_680_714 + NTP_UNIX_OFFSET as u32;
        assert_eq!(
            ntp_to_unix(ntp_seconds, 1 << 31),
            CaptureTime(1_741_680_714_500_000_000)
        );
        // The first second of era 1 wraps to the start of 2036.
        assert_eq!(
            ntp_to_unix(0, 0),
            CaptureTime((1i64 << 32) * 1_000_000_000 - NTP_UNIX_OFFSET * 1_000_000_000)
        );
    }

    #[test]
    fn the_axis_extension_gives_the_capture_time() {
        let ntp_seconds = 1_741_680_714 + NTP_UNIX_OFFSET as u32;
        let raw = packet(AXIS_EXTENSION, 3, [ntp_seconds, 0]);
        assert_eq!(
            capture_time(&raw),
            Some(CaptureTime(1_741_680_714_000_000_000))
        );
    }

    #[test]
    fn other_extensions_and_truncated_ones_give_none() {
        assert_eq!(capture_time(&packet(0xBEDE, 3, [1, 1])), None);
        assert_eq!(capture_time(&packet(AXIS_EXTENSION, 2, [1, 1])), None);
        assert_eq!(capture_time(&packet(AXIS_EXTENSION, 3, [1, 1])[..20]), None);
        assert_eq!(
            capture_time(&[0x80, 0x60, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0]),
            None
        );
        assert_eq!(capture_time(&[]), None);
    }
}
