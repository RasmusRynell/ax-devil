//! Which catalog.

use std::fmt;

use crate::built_in::DOCUMENTS;

/// Which catalog: one shipped with the app, or a file in the user's catalog folder.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum CatalogId {
    /// A catalog built into the app, by name.
    BuiltIn(&'static str),
    /// A catalog file of the user, by file name.
    User(String),
}

impl CatalogId {
    /// Reads `built-in:<name>` or `user:<file>`. `None` for anything else, for a built-in the app does not ship, and
    /// for a user file name that is empty, names a path or has a control character.
    pub fn parse(text: &str) -> Option<Self> {
        match text.split_once(':')? {
            ("built-in", name) => DOCUMENTS
                .iter()
                .find(|(served, _)| *served == name)
                .map(|(served, _)| Self::BuiltIn(served)),
            ("user", file) if is_file_name(file) => Some(Self::User(file.to_owned())),
            _ => None,
        }
    }
}

/// A plain file name: something the catalog folder can hold, never a way out of it, and printable.
pub(crate) fn is_file_name(file: &str) -> bool {
    !matches!(file, "" | "." | "..")
        && !file.contains(|c: char| c.is_control() || matches!(c, '/' | '\\'))
}

impl fmt::Display for CatalogId {
    /// The form [`CatalogId::parse`] reads: `built-in:<name>` or `user:<file>`.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::BuiltIn(name) => write!(f, "built-in:{name}"),
            Self::User(file) => write!(f, "user:{file}"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_read_against_the_catalogs_the_app_ships_and_print_the_way_they_are_read() {
        for (name, _) in &DOCUMENTS {
            let id = CatalogId::parse(&format!("built-in:{}", name));
            assert_eq!(id, Some(CatalogId::BuiltIn(name)));
            assert_eq!(id.unwrap().to_string(), format!("built-in:{}", name));
        }
        let user = CatalogId::parse("user:night.json");
        assert_eq!(user, Some(CatalogId::User("night.json".into())));
        assert_eq!(user.unwrap().to_string(), "user:night.json");

        for refused in [
            "",
            "standard",
            "built-in:nope",
            "built-in:",
            "user:",
            "user:..",
            "user:a/b.json",
            "user:a\\b.json",
            "user:a\u{1b}b.json",
            "other:standard",
        ] {
            assert_eq!(CatalogId::parse(refused), None, "{refused}");
        }
    }
}
