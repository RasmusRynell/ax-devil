use std::fmt;

/// A failure the user can be shown: what went wrong and where.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UserError {
    /// What went wrong, in words for the user.
    pub reason: String,
    /// What it happened to, such as a file path or a source name.
    pub location: String,
}

impl UserError {
    /// Builds an error from its reason and location.
    pub fn new(reason: impl Into<String>, location: impl Into<String>) -> Self {
        Self {
            reason: reason.into(),
            location: location.into(),
        }
    }
}

impl fmt::Display for UserError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.reason)
    }
}

impl std::error::Error for UserError {}
