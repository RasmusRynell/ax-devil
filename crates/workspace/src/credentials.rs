//! Credentials of a Live Stream Item: the redacted password type and `$NAME` references, which are expanded silently
//! when the item resolves.

use std::{fmt, sync::Arc};

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

/// Looks up an environment variable by name.
type Lookup = Arc<dyn Fn(&str) -> Option<String> + Send + Sync>;

/// Where `$NAME` references get their values, if anywhere. Cheap to clone.
#[derive(Clone)]
pub struct Environment {
    lookup: Option<Lookup>,
}

impl fmt::Debug for Environment {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Environment").finish_non_exhaustive()
    }
}

impl Environment {
    /// An environment that gives `lookup(name)` for a variable.
    pub fn new(lookup: impl Fn(&str) -> Option<String> + Send + Sync + 'static) -> Self {
        Self {
            lookup: Some(Arc::new(lookup)),
        }
    }

    /// The process's environment variables.
    pub fn of_process() -> Self {
        Self::new(|name| std::env::var(name).ok())
    }

    /// An environment with no variables: a reference stays the literal text it was typed as. For a remote viewer,
    /// whose text must never read this machine's environment.
    pub fn literal() -> Self {
        Self { lookup: None }
    }

    /// `text`, or what the variable gives when it is a reference; empty when the variable is unset. Nothing asks
    /// first, as in the reference app. A literal environment gives `text` as it is.
    pub(crate) fn expand(&self, text: &str) -> String {
        match (reference(text), &self.lookup) {
            (Some(name), Some(lookup)) => lookup(name).unwrap_or_default(),
            _ => text.to_owned(),
        }
    }
}
