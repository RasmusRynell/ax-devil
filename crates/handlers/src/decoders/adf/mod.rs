//! Axis Analytics Data Format (ADF), beta and v1: JSON, one object per line, as frames or as consolidated tracks.
//!
//! A line of v1 may start with a timestamp column and a tab, which is dropped; a beta line may not. A bad line of a
//! frame file is skipped; a bad line of a consolidated file fails the whole file.

mod consolidated;
mod frame;
mod parse;

use std::sync::Arc;

use foundation::{CancelToken, Interrupt, Warning};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Form, Framing, Header, Input, OnEqualKey, SceneSink, Spec,
    Values,
};
use memchr::memchr;
use parse::{Object, object};
use scene::{ClassRule, Coverage, Fields, FilterOption, FilterPolicy, MatchBasis, Name, Scene};

/// The `Spec::version` of all four ADF decoders, which share their reading code: raise it whenever any of them gives
/// other output for the same file.
const VERSION: u32 = 8;

/// Filter id, label and the class it shows, as every ADF decoder lists them first.
const FILTERS: [(&str, &str, &str); 12] = [
    ("show_unknown", "Unknown", "unknown"),
    ("show_humans", "Humans", "human"),
    ("show_heads", "Heads", "head"),
    ("show_vehicles", "Vehicles", "vehicle"),
    ("show_vehicles_other", "Vehicles (Other)", "vehicle_other"),
    ("show_cars", "Cars", "car"),
    ("show_buses", "Buses", "bus"),
    ("show_trucks", "Trucks", "truck"),
    ("show_bikes", "Bikes", "bike"),
    ("show_bicycles", "Bicycles", "bicycle"),
    ("show_animals", "Animals", "animal"),
    ("show_license_plates", "License Plates", "license_plate"),
];
const BAGS: (&str, &str, &str) = ("show_bags", "Bags", "bag");
const FACES: (&str, &str, &str) = ("show_faces", "Faces", "face");

/// Which ADF format a decoder reads, which decides the order of its last two class filters and its filter policy's name.
#[derive(Clone, Copy)]
enum Format {
    /// Bags, then Faces.
    V1,
    /// Faces, then Bags.
    Beta,
}

impl Format {
    fn filter_name(self) -> &'static str {
        match self {
            Self::V1 => "adf_v1_frame",
            Self::Beta => "adf_beta",
        }
    }

    /// Every filter in the order the format lists them; `show_other` is added after them.
    fn filters(self) -> Vec<(&'static str, &'static str, &'static str)> {
        let last = match self {
            Self::V1 => [BAGS, FACES],
            Self::Beta => [FACES, BAGS],
        };
        FILTERS.into_iter().chain(last).collect()
    }
}

/// What a line of a file is.
#[derive(Clone, Copy)]
enum Lines {
    /// A frame: its Scene replaces an earlier one of its time, and its place in the file is its frame.
    Frames,
    /// Tracks: their observations are Scenes of their own times, which join the others of that time.
    Tracks,
}

impl Lines {
    fn on_equal_key(self) -> OnEqualKey {
        match self {
            Self::Frames => OnEqualKey::Replace,
            Self::Tracks => OnEqualKey::Merge,
        }
    }
}

/// Reads one object of a line into the Scenes it gives, noting what the file's warnings should say.
type Read = fn(&Object, &mut Notes) -> Result<Vec<Scene>, String>;

/// What the records that were read had in them that is worth a warning.
#[derive(Default)]
struct Notes {
    /// Beta records whose frames are at different times.
    differing_times: usize,
    /// Beta observations that had a world velocity, which is not shown.
    world_velocity: usize,
}

impl Notes {
    /// The warning for each kind of note, if any were made.
    fn warnings(&self) -> [Option<Warning>; 2] {
        let warning = |count: usize, problem: String, consequence: &str| {
            (count > 0).then(|| Warning {
                problem,
                reasons: Vec::new(),
                consequence: consequence.to_owned(),
                more_reasons: false,
            })
        };
        [
            warning(
                self.differing_times,
                format!(
                    "Records with frames at different times: {}",
                    self.differing_times
                ),
                "Each is one Scene over its whole time range.",
            ),
            warning(
                self.world_velocity,
                format!(
                    "Observations with a world velocity: {}",
                    self.world_velocity
                ),
                "It is not shown.",
            ),
        ]
    }
}

/// One of the four ADF decoders.
struct AdfDecoder {
    spec: Spec,
    read: Read,
    lines: Lines,
    /// Whether a line may start with a timestamp column and a tab.
    tsv_prefix: bool,
    /// The format the decoder reads.
    format: Format,
}

