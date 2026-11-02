//! What a decode reports besides its Scenes, kept in the artifact so a reopen needs no decode.

use std::{
    collections::BTreeSet,
    sync::{Mutex, PoisonError},
};

use foundation::Warning;
use handler_api::DecodeSummary;
use scene::{ClassRule, Coverage, Fields, FilterOption, FilterPolicy, serial};
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize)]
struct WarningWire {
    problem: String,
    reasons: Vec<String>,
    consequence: String,
    more_reasons: bool,
}

#[derive(Clone, Serialize, Deserialize)]
struct OptionWire {
    id: String,
    label: String,
    default_enabled: bool,
    types: Vec<String>,
    include_empty: bool,
    exclude: bool,
}

/// The stored form of a [`DecodeSummary`].
#[derive(Clone, Serialize, Deserialize)]
pub(crate) struct Summary {
    dense: bool,
    warnings: Vec<WarningWire>,
    filter_name: String,
    filter_options: Vec<OptionWire>,
    /// [`serial::encode_fields`] of the file's metadata.
    metadata: Vec<u8>,
}

/// A [`Summary`] turned back into what it was made from.
pub(crate) struct Restored {
    pub coverage: Coverage,
    pub warnings: Vec<Warning>,
    pub filter: FilterPolicy,
    pub metadata: Fields,
}

impl Summary {
    pub(crate) fn of(decoded: &DecodeSummary) -> Result<Self, String> {
        let mut metadata = Vec::new();
        serial::encode_fields(&decoded.metadata, &mut metadata).map_err(|e| e.to_string())?;
        Ok(Self {
            dense: decoded.coverage == Coverage::Dense,
            warnings: decoded.warnings.iter().map(warning_wire).collect(),
            filter_name: decoded.filter.name.to_string(),
            filter_options: decoded
                .filter
                .options
                .iter()
                .map(|option| OptionWire {
                    id: option.id.to_string(),
                    label: option.label.to_string(),
                    default_enabled: option.default_enabled,
                    types: option.rule.types.iter().map(ToString::to_string).collect(),
                    include_empty: option.rule.include_empty,
                    exclude: option.rule.exclude,
                })
                .collect(),
            metadata,
        })
    }

    /// Adds a warning the store itself found.
    pub(crate) fn warn(&mut self, warning: &Warning) {
        self.warnings.push(warning_wire(warning));
    }

    pub(crate) fn restore(self) -> Result<Restored, String> {
        Ok(Restored {
            coverage: if self.dense {
                Coverage::Dense
            } else {
                Coverage::Sparse
            },
            warnings: self
                .warnings
                .into_iter()
                .map(|w| Warning {
                    problem: w.problem,
                    reasons: w.reasons,
                    consequence: w.consequence,
                    more_reasons: w.more_reasons,
                })
                .collect(),
            filter: FilterPolicy {
                name: intern(&self.filter_name),
                options: self
                    .filter_options
                    .into_iter()
                    .map(|o| FilterOption {
                        id: o.id.into(),
                        label: o.label.into(),
                        default_enabled: o.default_enabled,
                        rule: ClassRule {
                            types: o.types.into_iter().map(Into::into).collect(),
                            include_empty: o.include_empty,
                            exclude: o.exclude,
                        },
                    })
                    .collect(),
            },
            metadata: serial::decode_fields(&self.metadata).map_err(|e| e.to_string())?,
        })
    }
}

fn warning_wire(warning: &Warning) -> WarningWire {
    WarningWire {
        problem: warning.problem.clone(),
        reasons: warning.reasons.clone(),
        consequence: warning.consequence.clone(),
        more_reasons: warning.more_reasons,
    }
}

/// A policy name is `&'static str`; a name read from an artifact is made static once per distinct text. Names are
/// decoder ids, so the set stays as small as the set of decoders.
fn intern(name: &str) -> &'static str {
    static NAMES: Mutex<BTreeSet<&'static str>> = Mutex::new(BTreeSet::new());
    let mut names = NAMES.lock().unwrap_or_else(PoisonError::into_inner);
    if let Some(known) = names.get(name) {
        return known;
    }
    let leaked: &'static str = Box::leak(name.to_owned().into_boxed_str());
    names.insert(leaked);
    leaked
}
