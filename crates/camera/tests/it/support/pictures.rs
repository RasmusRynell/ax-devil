//! Real H.264 pictures for the desktop fixture; transport tests keep their numbered synthetic packets.

pub fn payloads(generation: usize) -> Vec<(Vec<u8>, bool)> {
    let bytes: &[u8] = if generation == 0 {
        include_bytes!("../../../../../fixtures/video/rtsp-red.h264")
    } else {
        include_bytes!("../../../../../fixtures/video/rtsp-green.h264")
    };
    let mut units = Vec::new();
    let mut start = 0;
    for at in 0..bytes.len().saturating_sub(3) {
        if bytes[at..].starts_with(&[0, 0, 1]) {
            if start != 0 {
                let end = if bytes[at - 1] == 0 { at - 1 } else { at };
                units.push((bytes[start..end].to_vec(), false));
            }
            start = at + 3;
        }
    }
    units.push((bytes[start..].to_vec(), true));
    units
}
