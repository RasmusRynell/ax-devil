//! The plugin's JSON Lines turned into calls on a [`SceneSink`] (the format in the write-plugin skill).

use std::borrow::Cow;

use foundation::Warning;
use handler_api::{Header, OnEqualKey, SceneSink, shorten};
use scene::{ClassRule, Coverage, Fields, FilterOption, FilterPolicy, Value, serial};
use serde::Deserialize;
use serde_json::value::RawValue;

use crate::{
    run::{LineHandler, Next},
    wire::{ErrorIn, WarningIn, cut, json_reason, load_warning},
};

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
    error: Option<ErrorIn>,
    done: Option<DoneIn>,
}

/// `{"done":{}}`: the answer to a record is complete.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DoneIn {}

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

/// What a line asks for.
enum Parsed {
    Header(Header),
    Sample(scene::Scene, OnEqualKey),
    Warning(Warning),
    Skipped(String),
    Error(String),
    Done,
}

/// The name of the filter policy of every plugin.
pub(crate) const POLICY_NAME: &str = "plugin";

/// Feeds a sink from lines.
pub(crate) struct Records<'a> {
    sink: &'a mut dyn SceneSink,
    /// The header the sink was given: the first of the run, and only while no sample had reached it.
    header: Option<Header>,
    /// The sink was given a sample.
    sunk: bool,
    /// The process has given a header or a sample, so it may not give a header.
    begun: bool,
    /// The record being answered; `None` while the plugin reads a whole file. Lines are then reported at the record's
    /// position instead of their own number, and `done` ends the answer.
    position: Option<u64>,
}

impl<'a> Records<'a> {
    pub(crate) fn new(sink: &'a mut dyn SceneSink) -> Self {
        Self {
            sink,
            header: None,
            sunk: false,
            begun: false,
            position: None,
        }
    }

    /// The lines that follow answer the record at `position`.
    pub(crate) fn at(&mut self, position: u64) {
        self.position = Some(position);
    }

    /// A new process starts: it may give a header, which the sink gets only if it has none and no sample yet.
    pub(crate) fn new_process(&mut self) {
        self.begun = false;
    }

    /// The record being answered could not be, so it is skipped.
    pub(crate) fn skip_record(&mut self, reason: String) {
        self.skip(0, reason);
    }

    /// Skips at the record's position, or at line `number` of a whole-file run.
    fn skip(&mut self, number: usize, reason: String) {
        self.sink
            .skip(self.position.unwrap_or(number as u64), reason);
    }

    /// The header the sink was given.
    pub(crate) fn finish(self) -> Option<Header> {
        self.header
    }

    /// Where a line is: its own number, or the record it answers.
    fn place(&self, number: usize) -> String {
        match self.position {
            Some(position) => format!("record {position}"),
            None => format!("line {number}"),
        }
    }

    fn parse(&self, bytes: &[u8]) -> Result<Parsed, String> {
        let text = std::str::from_utf8(bytes).map_err(|_| "is not valid UTF-8".to_owned())?;
        let line: LineIn<'_> = serde_json::from_str(text).map_err(|error| json_reason(&error))?;
        let scene = |raw: &RawValue| {
            serial::scene_from_json_slice(raw.get().as_bytes()).map_err(|error| error.to_string())
        };
        let LineIn {
            header,
            sample,
            replace,
            warning,
            skipped,
            error,
            done,
        } = line;
        match (header, sample, replace, warning, skipped, error, done) {
            (Some(raw), None, None, None, None, None, None) => {
                self.header_from(raw).map(Parsed::Header)
            }
            (None, Some(raw), None, None, None, None, None) => {
                Ok(Parsed::Sample(scene(raw)?, OnEqualKey::Merge))
            }
            (None, None, Some(raw), None, None, None, None) => {
                Ok(Parsed::Sample(scene(raw)?, OnEqualKey::Replace))
            }
            (None, None, None, Some(warning), None, None, None) => {
                Ok(Parsed::Warning(load_warning(warning)))
            }
            (None, None, None, None, Some(reason), None, None) => {
                Ok(Parsed::Skipped(shorten(&reason)))
            }
            (None, None, None, None, None, Some(error), None) => {
                Ok(Parsed::Error(cut(&error.reason)))
            }
            (None, None, None, None, None, None, Some(DoneIn {})) if self.position.is_some() => {
                Ok(Parsed::Done)
            }
            _ => Err(format!(
                "needs exactly one of header, sample, replace, warning, skipped{}",
                if self.position.is_some() {
                    ", error or done"
                } else {
                    " or error"
                }
            )),
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
        let filter = FilterPolicy::try_new(POLICY_NAME, options)
            .map_err(|error| format!("the header's filter: {error}"))?;
        Ok(Header {
            filter,
            coverage: match header.coverage {
                Some(CoverageIn::Dense) => Coverage::Dense,
                Some(CoverageIn::Sparse) | None => Coverage::Sparse,
            },
            metadata,
        })
    }
}

impl LineHandler for Records<'_> {
    /// Handles one line, without its `\n`. Blank lines do nothing.
    fn line(&mut self, number: usize, bytes: &[u8]) -> Next {
        let bytes = bytes.trim_ascii();
        if bytes.is_empty() {
            return Next::Continue;
        }
        match self.parse(bytes) {
            Ok(Parsed::Header(header)) => {
                if self.begun {
                    return Next::Stop(format!(
                        "{}: a header must come once, before the first sample",
                        self.place(number)
                    ));
                }
                self.begun = true;
                // A restarted plugin gives its header again; the sink has the first, or none once samples came.
                if self.header.is_none() && !self.sunk {
                    self.sink.header(header.clone());
                    self.header = Some(header);
                }
            }
            Ok(Parsed::Warning(warning)) => self.sink.warn(warning),
            Ok(Parsed::Sample(scene, on_equal_key)) => {
                self.begun = true;
                self.sunk = true;
                if self.sink.sample(scene, on_equal_key).is_break() {
                    return Next::SinkFull;
                }
            }
            Ok(Parsed::Skipped(reason)) => self.skip(number, reason),
            Ok(Parsed::Error(reason)) => return Next::Stop(reason),
            Ok(Parsed::Done) => return Next::Done,
            Err(reason) => self.skip(number, reason),
        }
        Next::Continue
    }
}
