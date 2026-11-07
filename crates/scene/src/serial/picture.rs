//! Picture bytes on the wire: base64 text in JSON, raw bytes in postcard.

use std::fmt;

use bytes::Bytes;
use serde::{
    Deserialize, Deserializer, Serialize, Serializer,
    de::{self, Visitor},
};

const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// Borrowed picture bytes being written.
pub(super) struct BytesOut<'a>(pub &'a [u8]);

impl Serialize for BytesOut<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        if serializer.is_human_readable() {
            serializer.serialize_str(&to_base64(self.0))
        } else {
            serializer.serialize_bytes(self.0)
        }
    }
}

/// Picture bytes being read.
pub(super) struct BytesIn(pub Bytes);

impl<'de> Deserialize<'de> for BytesIn {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        if deserializer.is_human_readable() {
            let text = String::deserialize(deserializer)?;
            from_base64(&text)
                .map(|data| Self(data.into()))
                .map_err(de::Error::custom)
        } else {
            deserializer.deserialize_bytes(RawVisitor)
        }
    }
}

struct RawVisitor;

impl Visitor<'_> for RawVisitor {
    type Value = BytesIn;

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("bytes")
    }

    fn visit_bytes<E>(self, v: &[u8]) -> Result<BytesIn, E> {
        Ok(BytesIn(Bytes::copy_from_slice(v)))
    }

    fn visit_byte_buf<E>(self, v: Vec<u8>) -> Result<BytesIn, E> {
        Ok(BytesIn(v.into()))
    }
}

/// Standard base64 with padding.
fn to_base64(data: &[u8]) -> String {
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let word = chunk.iter().enumerate().fold(0_u32, |word, (i, &byte)| {
            word | u32::from(byte) << (16 - 8 * i)
        });
        for i in 0..4 {
            if i <= chunk.len() {
                out.push(char::from(ALPHABET[(word >> (18 - 6 * i) & 63) as usize]));
            } else {
                out.push('=');
            }
        }
    }
    out
}

fn from_base64(text: &str) -> Result<Vec<u8>, String> {
    let bad = || "picture data is not valid base64".to_string();
    let bytes = text.as_bytes();
    if !bytes.len().is_multiple_of(4) {
        return Err(bad());
    }
    let mut out = Vec::with_capacity(bytes.len() / 4 * 3);
    let last = bytes.len().saturating_sub(4);
    for (start, quad) in bytes.chunks(4).enumerate().map(|(i, quad)| (i * 4, quad)) {
        let padding = quad.iter().rev().take_while(|&&byte| byte == b'=').count();
        if padding > 2 || (padding > 0 && start != last) {
            return Err(bad());
        }
        let mut word = 0_u32;
        for &byte in &quad[..4 - padding] {
            let sextet = ALPHABET.iter().position(|&c| c == byte).ok_or_else(bad)?;
            word = word << 6 | sextet as u32;
        }
        word <<= 6 * padding;
        for i in 0..3 - padding {
            out.push((word >> (16 - 8 * i)) as u8);
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_matches_the_standard_vectors_and_rejects_garbage() {
        for (plain, coded) in [
            ("", ""),
            ("f", "Zg=="),
            ("fo", "Zm8="),
            ("foo", "Zm9v"),
            ("foobar", "Zm9vYmFy"),
        ] {
            assert_eq!(to_base64(plain.as_bytes()), coded);
            assert_eq!(from_base64(coded), Ok(plain.as_bytes().to_vec()));
        }
        let every_byte: Vec<u8> = (0..=255).collect();
        assert_eq!(from_base64(&to_base64(&every_byte)), Ok(every_byte));
        for bad in ["Zg=", "Z===", "Zg==Zm9v", "Zm9*", "Zm 9"] {
            assert!(from_base64(bad).is_err(), "{bad}");
        }
    }
}
