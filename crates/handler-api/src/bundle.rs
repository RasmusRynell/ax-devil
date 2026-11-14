use std::{
    collections::BTreeMap,
    fs, io,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

use foundation::hex;
use serde::{Deserialize, Serialize, Serializer};
use sha2::{Digest, Sha256};

use crate::VideoRef;

/// The inputs of a derived lane, written by the host and read by the decoder that computes it. Its digest is the
/// derived lane's identity, so any input change rebuilds it and an unchanged one reuses the stored result.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Bundle {
    /// The input lane files by the key the decoder declares them under.
    pub lanes: BTreeMap<String, BundleLane>,
    /// The video the lanes belong to, when the decoder takes one.
    pub video: Option<BundleVideo>,
}

/// One input lane written as a lane file.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BundleLane {
    /// The lane file.
    #[serde(serialize_with = "lossy")]
    pub path: PathBuf,
    /// The lowercase hex SHA-256 of the file's content.
    pub sha256: String,
}

/// The video of a bundle, identified by the file's size and modification time, or for an image sequence by its pattern,
/// image count and a digest of every image's size and modification time.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BundleVideo {
    /// The video file.
    #[serde(serialize_with = "lossy")]
    pub path: PathBuf,
    /// The file size in bytes.
    pub size: u64,
    /// The modification time in nanoseconds since the Unix epoch.
    pub mtime_ns: i128,
    /// For an image sequence: the lowercase hex SHA-256 of each image's size and modification time, in frame order, so
    /// replacing one image changes the bundle.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub images_sha256: Option<String>,
}

impl BundleVideo {
    /// The entry for `video`. Costs one `stat` per image of a sequence and reads no image. A missing image is part of
    /// the identity, not an error here: the decoder that needs it reports it.
    pub fn of(video: &VideoRef) -> io::Result<Self> {
        match video {
            VideoRef::File(path) => {
                let file = fs::canonicalize(path)?;
                let stat = fs::metadata(&file)?;
                Ok(Self {
                    path: file,
                    size: stat.len(),
                    mtime_ns: mtime_ns(stat.modified()),
                    images_sha256: None,
                })
            }
            VideoRef::ImageSequence(sequence) => {
                let numbering = Numbering::parse(&sequence.pattern)?;
                let mut digest = Sha256::new();
                for index in 0..sequence.frame_count {
                    let number = u64::from(sequence.start_number) + index;
                    match fs::metadata(numbering.path(number)) {
                        Ok(stat) => {
                            digest.update([1]);
                            digest.update(stat.len().to_le_bytes());
                            digest.update(mtime_ns(stat.modified()).to_le_bytes());
                        }
                        Err(_) => digest.update([0]),
                    }
                }
                Ok(Self {
                    path: sequence.pattern.clone(),
                    size: sequence.frame_count,
                    mtime_ns: 0,
                    images_sha256: Some(hex(&digest.finalize())),
                })
            }
        }
    }
}

/// A modification time as nanoseconds since the Unix epoch; 0 when the platform gives none.
fn mtime_ns(modified: io::Result<SystemTime>) -> i128 {
    modified.map_or(0, |modified| match modified.duration_since(UNIX_EPOCH) {
        Ok(after) => i128::try_from(after.as_nanos()).unwrap_or(i128::MAX),
        Err(before) => -i128::try_from(before.duration().as_nanos()).unwrap_or(i128::MAX),
    })
}

/// A file name pattern with one `%d` or `%0Nd` in it.
struct Numbering {
    before: String,
    width: usize,
    after: String,
}

impl Numbering {
    fn parse(pattern: &Path) -> io::Result<Self> {
        let invalid = |reason: &str| io::Error::new(io::ErrorKind::InvalidInput, reason.to_owned());
        let text = pattern
            .to_str()
            .ok_or_else(|| invalid("the image pattern is not valid text"))?;
        let at = text
            .find('%')
            .ok_or_else(|| invalid("the image pattern has no number in it"))?;
        let rest = &text[at + 1..];
        let digits = rest.chars().take_while(char::is_ascii_digit).count();
        let (padding, after) = rest.split_at(digits);
        let after = after
            .strip_prefix('d')
            .filter(|after| !after.contains('%'))
            .ok_or_else(|| invalid("the image pattern must hold exactly one %d or %06d"))?;
        let width = if padding.is_empty() {
            0
        } else {
            padding
                .parse()
                .map_err(|_| invalid("the image pattern's padding is too wide"))?
        };
        Ok(Self {
            before: text[..at].to_owned(),
            width,
            after: after.to_owned(),
        })
    }

    fn path(&self, number: u64) -> PathBuf {
        let Self {
            before,
            width,
            after,
        } = self;
        PathBuf::from(format!("{before}{number:0width$}{after}"))
    }
}

impl Bundle {
    /// The bundle stored at `path`.
    pub fn read(path: &Path) -> io::Result<Self> {
        let bytes = std::fs::read(path)?;
        serde_json::from_slice(&bytes).map_err(io::Error::from)
    }

    /// The canonical JSON bytes: fixed field order and sorted lane keys, so equal bundles have equal bytes.
    pub fn to_bytes(&self) -> Vec<u8> {
        // Cannot fail: strings, numbers and string-keyed maps always serialize, and `lossy` handles any path.
        serde_json::to_vec(self).unwrap_or_default()
    }
}

/// Writes a path as text, replacing bytes that are not UTF-8, so serializing a bundle cannot fail.
// serde's `serialize_with` passes `&PathBuf`, the field's type, so the signature cannot take `&Path`.
#[allow(clippy::ptr_arg)]
fn lossy<S: Serializer>(path: &PathBuf, serializer: S) -> Result<S::Ok, S::Error> {
    serializer.serialize_str(&path.to_string_lossy())
}
