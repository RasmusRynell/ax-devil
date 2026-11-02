//! The `seqinfo.ini` of a MOT Challenge sequence.

use std::path::{Component, Path, PathBuf};

/// What a MOT Challenge `seqinfo.ini` says about its sequence.
#[derive(Clone, Debug, PartialEq)]
pub struct SeqInfo {
    /// The sequence name, when the file gives one.
    pub name: Option<String>,
    /// Frames per second.
    pub fps: f64,
    /// Number of images.
    pub frame_count: u64,
    /// Image width in pixels.
    pub width: u32,
    /// Image height in pixels.
    pub height: u32,
    /// The `imDir` value, `img1` when absent. Untrusted until [`SeqInfo::image_pattern`] accepts it.
    image_dir: String,
    /// The `imExt` value, `.jpg` when absent. Untrusted until [`SeqInfo::image_pattern`] accepts it.
    image_ext: String,
}

impl SeqInfo {
    /// The images' path below the sequence folder: `<imDir>/%06d<imExt>`. The error names the offending key
    /// when either is not a single plain name (no separators, `..` or absolute paths).
    pub fn image_pattern(&self) -> Result<PathBuf, String> {
        let plain = Path::new(&self.image_dir)
            .components()
            .eq([Component::Normal(self.image_dir.as_ref())])
            && !self.image_dir.contains('\\');
        if !plain {
            return Err(format!(
                "imDir '{}' must be a single folder name inside the sequence",
                self.image_dir
            ));
        }
        let extension = self.image_ext.strip_prefix('.').unwrap_or(&self.image_ext);
        if extension.is_empty() || !extension.chars().all(|c| c.is_ascii_alphanumeric()) {
            return Err(format!(
                "imExt '{}' must be a file extension such as .jpg",
                self.image_ext
            ));
        }
        Ok(Path::new(&self.image_dir).join(format!("%06d.{extension}")))
    }
}

/// Reads the `[Sequence]` section of a `seqinfo.ini`. Keys are matched case-insensitively; `;` and `#` start a
/// comment line and `=` or `:` separates a key from its value. The error says what is wrong with the file.
pub fn parse_seqinfo(text: &str) -> Result<SeqInfo, String> {
    let mut in_sequence = false;
    let mut found_section = false;
    let (mut name, mut fps, mut length, mut width, mut height) = (None, None, None, None, None);
    let (mut image_dir, mut image_ext) = (None, None);
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with([';', '#']) {
            continue;
        }
        if let Some(section) = line
            .strip_prefix('[')
            .and_then(|rest| rest.strip_suffix(']'))
        {
            in_sequence = section.trim().eq_ignore_ascii_case("sequence");
            found_section |= in_sequence;
            continue;
        }
        let Some((key, value)) = line.split_once(['=', ':']) else {
            continue;
        };
        if !in_sequence {
            continue;
        }
        let (key, value) = (key.trim(), value.trim());
        let slot = [
            ("name", &mut name),
            ("framerate", &mut fps),
            ("seqlength", &mut length),
            ("imwidth", &mut width),
            ("imheight", &mut height),
            ("imdir", &mut image_dir),
            ("imext", &mut image_ext),
        ]
        .into_iter()
        .find(|(wanted, _)| key.eq_ignore_ascii_case(wanted));
        if let Some((_, slot)) = slot {
            *slot = Some(value);
        }
    }
    if !found_section {
        return Err("missing [Sequence] section".to_string());
    }
    let fps: f64 = parse_key("frameRate", fps)?;
    if !fps.is_finite() || fps <= 0.0 {
        return Err(format!("frameRate '{fps}' must be a positive number"));
    }
    let frame_count: u64 = parse_key("seqLength", length)?;
    let width: u32 = parse_key("imWidth", width)?;
    let height: u32 = parse_key("imHeight", height)?;
    if width == 0 || height == 0 {
        return Err("imWidth and imHeight must be positive".to_string());
    }
    Ok(SeqInfo {
        name: name.filter(|name| !name.is_empty()).map(str::to_string),
        fps,
        frame_count,
        width,
        height,
        image_dir: image_dir
            .filter(|value| !value.is_empty())
            .unwrap_or("img1")
            .to_string(),
        image_ext: image_ext
            .filter(|value| !value.is_empty())
            .unwrap_or(".jpg")
            .to_string(),
    })
}

