//! Which catalog.

use std::fmt;

use crate::built_in::BUILT_INS;

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
    /// for a user file name that is empty or names a path.
    pub fn parse(text: &str) -> Option<Self> {
        match text.split_once(':')? {
            ("built-in", name) => BUILT_INS
                .iter()
                .find(|built_in| built_in.name == name)
                .map(|built_in| Self::BuiltIn(built_in.name)),
            ("user", file) if is_file_name(file) => Some(Self::User(file.to_owned())),
            _ => None,
        }
    }
}

/// A plain file name: something the catalog folder can hold, never a way out of it.
fn is_file_name(file: &str) -> bool {
    !matches!(file, "" | "." | "..") && !file.contains(['/', '\\', '\0'])
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
        for built_in in &BUILT_INS {
            let id = CatalogId::parse(&format!("built-in:{}", built_in.name));
            assert_eq!(id, Some(CatalogId::BuiltIn(built_in.name)));
            assert_eq!(
                id.unwrap().to_string(),
                format!("built-in:{}", built_in.name)
            );
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
            "other:standard",
        ] {
            assert_eq!(CatalogId::parse(refused), None, "{refused}");
        }
    }
}
