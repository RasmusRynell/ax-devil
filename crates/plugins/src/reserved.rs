//! The ids that are taken: plugin ids and handler ids are separate namespaces.

use std::collections::HashSet;

use crate::manifest::{Manifest, ManifestError};

/// Ids already taken, by the built-ins and by plugins claimed so far.
#[derive(Clone, Debug, Default)]
pub struct Reserved {
    /// Plugin ids, the built-in plugins' included.
    pub plugins: HashSet<String>,
    /// Handler ids across every kind of handler. A handler may share its own plugin's id.
    pub handlers: HashSet<String>,
}

impl Reserved {
    /// Takes the plugin id and every handler id of `manifest`, or takes nothing and refuses it, naming the field.
    pub(crate) fn claim(&mut self, manifest: &Manifest) -> Result<(), ManifestError> {
        if self.plugins.contains(&manifest.id) {
            return Err(ManifestError::new(
                "id",
                format!(
                    "'{}' is already taken by a built-in or installed plugin",
                    manifest.id
                ),
            ));
        }
        for (index, handler) in manifest.handlers.iter().enumerate() {
            if self.handlers.contains(handler.id()) {
                return Err(ManifestError::new(
                    format!("handlers[{index}].id"),
                    format!(
                        "'{}' is already taken by a built-in or installed handler",
                        handler.id()
                    ),
                ));
            }
        }
        self.plugins.insert(manifest.id.clone());
        self.handlers.extend(
            manifest
                .handlers
                .iter()
                .map(|handler| handler.id().to_owned()),
        );
        Ok(())
    }

    /// The loader's claim order: from these ids, each manifest in turn takes its ids or is refused, and a refused one
    /// takes nothing, so later ones see only the earlier successes. `load` and the registry's install and update
    /// checks all go through it, so none can disagree with what loading does.
    pub(crate) fn claim_in_order(
        &self,
        manifests: impl IntoIterator<Item = Result<Manifest, ManifestError>>,
    ) -> impl Iterator<Item = Result<Manifest, ManifestError>> {
        let mut taken = self.clone();
        manifests.into_iter().map(move |manifest| {
            let manifest = manifest?;
            taken.claim(&manifest)?;
            Ok(manifest)
        })
    }
}