/// Every ADF decoder: beta, then v1, each as frames and as consolidated tracks.
pub(crate) fn decoders() -> Vec<Arc<dyn Decoder>> {
    let adf = |id, plugin, name, format, lines, read, tsv_prefix| -> Arc<dyn Decoder> {
        Arc::new(AdfDecoder::new(
            id, plugin, name, format, lines, read, tsv_prefix,
        ))
    };
    vec![
        adf(
            "ADF_BETA_FRAME",
            "adf-v1-beta",
            "ADF v1 Beta Frame",
            Format::Beta,
            Lines::Frames,
            frame::read_beta,
            false,
        ),
        adf(
            "ADF_BETA_CONSOLIDATED",
            "adf-v1-beta",
            "ADF v1 Beta Consolidated",
            Format::Beta,
            Lines::Tracks,
            |record, notes| consolidated::read(record, false, notes),
            false,
        ),
        adf(
            "ADF_V1_FRAME",
            "adf-v1",
            "ADF v1 Frame",
            Format::V1,
            Lines::Frames,
            frame::read_v1,
            true,
        ),
        adf(
            "ADF_V1_CONSOLIDATED",
            "adf-v1",
            "ADF v1 Consolidated Tracks",
            Format::V1,
            Lines::Tracks,
            |record, notes| consolidated::read(record, true, notes),
            true,
        ),
    ]
}

impl AdfDecoder {
    fn new(
        id: &str,
        plugin: &str,
        name: &str,
        format: Format,
        lines: Lines,
        read: Read,
        tsv_prefix: bool,
    ) -> Self {
        let (bases, default_basis) = match lines {
            Lines::Frames => (vec![MatchBasis::Frame, MatchBasis::Time], MatchBasis::Frame),
            Lines::Tracks => (vec![MatchBasis::Time], MatchBasis::Time),
        };
        Self {
            spec: Spec {
                id: id.to_owned(),
                plugin: plugin.to_owned(),
                name: name.to_owned(),
                extensions: vec![".jsonl".to_owned(), ".json".to_owned()],
                description: None,
                framing: Framing::Records,
                bases,
                default_basis,
                options: Form::default(),
                version: VERSION,
            },
            read,
            lines,
            tsv_prefix,
            format,
        }
    }
}

impl Decoder for AdfDecoder {
    fn spec(&self) -> &Spec {
        &self.spec
    }

    /// Only frames are live: tracks are consolidated after the fact, so a live source has none.
    fn reads_live(&self) -> bool {
        matches!(self.lines, Lines::Frames)
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        let filter = filter_policy(self.format);
        // A live source never ends, so it has its class filters from the start. A file is published once decoded, and
        // a header would publish it while it is still being read.
        let live = self.reads_live() && input.path().is_none();
        if live {
            sink.header(Header {
                filter: filter.clone(),
                coverage: Coverage::Sparse,
                metadata: Fields::new(),
            });
        }
        let mut notes = Notes::default();
        let mut told = [false; 2];
        'records: while let Some(record) = input.next(cancel)? {
            let read = parse_line(record.bytes, self.tsv_prefix)
                .and_then(|value| (self.read)(object(&value, "the line")?, &mut notes));
            match read {
                Ok(scenes) => {
                    // A live source never finishes to give a summary, so each kind is told at its first sighting.
                    if live {
                        for (told, warning) in told.iter_mut().zip(notes.warnings()) {
                            if let (false, Some(warning)) = (*told, warning) {
                                *told = true;
                                sink.warn(warning);
                            }
                        }
                    }
                    for scene in scenes {
                        if sink.sample(scene, self.lines.on_equal_key()).is_break() {
                            break 'records;
                        }
                    }
                }
                Err(reason) => match self.lines {
                    Lines::Frames => sink.skip(record.position, reason),
                    Lines::Tracks => {
                        return Err(Interrupt::Failed(DecodeError(format!(
                            "Line {}: {reason}",
                            record.position
                        ))));
                    }
                },
            }
        }
        Ok(DecodeSummary {
            // A warning goes to the sink or the summary, not both.
            warnings: if live {
                Vec::new()
            } else {
                notes.warnings().into_iter().flatten().collect()
            },
            filter,
            metadata: Fields::new(),
            coverage: Coverage::Sparse,
        })
    }
}

/// The JSON of a line, without the timestamp column and tab that may come before it when `tsv_prefix` allows one.
fn parse_line(line: &[u8], tsv_prefix: bool) -> Result<serde_json::Value, String> {
    let json = match memchr(b'\t', line) {
        // JSON does not start with a timestamp, and tabs inside an object are only spacing.
        Some(tab) if tsv_prefix && line.first() != Some(&b'{') => &line[tab + 1..],
        _ => line,
    };
    if json.trim_ascii().is_empty() {
        return Err("the line has a timestamp column but no JSON after it".to_owned());
    }
    serde_json::from_slice(json).map_err(|error| format!("invalid JSON: {error}"))
}

/// The class filters of this decoder's content.
fn filter_policy(format: Format) -> FilterPolicy {
    let filters = format.filters();
    let mut options: Vec<FilterOption> = filters
        .iter()
        .map(|&(id, label, class)| FilterOption {
            id: id.into(),
            label: label.into(),
            default_enabled: true,
            rule: ClassRule {
                types: vec![class.into()],
                include_empty: class == "unknown",
                exclude: false,
            },
        })
        .collect();
    options.push(FilterOption {
        id: "show_other".into(),
        label: "Other".into(),
        default_enabled: true,
        rule: ClassRule {
            types: filters
                .iter()
                .map(|&(_, _, class)| Name::from(class))
                .collect(),
            include_empty: false,
            exclude: true,
        },
    });
    FilterPolicy {
        name: format.filter_name(),
        options,
    }
}