fn parse_key<T: std::str::FromStr>(key: &str, value: Option<&str>) -> Result<T, String> {
    let value = value.ok_or_else(|| format!("missing key {key}"))?;
    value
        .parse()
        .map_err(|_| format!("{key} '{value}' is not a valid number"))
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOOD: &str = "[Sequence]\nname=SEQ\nimDir=img1\nframeRate=30\nseqLength=100\nimWidth=640\nimHeight=480\n";

    fn info(name: Option<&str>, fps: f64, frames: u64, width: u32, height: u32) -> SeqInfo {
        SeqInfo {
            name: name.map(str::to_string),
            fps,
            frame_count: frames,
            width,
            height,
            image_dir: "img1".to_string(),
            image_ext: ".jpg".to_string(),
        }
    }

    #[test]
    fn reads_keys_case_insensitively_with_comments_and_either_separator() {
        assert_eq!(
            parse_seqinfo(GOOD),
            Ok(info(Some("SEQ"), 30.0, 100, 640, 480))
        );
        let text = "; note\r\n[sequence]\r\n# note\r\nFRAMERATE : 14.5\r\nSEQLENGTH=7\r\nimwidth = 1\r\nIMHEIGHT=2\r\n";
        assert_eq!(parse_seqinfo(text), Ok(info(None, 14.5, 7, 1, 2)));
    }

    #[test]
    fn builds_the_image_pattern_from_imdir_and_imext_and_refuses_paths() {
        let pattern = |dir: &str, ext: &str| {
            let text = format!(
                "[Sequence]\nframeRate=1\nseqLength=1\nimWidth=1\nimHeight=1\nimDir={dir}\nimExt={ext}\n"
            );
            parse_seqinfo(&text).unwrap().image_pattern()
        };
        assert_eq!(
            pattern("frames", "png"),
            Ok(PathBuf::from("frames/%06d.png"))
        );
        assert_eq!(pattern("img1", ".jpg"), Ok(PathBuf::from("img1/%06d.jpg")));
        for (dir, ext) in [
            ("../x", ".jpg"),
            ("/abs", ".jpg"),
            ("a/b", ".jpg"),
            ("..", ".jpg"),
            ("a\\b", ".jpg"),
            ("img1", "/x"),
            ("img1", ".j/pg"),
            ("img1", "."),
        ] {
            assert!(pattern(dir, ext).is_err(), "{dir} {ext}");
        }
    }

    #[test]
    fn rejects_files_the_sequence_cannot_be_built_from() {
        let rejected = [
            "[Other]\nkey=val\n",
            "",
            "[Sequence]\nframeRate=30\nseqLength=1\nimWidth=1\n",
            "[Sequence]\nframeRate=fast\nseqLength=1\nimWidth=1\nimHeight=1\n",
            "[Sequence]\nframeRate=nan\nseqLength=1\nimWidth=1\nimHeight=1\n",
            "[Sequence]\nframeRate=0\nseqLength=1\nimWidth=1\nimHeight=1\n",
            "[Sequence]\nframeRate=30\nseqLength=-1\nimWidth=1\nimHeight=1\n",
            "[Sequence]\nframeRate=30\nseqLength=1\nimWidth=0\nimHeight=1\n",
            "frameRate=30\nseqLength=1\nimWidth=1\nimHeight=1\n",
            "[Other]\nframeRate=30\nseqLength=1\nimWidth=1\nimHeight=1\n[Sequence]\n",
        ];
        for text in rejected {
            assert!(parse_seqinfo(text).is_err(), "{text:?}");
        }
    }
}
