//! Where an export may write: the checks before anything is created.

use std::path::{Path, PathBuf};

use foundation::{ErrorCode, HostPath, UserError};
use media::{EncodeFormat, VideoInput};

use crate::ExportSpec;

/// Refuses a destination that cannot be written, or that is something the export reads.
pub(crate) fn check(spec: &ExportSpec) -> Result<(), UserError> {
    let path = spec.destination.as_path();
    let refuse =
        |reason: &str| UserError::new(ErrorCode::InvalidPath, reason).at(HostPath::new(path));
    let (Some(name), Some(parent)) = (path.file_name(), path.parent()) else {
        return Err(refuse("The output must be a file path"));
    };
    if !path.is_absolute() {
        return Err(refuse("The output path must be absolute"));
    }
    let extension = match spec.format {
        EncodeFormat::Mp4 { .. } => "mp4",
        EncodeFormat::Png => "png",
    };
    if !Path::new(name)
        .extension()
        .is_some_and(|found| found.eq_ignore_ascii_case(extension))
    {
        return Err(refuse(&format!("The output must end in .{extension}")));
    }
    if !parent.is_dir() {
        return Err(refuse("The output's folder does not exist"));
    }
    if path.exists() && !path.is_file() {
        return Err(refuse("The output is not a file"));
    }
    let overwrites_video = match &spec.video {
        VideoInput::File(video) => same(video, path),
        VideoInput::ImageSequence(sequence) => in_sequence(&sequence.pattern, path),
    };
    if overwrites_video || spec.inputs.iter().any(|input| same(input, path)) {
        return Err(refuse("The output would overwrite a file the export reads"));
    }
    Ok(())
}

/// Whether two paths name the same file, whether or not it exists yet: the same resolved path, or a hard link.
fn same(a: &Path, b: &Path) -> bool {
    resolved(a) == resolved(b) || linked(a, b)
}

/// Whether two existing paths are hard links to one file (the same device and inode).
#[cfg(unix)]
fn linked(a: &Path, b: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    match (a.metadata(), b.metadata()) {
        (Ok(a), Ok(b)) => (a.dev(), a.ino()) == (b.dev(), b.ino()),
        _ => false,
    }
}

/// Hard links are not detected here: only the resolved path is compared.
#[cfg(not(unix))]
fn linked(_: &Path, _: &Path) -> bool {
    false
}

/// The path with symlinks resolved as far as it exists: the file itself, else its folder with the name appended.
fn resolved(path: &Path) -> PathBuf {
    if let Ok(found) = path.canonicalize() {
        return found;
    }
    match (path.parent().map(Path::canonicalize), path.file_name()) {
        (Some(Ok(parent)), Some(name)) => parent.join(name),
        _ => path.to_path_buf(),
    }
}

/// Whether `path` is an image of the sequence `pattern` (one `%d` or `%0Nd` in its file name): named like one in the
/// sequence's folder, whatever the number, since an image outside the sequence's range is still not a file to replace;
/// or the file a symlinked image of the sequence points at.
fn in_sequence(pattern: &Path, path: &Path) -> bool {
    let (Some(pattern_name), Some(name), Some(folder)) = (
        pattern.file_name().and_then(|name| name.to_str()),
        path.file_name().and_then(|name| name.to_str()),
        pattern.parent(),
    ) else {
        return false;
    };
    let numbered = |name: &str| {
        let Some((before, rest)) = pattern_name.split_once('%') else {
            return false;
        };
        let Some((spec, after)) = rest.split_once('d') else {
            return false;
        };
        spec.bytes().all(|byte| byte.is_ascii_digit())
            && name
                .strip_prefix(before)
                .and_then(|tail| tail.strip_suffix(after))
                .is_some_and(|digits| {
                    !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit())
                })
    };
    if path.parent().is_some_and(|parent| same(folder, parent)) && numbered(name) {
        return true;
    }
    std::fs::read_dir(folder).is_ok_and(|entries| {
        entries.flatten().any(|entry| {
            entry.file_type().is_ok_and(|kind| kind.is_symlink())
                && entry.file_name().to_str().is_some_and(numbered)
                && same(&entry.path(), path)
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_image_of_the_sequence_is_not_a_place_to_write() {
        let pattern = Path::new("/seq/img1/%06d.png");
        // Neither folder exists; paths then compare as written.
        assert!(in_sequence(pattern, Path::new("/seq/img1/000007.png")));
        assert!(!in_sequence(pattern, Path::new("/seq/img1/overview.png")));
        assert!(!in_sequence(pattern, Path::new("/seq/img1/000007.jpg")));
        assert!(!in_sequence(pattern, Path::new("/seq/other/000007.png")));
    }

    #[cfg(unix)]
    #[test]
    fn the_file_a_linked_image_of_the_sequence_points_at_is_not_a_place_to_write() {
        let dir = tempfile::tempdir().unwrap();
        let (seq, original) = (dir.path().join("seq"), dir.path().join("original"));
        std::fs::create_dir_all(&seq).unwrap();
        std::fs::create_dir_all(&original).unwrap();
        std::fs::write(original.join("frame.png"), "image").unwrap();
        std::os::unix::fs::symlink(original.join("frame.png"), seq.join("000001.png")).unwrap();
        let pattern = seq.join("%06d.png");

        assert!(in_sequence(&pattern, &original.join("frame.png")));
        assert!(!in_sequence(&pattern, &original.join("other.png")));
    }
}
