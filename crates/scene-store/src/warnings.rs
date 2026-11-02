//! The warnings a decode gives, bounded: a converter can repeat tiny warnings forever, and every publication carries
//! them.

use foundation::Warning;

/// The most warnings kept.
const MAX_WARNINGS: usize = 64;
/// The most text, in bytes, the kept warnings may hold.
const MAX_WARNING_BYTES: usize = 64 << 10;

/// The warnings kept so far and how many were dropped after the limits were reached.
#[derive(Default)]
pub(crate) struct Warnings {
    kept: Vec<Warning>,
    bytes: usize,
    suppressed: usize,
}

impl Warnings {
    /// Keeps `warning` unless the limits are reached, in which case it is counted.
    pub(crate) fn push(&mut self, warning: Warning) {
        let bytes = warning.problem.len()
            + warning.consequence.len()
            + warning.reasons.iter().map(String::len).sum::<usize>();
        if self.kept.len() >= MAX_WARNINGS || self.bytes + bytes > MAX_WARNING_BYTES {
            self.suppressed += 1;
            return;
        }
        self.bytes += bytes;
        self.kept.push(warning);
    }

    /// The kept warnings, then one saying how many were suppressed when any were.
    pub(crate) fn into_list(self) -> Vec<Warning> {
        let Self {
            mut kept,
            suppressed,
            ..
        } = self;
        if suppressed > 0 {
            kept.push(Warning {
                problem: format!("{suppressed} more warnings suppressed"),
                reasons: Vec::new(),
                consequence: "They are not listed.".to_owned(),
                more_reasons: false,
            });
        }
        kept
    }

    /// [`into_list`](Self::into_list) for a copy.
    pub(crate) fn list(&self) -> Vec<Warning> {
        Self {
            kept: self.kept.clone(),
            bytes: self.bytes,
            suppressed: self.suppressed,
        }
        .into_list()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn warning(problem: String) -> Warning {
        Warning {
            problem,
            reasons: Vec::new(),
            consequence: String::new(),
            more_reasons: false,
        }
    }

    #[test]
    fn a_flood_of_warnings_is_cut_by_count_and_by_text_and_the_rest_is_counted() {
        let mut flood = Warnings::default();
        for n in 0..1_000 {
            flood.push(warning(format!("w{n}")));
        }
        let list = flood.into_list();
        assert_eq!(list.len(), MAX_WARNINGS + 1);
        assert_eq!(
            list[MAX_WARNINGS].problem,
            format!("{} more warnings suppressed", 1_000 - MAX_WARNINGS)
        );

        let mut long = Warnings::default();
        long.push(warning("x".repeat(MAX_WARNING_BYTES)));
        long.push(warning("one byte too many".into()));
        let list = long.into_list();
        assert_eq!(list.len(), 2);
        assert_eq!(list[1].problem, "1 more warnings suppressed");
    }
}
