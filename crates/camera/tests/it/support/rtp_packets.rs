//! Packet encoding shared by the synthetic transport and real-picture fixtures.
use super::fake_rtsp::{GOP, PPS, SPS};

/// The RTP payloads of frame `frame`, each with whether it ends the frame.
pub(super) fn video_payloads(frame: u32, keyframes: bool) -> Vec<(Vec<u8>, bool)> {
    let index = u16::try_from(frame)
        .expect("fewer than 65536 frames")
        .to_be_bytes();
    if !keyframes || !frame.is_multiple_of(GOP) {
        let mut nal = vec![0x41, 0xBB, index[0], index[1], 0xBB];
        nal.extend([0xAA; 200]);
        return vec![(nal, true)];
    }
    let mut stap = vec![24, 0, SPS.len() as u8];
    stap.extend(SPS);
    stap.extend([0, PPS.len() as u8]);
    stap.extend(PPS);
    let mut idr = vec![0x65, 0xBB, index[0], index[1], 0xBB];
    idr.extend([0xAA; 2500]);
    // FU-A: the NAL header's type and importance go to the fragments' headers; its body is cut into pieces.
    let body = &idr[1..];
    let pieces: Vec<&[u8]> = body.chunks(1000).collect();
    let mut payloads = vec![(stap, false)];
    for (n, piece) in pieces.iter().enumerate() {
        let (first, last) = (n == 0, n + 1 == pieces.len());
        let header = (u8::from(first) << 7) | (u8::from(last) << 6) | 5;
        let mut payload = vec![0x60 | 28, header];
        payload.extend(*piece);
        payloads.push((payload, last));
    }
    payloads
}

/// One RTP packet with the Axis capture time in its header extension.
pub(super) fn rtp(
    payload_type: u8,
    seq: u16,
    timestamp: u32,
    marker: bool,
    capture: i64,
    payload: &[u8],
) -> Vec<u8> {
    let seconds = u32::try_from(capture / 1_000_000_000 + 2_208_988_800).expect("NTP seconds");
    let fraction =
        u32::try_from((u128::from(capture.unsigned_abs() % 1_000_000_000) << 32) / 1_000_000_000)
            .expect("fraction");
    let mut packet = vec![0x90, (u8::from(marker) << 7) | payload_type];
    packet.extend(seq.to_be_bytes());
    packet.extend(timestamp.to_be_bytes());
    packet.extend(0xAu32.to_be_bytes());
    packet.extend([0xAB, 0xAC, 0, 3]);
    packet.extend(seconds.to_be_bytes());
    packet.extend(fraction.to_be_bytes());
    packet.extend([0; 4]);
    packet.extend(payload);
    packet
}
