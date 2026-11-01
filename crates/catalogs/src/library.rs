//! The library of catalogs: what the picker lists and the document each id names.

use serde::Deserialize;

use crate::{CatalogId, built_in::DOCUMENTS};

/// What the catalog picker shows about a catalog.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CatalogInfo {
    /// Which catalog.
    pub id: CatalogId,
    /// What the user reads.
    pub name: String,
    /// What the catalog is for.
    pub description: String,
}

/// What a catalog document says about itself.
#[derive(Deserialize)]
struct Described {
    name: String,
    description: String,
}

/// The catalogs available. Cheap to clone.
#[derive(Clone)]
pub struct Library;

impl Library {
    /// The library of the catalogs built into the app.
    pub fn built_in() -> Self {
        Self
    }

    /// The catalogs the playback socket serves, Standard first, named by what their documents say.
    pub fn documents(&self) -> Vec<CatalogInfo> {
        DOCUMENTS
            .iter()
            .map(|(name, document)| {
                let id = CatalogId::BuiltIn(name);
                match serde_json::from_str::<Described>(document) {
                    Ok(described) => CatalogInfo {
                        id,
                        name: described.name,
                        description: described.description,
                    },
                    Err(error) => CatalogInfo {
                        name: (*name).to_owned(),
                        description: error.to_string(),
                        id,
                    },
                }
            })
            .collect()
    }

    /// The source of the catalog `id` names, `None` for one that is not served.
    pub fn document(&self, id: &CatalogId) -> Option<&'static str> {
        let CatalogId::BuiltIn(name) = id else {
            return None;
        };
        DOCUMENTS
            .iter()
            .find(|(served, _)| served == name)
            .map(|(_, document)| *document)
    }

    /// The catalog a new lane starts with: Standard.
    pub fn default_id(&self) -> CatalogId {
        CatalogId::BuiltIn(DOCUMENTS[0].0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_served_documents_are_listed_by_their_own_names_and_carry_the_id_they_are_asked_for_by() {
        let library = Library::built_in();

        let listed = library.documents();

        let ids: Vec<String> = listed.iter().map(|info| info.id.to_string()).collect();
        assert_eq!(
            ids,
            [
                "built-in:standard",
                "built-in:tracking",
                "built-in:classic",
                "built-in:chunky",
                "built-in:glass",
                "built-in:minimal"
            ]
        );
        assert_eq!(listed[0].name, "Standard");
        assert_eq!(library.default_id(), listed[0].id);
        for info in &listed {
            assert!(!info.description.is_empty());
            let document = library.document(&info.id).expect("a listed id is served");
            let value: serde_json::Value = serde_json::from_str(document).unwrap();
            assert_eq!(value["id"], info.id.to_string());
        }
        for unserved in [
            CatalogId::BuiltIn("nope"),
            CatalogId::User("night.json".into()),
        ] {
            assert_eq!(library.document(&unserved), None);
        }
    }
}
