//! The converter's JSON Lines turned into calls on a [`SceneSink`] (the format in the write-plugin skill).

use std::borrow::Cow;

use scene::{ClassRule, Coverage, Fields, FilterOption, FilterPolicy, Value, serial};
use scene_decoders::{Header, LoadWarning, OnEqualKey, SceneSink, SkippedRecords};
use serde::Deserialize;
use serde_json::value::RawValue;

/// Most reasons a converter's warning lists, and the longest any of its texts is.
const MAX_REASONS: usize = 3;
const MAX_TEXT_CHARS: usize = 200;

/// One line: exactly one of these keys.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LineIn<'a> {
    #[serde(borrow)]
    header: Option<&'a RawValue>,
    #[serde(borrow)]
    sample: Option<&'a RawValue>,
    #[serde(borrow)]
    replace: Option<&'a RawValue>,
    warning: Option<WarningIn>,
    skipped: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HeaderIn {
    coverage: Option<CoverageIn>,
    #[serde(default)]
    filter: Vec<FilterIn>,
    metadata: Option<Value>,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum CoverageIn {
    Dense,
    Sparse,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FilterIn {
    id: String,
    label: String,
    #[serde(default = "on")]
    default: bool,
    #[serde(default)]
    types: Vec<String>,
    #[serde(default)]
    include_empty: bool,
    #[serde(default)]
    exclude: bool,
}

fn on() -> bool {
    true
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WarningIn {
    problem: String,
    #[serde(default)]
    reasons: Vec<String>,
    #[serde(default)]
    consequence: String,
}

/// What a line asks for.
enum Parsed {
    Header(Header),
    Sample(scene::Scene, OnEqualKey),
    Warning(LoadWarning),
    Skipped(String),
}

/// What the host does after a line.
pub(crate) enum Next {
    Continue,
    /// The sink cannot take more.
    SinkFull,
    /// The converter broke the protocol in a way that stops the run.
    Protocol(String),
}

/// Feeds a sink from lines and counts what it skipped.
pub(crate) struct Records<'a> {
    sink: &'a mut dyn SceneSink,
    policy_name: &'static str,
    header: Option<Header>,
    sampled: bool,
    seen: usize,
    skipped: SkippedRecords,
}

impl<'a> Records<'a> {
    /// `policy_name` names the filter policy a header declares: the converter's id.
    pub(crate) fn new(sink: &'a mut dyn SceneSink, policy_name: &'static str) -> Self {
        Self {
            sink,
            policy_name,
            header: None,
            sampled: false,
            seen: 0,
            skipped: SkippedRecords::new("records", "Objects in skipped records are not shown."),
        }
    }

    /// Handles one line, without its `\n`. Blank lines do nothing.
    pub(crate) fn line(&mut self, number: usize, bytes: &[u8]) -> Next {
        let bytes = bytes.trim_ascii();
        if bytes.is_empty() {
            return Next::Continue;
        }
        match self.parse(bytes) {
            Ok(Parsed::Header(header)) => {
                if self.header.is_some() || self.sampled {
                    return Next::Protocol(format!(
                        "line {number}: a header must come once, before the first sample"
                    ));
                }
                self.sink.header(header.clone());
                self.header = Some(header);
            }
            Ok(Parsed::Warning(warning)) => self.sink.warn(warning),
            Ok(Parsed::Sample(scene, on_equal_key)) => {
                self.seen += 1;
                self.sampled = true;
                if self.sink.sample(scene, on_equal_key).is_break() {
                    return Next::SinkFull;
                }
            }
            Ok(Parsed::Skipped(reason)) => {
                self.seen += 1;
                self.skipped.add(reason);
            }
            Err(reason) => {
                self.seen += 1;
                self.skipped.add(format!("line {number}: {reason}"));
            }
        }
        Next::Continue
    }

    /// Sends the warning about skipped records and gives back the header the file declared.
    pub(crate) fn finish(self) -> Option<Header> {
        if let Some(warning) = self.skipped.warning(self.seen) {
            self.sink.warn(warning);
        }
        self.header
    }

    fn parse(&self, bytes: &[u8]) -> Result<Parsed, String> {
        let text = std::str::from_utf8(bytes).map_err(|_| "is not valid UTF-8".to_owned())?;
        let line: LineIn<'_> = serde_json::from_str(text).map_err(|error| json_reason(&error))?;
        let scene = |raw: &RawValue| {
            serial::scene_from_json_slice(raw.get().as_bytes()).map_err(|error| error.to_string())
        };
        match line {
            LineIn {
                header: Some(raw),
                sample: None,
                replace: None,
                warning: None,
                skipped: None,
            } => self.header_from(raw).map(Parsed::Header),
            LineIn {
                header: None,
                sample: Some(raw),
                replace: None,
                warning: None,
                skipped: None,
            } => Ok(Parsed::Sample(scene(raw)?, OnEqualKey::Merge)),
            LineIn {
                header: None,
                sample: None,
                replace: Some(raw),
                warning: None,
                skipped: None,
            } => Ok(Parsed::Sample(scene(raw)?, OnEqualKey::Replace)),
            LineIn {
                header: None,
                sample: None,
                replace: None,
                warning: Some(warning),
                skipped: None,
            } => Ok(Parsed::Warning(load_warning(warning))),
            LineIn {
                header: None,
                sample: None,
                replace: None,
                warning: None,
                skipped: Some(reason),
            } => Ok(Parsed::Skipped(cut(&reason))),
            _ => Err("needs exactly one of header, sample, replace, warning or skipped".to_owned()),
        }
    }

    fn header_from(&self, raw: &RawValue) -> Result<Header, String> {
        let header: HeaderIn =
            serde_json::from_str(raw.get()).map_err(|error| json_reason(&error))?;
        let metadata = match header.metadata {
            None | Some(Value::Null) => Fields::new(),
            Some(Value::Map(fields)) => fields,
            Some(_) => return Err("the header's metadata must be an object".to_owned()),
        };
        let options = header
            .filter
            .into_iter()
            .map(|option| FilterOption {
                id: Cow::Owned(option.id),
                label: Cow::Owned(option.label),
                default_enabled: option.default,
                rule: ClassRule {
                    types: option.types.into_iter().map(Cow::Owned).collect(),
                    include_empty: option.include_empty,
                    exclude: option.exclude,
                },
            })
            .collect();
        Ok(Header {
            filter: FilterPolicy {
                name: self.policy_name,
                options,
            },
            coverage: match header.coverage {
                Some(CoverageIn::Dense) => Coverage::Dense,
                Some(CoverageIn::Sparse) | None => Coverage::Sparse,
            },
            metadata,
        })
    }
}

/// A converter's warning, cut to what a lane shows.
fn load_warning(warning: WarningIn) -> LoadWarning {
    LoadWarning {
        problem: cut(&warning.problem),
        more_reasons: warning.reasons.len() > MAX_REASONS,
        reasons: warning
            .reasons
            .iter()
            .take(MAX_REASONS)
            .map(|reason| cut(reason))
            .collect(),
        consequence: cut(&warning.consequence),
    }
}

fn cut(text: &str) -> String {
    text.chars().take(MAX_TEXT_CHARS).collect()
}

/// Why JSON failed to read, without the position inside the line.
fn json_reason(error: &serde_json::Error) -> String {
    if error.is_data() {
        let text = error.to_string();
        text.split(" at line ").next().unwrap_or(&text).to_owned()
    } else {
        "is not JSON".to_owned()
    }
}
