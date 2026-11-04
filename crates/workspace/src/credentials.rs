//! Credentials of a Live Stream Item: the redacted password type and `$NAME` references, which are expanded only
//! where the user's confirmation or the reference's binding to its host allows it.

use std::{collections::HashSet, fmt, sync::Arc};

/// A password or other secret. `Debug` prints `***`; there is no `Display` and no serialization, so it cannot reach
/// a log or a message by accident. Only [`expose`](Self::expose) reads it, where it is saved or sent.
#[derive(Clone, Default, PartialEq, Eq)]
pub struct Secret(String);

impl Secret {
    /// Wraps `value`.
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    /// The secret itself; call it only where it is written to a workspace file or sent to its host.
    pub fn expose(&self) -> &str {
        &self.0
    }

    /// Whether there is no secret.
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    /// The text when it is a `$NAME` reference, which names a secret instead of holding one and so may be shown.
    pub fn as_reference(&self) -> Option<&str> {
        reference(&self.0).map(|_| self.0.as_str())
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("***")
    }
}

/// The variable name if the whole `text` is a reference such as `$ADDRESS`.
pub(crate) fn reference(text: &str) -> Option<&str> {
    let name = text.strip_prefix('$')?;
    let mut chars = name.chars();
    let first = chars.next()?;
    let valid = (first.is_ascii_alphabetic() || first == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_');
    valid.then_some(name)
}

pub use foundation::Confirmation;

/// Looks up an environment variable by name.
type Lookup = Arc<dyn Fn(&str) -> Option<String> + Send + Sync>;

/// Where `$NAME` references get their values, and which of them the user confirmed. Cheap to clone.
#[derive(Clone)]
pub struct Environment {
    lookup: Lookup,
    confirmed: HashSet<(String, String)>,
}

impl fmt::Debug for Environment {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Environment").finish_non_exhaustive()
    }
}

impl Environment {
    /// An environment that gives `lookup(name)` for a variable and has `confirmed` allowed.
    pub fn new(
        lookup: impl Fn(&str) -> Option<String> + Send + Sync + 'static,
        confirmed: impl IntoIterator<Item = Confirmation>,
    ) -> Self {
        Self {
            lookup: Arc::new(lookup),
            confirmed: confirmed
                .into_iter()
                .map(|c| (c.reference, c.host.to_ascii_lowercase()))
                .collect(),
        }
    }

    /// The process's environment variables with nothing confirmed: only references bound to their host expand.
    pub fn of_process() -> Self {
        Self::new(|name| std::env::var(name).ok(), [])
    }

    /// `text`, or what the variable gives when it is a reference; empty when the variable is unset.
    pub(crate) fn expand(&self, text: &str) -> String {
        match reference(text) {
            Some(name) => (self.lookup)(name).unwrap_or_default(),
            None => text.to_owned(),
        }
    }

    /// Whether the user confirmed any reference for `host`.
    pub(crate) fn confirms_host(&self, host: &str) -> bool {
        let host = host.to_ascii_lowercase();
        self.confirmed
            .iter()
            .any(|(_, confirmed)| *confirmed == host)
    }

    pub(crate) fn is_confirmed(&self, reference: &str, host: &str) -> bool {
        self.confirmed
            .contains(&(reference.to_owned(), host.to_ascii_lowercase()))
    }
}
