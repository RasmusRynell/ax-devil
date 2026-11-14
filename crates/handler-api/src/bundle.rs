use std::{collections::BTreeMap, io, path::Path, path::PathBuf};

use serde::{Deserialize, Serialize, Serializer};

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

/// The video of a bundle, identified by the file's size and modification time.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BundleVideo {
    /// The video file.
    #[serde(serialize_with = "lossy")]
    pub path: PathBuf,
    /// The file size in bytes.
    pub size: u64,
    /// The modification time in nanoseconds since the Unix epoch.
    pub mtime_ns: i128,
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
