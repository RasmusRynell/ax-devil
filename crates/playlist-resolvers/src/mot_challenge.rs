use std::{
    collections::HashMap,
    fs,
    io::{self, ErrorKind},
    path::{Path, PathBuf},
};

use foundation::{
    CancelToken, ErrorCode, FrameRate, HostPath, Interrupt, Location, SeqInfo, UserError, Warning,
    parse_seqinfo,
};
use handler_api::{
    Choice, Field, FieldKind, Form, ImageSequence, OverlayRef, PlaylistResolver, ResolvedEntry,
    ResolvedPlaylist, ResolverSpec, Selection, Settings, SkippedRecords, Values, VideoRef,
    invalid_field,
};

use crate::resolved_file_type;

const DETECTOR_SUFFIXES: [&str; 3] = ["DPM", "FRCNN", "SDP"];
const NOT_LISTED: &str = "They are not in the playlist.";
const MOT_HANDLER: &str = "MOT_FILE";

/// Lists the sequences of a MOT Challenge dataset folder.
///
/// Sequences are the folders with a `seqinfo.ini` directly under `root` and under its `train` and `test` folders.
/// The optional `sequences` setting names the ones to keep. Detector variants of one video (`-DPM`, `-FRCNN`,
/// `-SDP`) become one entry with one detection lane each, in variant name order, and a single ground-truth lane
/// right after the detection lane of the first variant that has one: `DPM, GT, FRCNN, SDP`. Every lane's `width` and
/// `height` options are the image size its sequence's `seqinfo.ini` states, which the MOT decoder needs.
pub struct MotChallengeResolver {
    spec: ResolverSpec,
}

impl MotChallengeResolver {
    pub(crate) fn new() -> Self {
        Self {
            spec: ResolverSpec {
                id: "mot_challenge".to_owned(),
                plugin: "mot_challenge".to_owned(),
                name: "MOT Challenge".to_owned(),
                description: "Load the train and test sequences of a MOT Challenge dataset folder."
                    .to_owned(),
                form: Form {
                    fields: vec![
                        Field::new("root", "Dataset folder", FieldKind::Folder)
                            .required()
                            .positional(),
                        Field::new(
                            "sequences",
                            "Sequences",
                            FieldKind::Scan {
                                depends_on: vec!["root".to_owned()],
                            },
                        ),
                    ],
                },
            },
        }
    }
}

impl PlaylistResolver for MotChallengeResolver {
    fn spec(&self) -> &ResolverSpec {
        &self.spec
    }

    fn scan(
        &self,
        field: &str,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<Vec<Choice>, Interrupt<UserError>> {
        if field != "sequences" {
            return Err(Interrupt::Failed(invalid_field(
                "This setting has nothing to scan",
                field,
            )));
        }
        let root = root(settings).map_err(Interrupt::Failed)?;
        let mut sequences = load_sequences(root, Location::field("root"), cancel)?.sequences;
        sequences.sort_by(|a, b| a.name.cmp(&b.name));
        sequences.dedup_by(|a, b| a.name == b.name);
        Ok(sequences.into_iter().map(Sequence::into_choice).collect())
    }

    fn resolve(
        &self,
        settings: &Values,
        cancel: &CancelToken,
    ) -> Result<ResolvedPlaylist, Interrupt<UserError>> {
        let root = root(settings).map_err(Interrupt::Failed)?;
        let Discovered {
            mut sequences,
            skipped,
            total,
        } = load_sequences(root, HostPath::new(root).into(), cancel)?;
        let mut warnings: Vec<Warning> = skipped.warning(total).into_iter().collect();
        if let Selection::Only(names) = settings.selection("sequences") {
            let mut missing = SkippedRecords::new("selected sequences", NOT_LISTED);
            let mut absent: Vec<&str> = names
                .iter()
                .map(String::as_str)
                .filter(|name| !sequences.iter().any(|sequence| sequence.name == *name))
                .collect();
            absent.sort_unstable();
            for name in absent {
                missing.add(format!("{name} is not in the dataset folder"));
            }
            warnings.extend(missing.warning(names.len()));
            sequences.retain(|sequence| names.contains(&sequence.name));
        }
        let entries = build_entries(sequences, cancel)?;
        if entries.is_empty() {
            return Err(Interrupt::Failed(
                UserError::new(ErrorCode::PlaylistInvalid, "No MOT sequences found")
                    .at(HostPath::new(root)),
            ));
        }
        Ok(ResolvedPlaylist {
            display_name: self.spec.name.clone(),
            entries,
            warnings,
        })
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

/// The sequences under `root` and the folders left out of them.
struct Discovered {
    sequences: Vec<Sequence>,
    /// The folders with a `seqinfo.ini` that could not be used.
    skipped: SkippedRecords,
    /// How many folders have a `seqinfo.ini`.
    total: usize,
}

/// The sequences under `root`. A problem with the folder is attached to `location`: the setting key for a scan, the
/// path for a resolve.
fn load_sequences(
    root: &Path,
    location: Location,
    cancel: &CancelToken,
) -> Result<Discovered, Interrupt<UserError>> {
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

/// The dataset folder setting.
fn root(settings: &Values) -> Result<&Path, UserError> {
    settings
        .path("root")
        .ok_or_else(|| invalid_field("Required", "root"))
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

/// The sequences under `root`; a folder with a missing `seqinfo.ini` is not one, and one with an unreadable or bad
/// `seqinfo.ini` is skipped.
fn discover(root: &Path, cancel: &CancelToken) -> Result<Discovered, Interrupt<io::Error>> {
    let mut sequences = Vec::new();
    let mut skipped = SkippedRecords::new("sequences", NOT_LISTED);
    let mut total = 0;
    for path in candidate_dirs(root, cancel)? {
        cancel.check()?;
        let text = match fs::read_to_string(path.join("seqinfo.ini")) {
            Ok(text) => text,
            Err(error) if matches!(error.kind(), ErrorKind::NotFound | ErrorKind::IsADirectory) => {
                continue;
            }
            Err(error) => {
                total += 1;
                skipped.add(format!(
                    "{}: cannot read seqinfo.ini ({error})",
                    path.display()
                ));
                continue;
            }
        };
        total += 1;
        let mut info = match parse_seqinfo(&text) {
            Ok(info) => info,
            Err(reason) => {
                skipped.add(format!("{}: bad seqinfo.ini ({reason})", path.display()));
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
    Ok(Discovered {
        sequences,
        skipped,
        total,
    })
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
                overlays.push(mot_overlay(label, detections, &variant.info));
            }
            let ground_truth = variant.path.join("gt").join("gt.txt");
            if !has_ground_truth && ground_truth.is_file() {
                has_ground_truth = true;
                overlays.push(mot_overlay("GT".to_string(), ground_truth, &variant.info));
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

/// A MOT lane over `path`, with the image size the decoder normalizes its boxes by.
fn mot_overlay(label: String, path: PathBuf, info: &SeqInfo) -> OverlayRef {
    OverlayRef {
        slot: label.clone(),
        label,
        path,
        handler: MOT_HANDLER.to_string(),
        options: Settings::from_iter([
            ("width".to_owned(), info.width.into()),
            ("height".to_owned(), info.height.into()),
        ]),
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
