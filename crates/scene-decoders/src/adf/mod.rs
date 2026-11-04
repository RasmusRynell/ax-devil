//! Axis Analytics Data Format (ADF), beta and v1: JSON, one object per line, as frames or as consolidated tracks.
//!
//! A line may start with a timestamp column and a tab, which is dropped.

mod consolidated;
mod frame;
mod parse;

use std::sync::Arc;

use foundation::{CancelToken, Interrupt};
use handler_api::{
    DecodeError, DecodeSummary, Decoder, Form, Framing, Header, Input, OnEqualKey, SceneSink, Spec,
    Values,
};
use memchr::memchr;
use parse::{Object, object};
use scene::{ClassRule, Coverage, Fields, FilterOption, FilterPolicy, MatchBasis, Name, Scene};

/// Filter id, label and the class it shows; `show_other` is added after them.
const FILTERS: [(&str, &str, &str); 14] = [
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
    ("show_bags", "Bags", "bag"),
    ("show_faces", "Faces", "face"),
];

/// What a line of a file is.
#[derive(Clone, Copy)]
enum Lines {
    /// A frame: its Scene replaces an earlier one of its time, and its place in the file is its frame.
    Frames,
    /// Tracks: their observations are Scenes of their own times, which join the others of that time.
    Tracks,
}

/// Reads one object of a line into the Scenes it gives.
type Read = fn(&Object) -> Result<Vec<Scene>, String>;

/// One of the four ADF decoders.
struct AdfDecoder {
    spec: Spec,
    read: Read,
    on_equal_key: OnEqualKey,
    /// The name of the decoder's filter policy.
    filters: &'static str,
}

/// Every ADF decoder: beta, then v1, each as frames and as consolidated tracks.
pub(crate) fn decoders() -> Vec<Arc<dyn Decoder>> {
    let adf = |id, plugin, name, filters, lines, read| -> Arc<dyn Decoder> {
        Arc::new(AdfDecoder::new(id, plugin, name, filters, lines, read))
    };
    vec![
        adf(
            "ADF_BETA_FRAME",
            "adf-v1-beta",
            "ADF v1 Beta Frame",
            "adf_beta",
            Lines::Frames,
            frame::read_beta,
        ),
        adf(
            "ADF_BETA_CONSOLIDATED",
            "adf-v1-beta",
            "ADF v1 Beta Consolidated",
            "adf_beta",
            Lines::Tracks,
            |record| consolidated::read(record, false),
        ),
        adf(
            "ADF_V1_FRAME",
            "adf-v1",
            "ADF v1 Frame",
            "adf_v1_frame",
            Lines::Frames,
            frame::read_v1,
        ),
        adf(
            "ADF_V1_CONSOLIDATED",
            "adf-v1",
            "ADF v1 Consolidated Tracks",
            "adf_v1_frame",
            Lines::Tracks,
            |record| consolidated::read(record, true),
        ),
    ]
}

impl AdfDecoder {
    fn new(
        id: &str,
        plugin: &str,
        name: &str,
        filters: &'static str,
        lines: Lines,
        read: Read,
    ) -> Self {
        let (bases, default_basis, on_equal_key) = match lines {
            Lines::Frames => (
                vec![MatchBasis::Frame, MatchBasis::Time],
                MatchBasis::Frame,
                OnEqualKey::Replace,
            ),
            Lines::Tracks => (vec![MatchBasis::Time], MatchBasis::Time, OnEqualKey::Merge),
        };
        Self {
            spec: Spec {
                id: id.to_owned(),
                plugin: plugin.to_owned(),
                name: name.to_owned(),
                extensions: vec![".jsonl".to_owned(), ".json".to_owned()],
                framing: Framing::Records,
                bases,
                default_basis,
                options: Form::default(),
                version: 1,
            },
            read,
            on_equal_key,
            filters,
        }
    }
}

impl Decoder for AdfDecoder {
    fn spec(&self) -> &Spec {
        &self.spec
    }

    fn decode(
        &self,
        input: &mut dyn Input,
        _options: &Values,
        sink: &mut dyn SceneSink,
        cancel: &CancelToken,
    ) -> Result<DecodeSummary, Interrupt<DecodeError>> {
        let filter = filter_policy(self.filters);
        // A live source never ends, so it has its class filters from the start. A file is published once decoded, and
        // a header would publish it while it is still being read.
        if input.path().is_none() {
            sink.header(Header {
                filter: filter.clone(),
                coverage: Coverage::Sparse,
                metadata: Fields::new(),
            });
        }
        'records: while let Some(record) = input.next(cancel)? {
            let read =
                parse_line(record.bytes).and_then(|value| (self.read)(object(&value, "the line")?));
            match read {
                Ok(scenes) => {
                    for scene in scenes {
                        if sink.sample(scene, self.on_equal_key).is_break() {
                            break 'records;
                        }
                    }
                }
                Err(reason) => sink.skip(record.position, reason),
            }
        }
        Ok(DecodeSummary {
            warnings: Vec::new(),
            filter,
            metadata: Fields::new(),
            coverage: Coverage::Sparse,
        })
    }
}

/// The JSON of a line, without the timestamp column and tab that may come before it.
fn parse_line(line: &[u8]) -> Result<serde_json::Value, String> {
    let json = match memchr(b'\t', line) {
        // JSON does not start with a timestamp, and tabs inside an object are only spacing.
        Some(tab) if line.first() != Some(&b'{') => &line[tab + 1..],
        _ => line,
    };
    if json.trim_ascii().is_empty() {
        return Err("the line has a timestamp column but no JSON after it".to_owned());
    }
    serde_json::from_slice(json).map_err(|error| format!("invalid JSON: {error}"))
}

/// The class filters of this decoder's content.
fn filter_policy(name: &'static str) -> FilterPolicy {
    let mut options: Vec<FilterOption> = FILTERS
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
            types: FILTERS
                .iter()
                .map(|&(_, _, class)| Name::from(class))
                .collect(),
            include_empty: false,
            exclude: true,
        },
    });
    FilterPolicy { name, options }
}
