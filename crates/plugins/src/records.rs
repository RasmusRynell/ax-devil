//! The plugin's JSON Lines turned into calls on a [`SceneSink`] (the format in the write-plugin skill).

use std::borrow::Cow;

use foundation::Warning;
use handler_api::{Header, OnEqualKey, SceneSink};
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

/// What a line asks for.
enum Parsed {
    Header(Header),
    Sample(scene::Scene, OnEqualKey),
    Warning(Warning),
    Skipped(String),
    Error(String),
}

/// The name of the filter policy of every plugin.
pub(crate) const POLICY_NAME: &str = "plugin";

/// Feeds a sink from lines.
pub(crate) struct Records<'a> {
    sink: &'a mut dyn SceneSink,
    header: Option<Header>,
    sampled: bool,
}

impl<'a> Records<'a> {
    pub(crate) fn new(sink: &'a mut dyn SceneSink) -> Self {
        Self {
            sink,
            header: None,
            sampled: false,
        }
    }

    /// The header the file declared.
    pub(crate) fn finish(self) -> Option<Header> {
        self.header
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
        } = line;
        match (header, sample, replace, warning, skipped, error) {
            (Some(raw), None, None, None, None, None) => self.header_from(raw).map(Parsed::Header),
            (None, Some(raw), None, None, None, None) => {
                Ok(Parsed::Sample(scene(raw)?, OnEqualKey::Merge))
            }
            (None, None, Some(raw), None, None, None) => {
                Ok(Parsed::Sample(scene(raw)?, OnEqualKey::Replace))
            }
            (None, None, None, Some(warning), None, None) => {
                Ok(Parsed::Warning(load_warning(warning)))
            }
            (None, None, None, None, Some(reason), None) => Ok(Parsed::Skipped(cut(&reason))),
            (None, None, None, None, None, Some(error)) => Ok(Parsed::Error(cut(&error.reason))),
            _ => Err(
                "needs exactly one of header, sample, replace, warning, skipped or error"
                    .to_owned(),
            ),
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
                if self.header.is_some() || self.sampled {
                    return Next::Stop(format!(
                        "line {number}: a header must come once, before the first sample"
                    ));
                }
                self.sink.header(header.clone());
                self.header = Some(header);
            }
            Ok(Parsed::Warning(warning)) => self.sink.warn(warning),
            Ok(Parsed::Sample(scene, on_equal_key)) => {
                self.sampled = true;
                if self.sink.sample(scene, on_equal_key).is_break() {
                    return Next::SinkFull;
                }
            }
            Ok(Parsed::Skipped(reason)) => self.sink.skip(number as u64, reason),
            Ok(Parsed::Error(reason)) => return Next::Stop(reason),
            Err(reason) => self.sink.skip(number as u64, reason),
        }
        Next::Continue
    }
}
