use foundation::Warning;

/// Most reasons a warning lists.
const MAX_REASONS: usize = 3;
/// Longest a listed reason gets, in characters, its ellipsis included.
const MAX_REASON_CHARS: usize = 200;

/// `text` as a warning shows it: kept whole up to 200 characters, otherwise cut to 200 characters ending in `…`.
pub fn shorten(text: &str) -> String {
    if text.chars().count() <= MAX_REASON_CHARS {
        return text.to_owned();
    }
    let mut cut: String = text.chars().take(MAX_REASON_CHARS - 1).collect();
    cut.push('…');
    cut
}

/// Collects the records a decoder skipped.
#[derive(Clone, Debug)]
pub struct SkippedRecords {
    noun: String,
    consequence: String,
    skipped: usize,
    reasons: Vec<String>,
    /// Whether a reason for a counted record was left out for lack of room.
    reasons_dropped: bool,
}

impl SkippedRecords {
    /// Starts an empty collection; `noun` is what was skipped, such as `rows`.
    pub fn new(noun: impl Into<String>, consequence: impl Into<String>) -> Self {
        Self {
            noun: noun.into(),
            consequence: consequence.into(),
            skipped: 0,
            reasons: Vec::new(),
            reasons_dropped: false,
        }
    }

    /// Counts one skipped record; only the first few reasons are kept.
    pub fn add(&mut self, reason: impl Into<String>) {
        self.skipped += 1;
        if self.reasons.len() < MAX_REASONS {
            self.reasons.push(shorten(&reason.into()));
        }
    }

    /// Adds a reason for a record already counted by [`add`](Self::add), so a record with two causes is one skip. A
    /// reason already listed is not listed again.
    pub fn also(&mut self, reason: impl Into<String>) {
        let reason = shorten(&reason.into());
        if self.reasons.contains(&reason) {
            return;
        }
        if self.reasons.len() < MAX_REASONS {
            self.reasons.push(reason);
        } else {
            self.reasons_dropped = true;
        }
    }

    /// The warning for `total` records read, or `None` when nothing was skipped.
    pub fn warning(self, total: usize) -> Option<Warning> {
        (self.skipped > 0).then(|| Warning {
            problem: format!("Skipped {} of {total} {}", self.skipped, self.noun),
            reasons: self.reasons,
            consequence: self.consequence,
            more_reasons: self.skipped > MAX_REASONS || self.reasons_dropped,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn warning_keeps_three_reasons_cut_short_and_says_when_more_exist() {
        let mut skipped = SkippedRecords::new("rows", "They are not drawn.");
        assert_eq!(skipped.clone().warning(5), None);

        for reason in ["a", "b", &"é".repeat(300), "d"] {
            skipped.add(reason);
        }
        let warning = skipped.warning(10).expect("four rows were skipped");

        assert_eq!(warning.problem, "Skipped 4 of 10 rows");
        assert_eq!(warning.reasons[..2], ["a", "b"]);
        assert_eq!(warning.reasons.len(), 3);
        assert_eq!(warning.reasons[2], format!("{}…", "é".repeat(199)));
        assert!(warning.more_reasons);
    }
}
