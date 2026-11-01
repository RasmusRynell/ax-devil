//! Located problems in a catalog, and the stable codes that name their kinds.

/// One problem in a catalog, with where it is.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct CatalogDiagnostic {
    /// A stable code for the kind of problem, one of [`codes`].
    pub code: &'static str,
    /// The JSON pointer (RFC 6901) of the offending value in the catalog; empty for the whole document.
    pub location: String,
    /// What is wrong, in words for the user.
    pub message: String,
}

/// The codes a [`CatalogDiagnostic`] carries.
pub mod codes {
    /// The file is not JSON.
    pub const INVALID_JSON: &str = "invalid_json";
    /// An object names the same key twice.
    pub const DUPLICATE_DEFINITION: &str = "duplicate_definition";
    /// The catalog is for another schema version.
    pub const UNSUPPORTED_VERSION: &str = "unsupported_version";
    /// A size, depth, nesting or expansion limit is exceeded.
    pub const EXECUTION_LIMIT: &str = "execution_limit";
    /// An object, list or key is missing, unexpected or of the wrong shape.
    pub const INVALID_STRUCTURE: &str = "invalid_structure";
    /// A value breaks its type, its bounds or a rule of the language.
    pub const INVALID_VALUE: &str = "invalid_value";
    /// A reference names a scope, value or field that does not exist.
    pub const UNKNOWN_REFERENCE: &str = "unknown_reference";
    /// A call names an operation that does not exist.
    pub const UNKNOWN_OPERATION: &str = "unknown_operation";
    /// A step names a template that does not exist.
    pub const UNKNOWN_TEMPLATE: &str = "unknown_template";
    /// Templates call each other in a cycle.
    pub const TEMPLATE_CYCLE: &str = "template_cycle";
    /// Calculated values refer to each other in a cycle.
    pub const VALUE_CYCLE: &str = "value_cycle";
    /// A value's type does not fit where it is used.
    pub const TYPE_MISMATCH: &str = "type_mismatch";
    /// Evaluation failed for a reason the language does not name.
    pub const EVALUATION_ERROR: &str = "evaluation_error";
}

/// A JSON pointer being built while walking the document.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub(crate) struct Pointer(String);

impl Pointer {
    /// The whole document.
    pub(crate) fn root() -> Self {
        Self(String::new())
    }

    /// The member `key` of the object here.
    pub(crate) fn key(&self, key: &str) -> Self {
        let escaped = key.replace('~', "~0").replace('/', "~1");
        Self(format!("{}/{escaped}", self.0))
    }

    /// Item `index` of the list here.
    pub(crate) fn index(&self, index: usize) -> Self {
        Self(format!("{}/{index}", self.0))
    }

    /// The pointer text.
    pub(crate) fn as_str(&self) -> &str {
        &self.0
    }
}

/// A problem found while compiling, located once its place is known.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Problem {
    pub(crate) code: &'static str,
    pub(crate) location: Option<Pointer>,
    pub(crate) message: String,
}

impl Problem {
    /// A problem of `code` whose place the caller fills in.
    pub(crate) fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            location: None,
            message: message.into(),
        }
    }

    /// An `invalid_value` problem whose place the caller fills in.
    pub(crate) fn value(message: impl Into<String>) -> Self {
        Self::new(codes::INVALID_VALUE, message)
    }

    /// The problem at `location`.
    pub(crate) fn at(self, location: &Pointer) -> Self {
        Self {
            location: Some(location.clone()),
            ..self
        }
    }

    /// The problem at `location` unless it is located already.
    pub(crate) fn or_at(self, location: &Pointer) -> Self {
        if self.location.is_some() {
            self
        } else {
            self.at(location)
        }
    }

    /// The diagnostic users see; an unlocated problem is about the whole document.
    pub(crate) fn diagnostic(self) -> CatalogDiagnostic {
        CatalogDiagnostic {
            code: self.code,
            location: self.location.map(|pointer| pointer.0).unwrap_or_default(),
            message: self.message,
        }
    }
}

/// Every problem found so far, without repeats.
#[derive(Debug, Default)]
pub(crate) struct Problems(Vec<CatalogDiagnostic>);

impl Problems {
    /// Records `problem` unless the same one is recorded already.
    pub(crate) fn push(&mut self, problem: Problem) {
        let diagnostic = problem.diagnostic();
        if !self.0.contains(&diagnostic) {
            self.0.push(diagnostic);
        }
    }

    /// How many problems are recorded.
    pub(crate) fn len(&self) -> usize {
        self.0.len()
    }

    /// Whether nothing is recorded.
    pub(crate) fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    /// The recorded problems in the order they were found.
    pub(crate) fn into_vec(self) -> Vec<CatalogDiagnostic> {
        self.0
    }
}

impl Extend<Problem> for Problems {
    fn extend<I: IntoIterator<Item = Problem>>(&mut self, problems: I) {
        for problem in problems {
            self.push(problem);
        }
    }
}
