use std::{
    collections::HashMap,
    fs,
    io::{self, ErrorKind},
    path::{Path, PathBuf},
};

use crate::schema::invalid_field;
use foundation::{
    CancelToken, Cancelled, ErrorCode, FrameRate, HostPath, Interrupt, Location, UserError,
};

use crate::{
    Choice, Cli, Field, FieldKind, ImageSequence, OverlayRef, PlaylistResolver, ResolvedEntry,
    ResolvedPlaylist, ResolverSchema, ResolverSpec, Settings, VideoRef, WatchPaths,
    folder_pair::{required_text, resolved_file_type},
    seqinfo::{SeqInfo, parse_seqinfo},
};

static SPEC: ResolverSpec = ResolverSpec {
    id: "mot_challenge",
    display_name: "MOT Challenge",
    description: "Load the train and test sequences of a MOT Challenge dataset folder.",
};

static SCHEMA: ResolverSchema = ResolverSchema {
    fields: &[
        Field {
            key: "root",
            label: "Dataset folder",
            kind: FieldKind::Folder,
            required: true,
            cli: Cli::Positional,
        },
        Field {
            key: "sequences",
            label: "Sequences",
            kind: FieldKind::Scan {
                depends_on: &["root"],
            },
            required: false,
            cli: Cli::Flag("sequences"),
        },
    ],
};

const DETECTOR_SUFFIXES: [&str; 3] = ["DPM", "FRCNN", "SDP"];
const MOT_HANDLER: &str = "MOT_FILE";

/// Lists the sequences of a MOT Challenge dataset folder.
///
/// Sequences are the folders with a `seqinfo.ini` directly under `root` and under its `train` and `test` folders.
/// The optional `sequences` setting names the ones to keep. Detector variants of one video (`-DPM`, `-FRCNN`,
/// `-SDP`) become one entry with one detection lane each, in variant name order, and a single ground-truth lane
/// right after the detection lane of the first variant that has one: `DPM, GT, FRCNN, SDP`.
pub struct MotChallengeResolver;

