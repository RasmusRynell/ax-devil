//! The lines every operation shares: a warning and an error, and how a plugin's text is cut to what the UI shows.

use foundation::Warning;
use handler_api::shorten;
use serde::Deserialize;

/// Most reasons a plugin's warning lists, and the longest any of its texts is.
const MAX_REASONS: usize = 3;
const MAX_TEXT_CHARS: usize = 200;

/// A failure the plugin reports: it ends the run. `field` and `path` say what a scan or resolve was about and mean
/// nothing to a decode.
#[derive(Deserialize)]
pub(crate) struct ErrorIn {
    pub reason: String,
    pub field: Option<String>,
    pub path: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct WarningIn {
    problem: String,
    #[serde(default)]
    reasons: Vec<String>,
    #[serde(default)]
    consequence: String,
}

/// A plugin's warning, cut to what a lane shows.
pub(crate) fn load_warning(warning: WarningIn) -> Warning {
    Warning {
        problem: shorten(&warning.problem),
        more_reasons: warning.reasons.len() > MAX_REASONS,
        reasons: warning
            .reasons
            .iter()
            .take(MAX_REASONS)
            .map(|reason| shorten(reason))
            .collect(),
        consequence: shorten(&warning.consequence),
    }
}

/// Text that is not a warning, such as a name that identifies a playlist: hard-cut, never given an ellipsis.
pub(crate) fn cut(text: &str) -> String {
    text.chars().take(MAX_TEXT_CHARS).collect()
}

/// Why JSON failed to read, without the position inside the line.
pub(crate) fn json_reason(error: &serde_json::Error) -> String {
    if error.is_data() {
        let text = error.to_string();
        text.split(" at line ").next().unwrap_or(&text).to_owned()
    } else {
        "is not JSON".to_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_long_warning_ends_with_an_ellipsis_but_a_name_is_hard_cut() {
        let text = "ü".repeat(250);
        let warning: WarningIn =
            serde_json::from_value(serde_json::json!({"problem": text})).expect("a warning");

        assert_eq!(
            load_warning(warning).problem,
            format!("{}…", "ü".repeat(199))
        );
        assert_eq!(cut(&text), "ü".repeat(200));
    }
}
