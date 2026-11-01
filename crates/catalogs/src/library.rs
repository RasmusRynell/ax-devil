//! The library of catalogs: what the picker lists and each catalog compiled once.

use std::sync::{Arc, OnceLock};

use foundation::{ErrorCode, UserError};
use render_catalog::CompiledCatalog;

use crate::{
    CatalogId,
    built_in::{BUILT_INS, BuiltIn},
};

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

/// A built-in catalog and its compiled form, which the first caller makes.
struct Slot {
    built_in: &'static BuiltIn,
    compiled: OnceLock<Result<Arc<CompiledCatalog>, UserError>>,
}

/// The catalogs available, each compiled on first use. Cheap to clone; clones share the compiled catalogs.
#[derive(Clone)]
pub struct Library {
    slots: Arc<[Slot]>,
}

impl Library {
    /// The library of the catalogs built into the app.
    pub fn built_in() -> Self {
        Self {
            slots: BUILT_INS
                .iter()
                .map(|built_in| Slot {
                    built_in,
                    compiled: OnceLock::new(),
                })
                .collect(),
        }
    }

    /// The catalogs, built-ins first and Standard first among them. Blocking on first use, as
    /// [`Library::compiled`] is: the names and descriptions are the catalogs' own.
    pub fn list(&self) -> Vec<CatalogInfo> {
        self.slots
            .iter()
            .map(|slot| {
                let id = CatalogId::BuiltIn(slot.built_in.name);
                match self.compiled(&id) {
                    Ok(compiled) => {
                        let metadata = compiled.metadata();
                        CatalogInfo {
                            id,
                            name: metadata.name.clone(),
                            description: metadata.description.clone(),
                        }
                    }
                    // Listed all the same, so the user sees the catalog is there and why it cannot draw.
                    Err(error) => CatalogInfo {
                        name: slot.built_in.name.to_owned(),
                        description: error.reason,
                        id,
                    },
                }
            })
            .collect()
    }

    /// The catalog a new lane starts with: Standard.
    pub fn default_id(&self) -> CatalogId {
        CatalogId::BuiltIn(BUILT_INS[0].name)
    }

    /// The compiled catalog. Blocking on first use per id, with concurrent callers waiting for the one compiling, then
    /// cached: every caller gets the same compiled catalog. A failure names the catalog in its reason.
    pub fn compiled(&self, id: &CatalogId) -> Result<Arc<CompiledCatalog>, UserError> {
        let slot = match id {
            CatalogId::BuiltIn(name) => self.slots.iter().find(|slot| slot.built_in.name == *name),
            CatalogId::User(_) => None,
        };
        let Some(slot) = slot else {
            let reason = match id {
                CatalogId::BuiltIn(_) => "There is no such catalog",
                CatalogId::User(_) => "Catalogs of your own are not available yet",
            };
            return Err(UserError::new(
                ErrorCode::CatalogInvalid,
                format!("{reason}: {id}"),
            ));
        };
        slot.compiled
            .get_or_init(|| {
                render_catalog::compile(slot.built_in.source)
                    .map(Arc::new)
                    .map_err(|diagnostics| {
                        let first = diagnostics.first().map_or_else(
                            || "the catalog is not valid".to_owned(),
                            |first| format!("{} (at {})", first.message, first.location),
                        );
                        UserError::new(
                            ErrorCode::CatalogInvalid,
                            format!("The catalog {id} cannot be used: {first}"),
                        )
                    })
            })
            .clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_six_built_ins_are_listed_with_standard_first_and_the_default() {
        let library = Library::built_in();

        let listed = library.list();

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
        assert!(listed.iter().all(|info| !info.description.is_empty()));
        assert_eq!(library.default_id(), listed[0].id);
    }

    #[test]
    fn a_catalog_is_compiled_once_for_every_caller() {
        let library = Library::built_in();
        let id = CatalogId::parse("built-in:tracking").unwrap();

        let first = library.compiled(&id).unwrap();
        let again = library.compiled(&id).unwrap();
        let from_a_clone = library.clone().compiled(&id).unwrap();

        assert!(Arc::ptr_eq(&first, &again));
        assert!(Arc::ptr_eq(&first, &from_a_clone));
        assert_eq!(first.metadata().name, "Tracking");
    }

    #[test]
    fn a_catalog_the_library_cannot_give_fails_naming_it() {
        let library = Library::built_in();
        for id in [
            CatalogId::BuiltIn("nope"),
            CatalogId::User("night.json".into()),
        ] {
            let error = library.compiled(&id).unwrap_err();
            assert_eq!(error.code, ErrorCode::CatalogInvalid);
            assert!(error.reason.contains(&id.to_string()), "{error}");
        }
    }
}