impl PlaylistResolver for MotChallengeResolver {
    fn spec(&self) -> &'static ResolverSpec {
        &SPEC
    }

    fn schema(&self) -> &'static ResolverSchema {
        &SCHEMA
    }

    fn scan(
        &self,
        field: &str,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<Vec<Choice>, Interrupt<UserError>> {
        if field != "sequences" {
            return Err(Interrupt::Failed(invalid_field(
                "This setting has nothing to scan",
                field,
            )));
        }
        let root = required_text(settings, "root").map_err(Interrupt::Failed)?;
        let mut sequences = load_sequences(Path::new(root), Location::field("root"), cancel)?;
        sequences.sort_by(|a, b| a.name.cmp(&b.name));
        sequences.dedup_by(|a, b| a.name == b.name);
        Ok(sequences.into_iter().map(Sequence::into_choice).collect())
    }

    fn resolve(
        &self,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<ResolvedPlaylist, Interrupt<UserError>> {
        let root = required_text(settings, "root").map_err(Interrupt::Failed)?;
        let subset = sequence_subset(settings).map_err(Interrupt::Failed)?;
        let mut sequences = load_sequences(Path::new(root), HostPath::new(root).into(), cancel)?;
        if let Some(names) = subset {
            let mut missing: Vec<&str> = names
                .iter()
                .copied()
                .filter(|name| !sequences.iter().any(|sequence| sequence.name == *name))
                .collect();
            if !missing.is_empty() {
                missing.sort_unstable();
                tracing::warn!(
                    "MOT sequences not found in {}: {}",
                    root,
                    missing.join(", ")
                );
            }
            sequences.retain(|sequence| names.contains(&sequence.name.as_str()));
        }
        let entries = build_entries(sequences, cancel)?;
        if entries.is_empty() {
            return Err(Interrupt::Failed(
                UserError::new(ErrorCode::PlaylistInvalid, "No MOT sequences found")
                    .at(HostPath::new(root)),
            ));
        }
        Ok(ResolvedPlaylist {
            display_name: SPEC.display_name.to_string(),
            entries,
        })
    }

    fn watch_paths(
        &self,
        settings: &Settings,
        cancel: &CancelToken,
    ) -> Result<WatchPaths, Cancelled> {
        let Ok(root) = required_text(settings, "root") else {
            return Ok(WatchPaths::default());
        };
        let root = PathBuf::from(root);
        let mut watch = WatchPaths::default();
        match candidate_dirs(&root, cancel) {
            Ok(candidates) => {
                for dir in candidates {
                    cancel.check()?;
                    let ini = dir.join("seqinfo.ini");
                    if ini.is_file() {
                        watch.files.insert(ini);
                    }
                }
            }
            Err(Interrupt::Cancelled) => return Err(Cancelled),
            Err(Interrupt::Failed(_)) => {}
        }
        watch.folders.insert(root);
        Ok(watch)
    }
}

/// A folder with a readable `seqinfo.ini`.
struct Sequence {
    name: String,
    path: PathBuf,
    info: SeqInfo,
}

impl Sequence {
    /// The scan's offer for this sequence: its name, with the size, rate and length as the second line.
    fn into_choice(self) -> Choice {
        let info = &self.info;
        Choice {
            detail: format!(
                "{}×{} · {} fps · {} frames",
                info.width, info.height, info.fps, info.frame_count
            ),
            label: self.name.clone(),
            value: self.name,
        }
    }
}

/// The sequences under `root`. A problem with the folder is attached to `location`: the setting key for a scan, the
/// path for a resolve.
fn load_sequences(
    root: &Path,
    location: Location,
    cancel: &CancelToken,
) -> Result<Vec<Sequence>, Interrupt<UserError>> {
    cancel.check()?;
    let error =
        |code, reason: String| Interrupt::Failed(UserError::new(code, reason).at(location.clone()));
    if !root.is_dir() {
        return Err(error(
            ErrorCode::FileNotFound,
            "MOT root directory does not exist".to_string(),
        ));
    }
    discover(root, cancel).map_err(|interrupt| match interrupt {
        Interrupt::Cancelled => Interrupt::Cancelled,
        Interrupt::Failed(cause) => error(
            ErrorCode::FileUnreadable,
            format!("Cannot read the MOT root directory: {cause}"),
        ),
    })
}

/// The `sequences` setting; `None` when it is absent.
fn sequence_subset(settings: &Settings) -> Result<Option<Vec<&str>>, UserError> {
    let invalid = || invalid_field("Must be a list of sequence names", "sequences");
    match settings.get("sequences") {
        None | Some(serde_json::Value::Null) => Ok(None),
        Some(serde_json::Value::Array(names)) => names
            .iter()
            .map(|name| name.as_str().ok_or_else(invalid))
            .collect::<Result<_, _>>()
            .map(Some),
        Some(_) => Err(invalid()),
    }
}

/// The folders under `root` and under its `train` and `test` folders, in path order.
fn candidate_dirs(root: &Path, cancel: &CancelToken) -> Result<Vec<PathBuf>, Interrupt<io::Error>> {
    let mut dirs = Vec::new();
    list_dirs(root, &mut dirs, cancel)?;
    for split in ["train", "test"] {
        let split = root.join(split);
        if split.is_dir() {
            list_dirs(&split, &mut dirs, cancel)?;
        }
    }
    dirs.sort_unstable();
    dirs.dedup();
    Ok(dirs)
}

fn list_dirs(
    parent: &Path,
    dirs: &mut Vec<PathBuf>,
    cancel: &CancelToken,
) -> Result<(), Interrupt<io::Error>> {
    for entry in fs::read_dir(parent).map_err(Interrupt::Failed)? {
        cancel.check()?;
        let entry = entry.map_err(Interrupt::Failed)?;
        if resolved_file_type(&entry).is_some_and(|ty| ty.is_dir()) {
            dirs.push(entry.path());
        }
    }
    Ok(())
}

/// The sequences under `root`; a folder with a missing or unreadable `seqinfo.ini` is left out.
fn discover(root: &Path, cancel: &CancelToken) -> Result<Vec<Sequence>, Interrupt<io::Error>> {
    let mut sequences = Vec::new();
    for path in candidate_dirs(root, cancel)? {
        cancel.check()?;
        let text = match fs::read_to_string(path.join("seqinfo.ini")) {
            Ok(text) => text,
            Err(error) if matches!(error.kind(), ErrorKind::NotFound | ErrorKind::IsADirectory) => {
                continue;
            }
            Err(error) => {
                tracing::warn!(
                    "Skipping {}: cannot read seqinfo.ini ({error})",
                    path.display()
                );
                continue;
            }
        };
        let mut info = match parse_seqinfo(&text) {
            Ok(info) => info,
            Err(reason) => {
                tracing::warn!("Skipping {}: bad seqinfo.ini ({reason})", path.display());
                continue;
            }
        };
        let name = info.name.take().unwrap_or_else(|| {
            path.file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned()
        });
        sequences.push(Sequence { name, path, info });
    }
    Ok(sequences)
}

/// One entry per video: detector variants of a sequence share the first variant's video.
fn build_entries(
    sequences: Vec<Sequence>,
    cancel: &CancelToken,
) -> Result<Vec<ResolvedEntry>, Interrupt<UserError>> {
    let mut groups: Vec<(String, Vec<Sequence>)> = Vec::new();
    let mut index: HashMap<String, usize> = HashMap::new();
    for sequence in sequences {
        let base = split_detector(&sequence.name).map_or(sequence.name.as_str(), |(base, _)| base);
        match index.get(base) {
            Some(&group) => groups[group].1.push(sequence),
            None => {
                let base = base.to_string();
                index.insert(base.clone(), groups.len());
                groups.push((base, vec![sequence]));
            }
        }
    }

    let mut entries = Vec::with_capacity(groups.len());
    for (name, variants) in groups {
        cancel.check()?;
        let first = &variants[0];
        let frame_rate = FrameRate::parse(&first.info.fps.to_string()).ok_or_else(|| {
            Interrupt::Failed(
                UserError::new(
                    ErrorCode::PlaylistInvalid,
                    format!("frameRate {} is not a usable frame rate", first.info.fps),
                )
                .at(HostPath::new(first.path.join("seqinfo.ini"))),
            )
        })?;
        let images = first.info.image_pattern().map_err(|reason| {
            Interrupt::Failed(
                UserError::new(ErrorCode::PlaylistInvalid, reason)
                    .at(HostPath::new(first.path.join("seqinfo.ini"))),
            )
        })?;
        let video = VideoRef::ImageSequence(ImageSequence {
            pattern: first.path.join(images),
            start_number: 1,
            frame_rate,
            width: first.info.width,
            height: first.info.height,
            frame_count: first.info.frame_count,
        });
        let mut overlays = Vec::new();
        let mut has_ground_truth = false;
        for variant in &variants {
            let detections = variant.path.join("det").join("det.txt");
            if detections.is_file() {
                let label = split_detector(&variant.name).map_or_else(
                    || "DET".to_string(),
                    |(_, detector)| detector.to_ascii_uppercase(),
                );
                overlays.push(mot_overlay(label, detections));
            }
            let ground_truth = variant.path.join("gt").join("gt.txt");
            if !has_ground_truth && ground_truth.is_file() {
                has_ground_truth = true;
                overlays.push(mot_overlay("GT".to_string(), ground_truth));
            }
        }
        entries.push(ResolvedEntry {
            name,
            video,
            overlays,
        });
    }
    Ok(entries)
}

fn mot_overlay(label: String, path: PathBuf) -> OverlayRef {
    OverlayRef {
        label,
        path,
        handler: MOT_HANDLER.to_string(),
        options: Settings::new(),
    }
}

/// Splits `MOT17-01-DPM` into its video name and detector.
fn split_detector(name: &str) -> Option<(&str, &str)> {
    let (base, detector) = name.rsplit_once('-')?;
    (!base.is_empty()
        && DETECTOR_SUFFIXES
            .iter()
            .any(|known| detector.eq_ignore_ascii_case(known)))
    .then_some((base, detector))
}
