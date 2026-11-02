use foundation::Warning;

/// Most reasons a warning lists.
const MAX_REASONS: usize = 3;
/// Longest a listed reason gets, in characters.
const MAX_REASON_CHARS: usize = 200;

/// Collects the records a decoder skipped.
#[derive(Clone, Debug)]
pub struct SkippedRecords {
    noun: String,
    consequence: String,
    skipped: usize,
    reasons: Vec<String>,
}

impl SkippedRecords {
    /// Starts an empty collection; `noun` is what was skipped, such as `rows`.
    pub fn new(noun: impl Into<String>, consequence: impl Into<String>) -> Self {
        Self {
            noun: noun.into(),
            consequence: consequence.into(),
            skipped: 0,
            reasons: Vec::new(),
        }
    }

    /// Counts one skipped record; only the first few reasons are kept.
    pub fn add(&mut self, reason: impl Into<String>) {
        self.skipped += 1;
        if self.reasons.len() < MAX_REASONS {
            self.reasons
                .push(reason.into().chars().take(MAX_REASON_CHARS).collect());
        }
    }

    /// The warning for `total` records read, or `None` when nothing was skipped.
    pub fn warning(self, total: usize) -> Option<Warning> {
        (self.skipped > 0).then(|| Warning {
            problem: format!("Skipped {} of {total} {}", self.skipped, self.noun),
            reasons: self.reasons,
            consequence: self.consequence,
            more_reasons: self.skipped > MAX_REASONS,
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

        for reason in ["a", "b", &"x".repeat(300), "d"] {
            skipped.add(reason);
        }
        let warning = skipped.warning(10).expect("four rows were skipped");

        assert_eq!(warning.problem, "Skipped 4 of 10 rows");
        assert_eq!(warning.reasons[..2], ["a", "b"]);
        assert_eq!(warning.reasons.len(), 3);
        assert_eq!(warning.reasons[2].chars().count(), 200);
        assert!(warning.more_reasons);
    }
}
