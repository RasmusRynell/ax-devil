//! The render catalog language (schema version 3): parse, validate and compile a catalog, then evaluate it over a
//! filtered Scene into Drawing Instructions.

mod compile;
mod definitions;
mod diagnostics;
mod document;
mod eval;
pub mod ir;
mod routing;
mod validate;

use drawing::DrawingInstructions;
use scene::SceneView;

pub use diagnostics::{CatalogDiagnostic, codes};

/// The catalog schema version this build reads.
pub const SCHEMA_VERSION: u32 = 3;

/// What a catalog says about itself.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CatalogMetadata {
    /// The catalog's id.
    pub id: String,
    /// What the user reads.
    pub name: String,
    /// What the catalog is for.
    pub description: String,
    /// The catalog this one was copied from, when it names one.
    pub origin: Option<String>,
}

/// A detail of an overlay that the user can hide.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum OverlayFeature {
    /// Outlines of shapes.
    Outlines,
    /// Entity ids.
    Ids,
    /// Class names.
    ClassNames,
    /// Scores.
    Score,
    /// Confidences.
    Confidence,
    /// Speeds.
    Speed,
    /// Movement trails and arrows.
    Movement,
    /// Attributes.
    Attributes,
    /// Relations between entities.
    Relations,
}

impl OverlayFeature {
    /// Every feature with its catalog name.
    pub const NAMES: [(OverlayFeature, &'static str); 9] = [
        (Self::Outlines, "outlines"),
        (Self::Ids, "ids"),
        (Self::ClassNames, "class_names"),
        (Self::Score, "score"),
        (Self::Confidence, "confidence"),
        (Self::Speed, "speed"),
        (Self::Movement, "movement"),
        (Self::Attributes, "attributes"),
        (Self::Relations, "relations"),
    ];

    /// The feature with this catalog name, such as `class_names`.
    pub fn parse(name: &str) -> Option<Self> {
        Self::NAMES
            .iter()
            .find(|(_, candidate)| *candidate == name)
            .map(|(feature, _)| *feature)
    }

    /// The catalog name.
    pub fn name(self) -> &'static str {
        Self::NAMES[self as usize].1
    }
}

/// A set of overlay features; the render takes the hidden ones.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub struct FeatureSet(u16);

impl FeatureSet {
    /// The set with `feature` added.
    pub fn with(self, feature: OverlayFeature) -> Self {
        Self(self.0 | 1 << feature as u16)
    }

    /// Whether `feature` is in the set.
    pub fn contains(self, feature: OverlayFeature) -> bool {
        self.0 & (1 << feature as u16) != 0
    }

    /// Every feature in either set.
    pub fn union(self, other: Self) -> Self {
        Self(self.0 | other.0)
    }

    /// Whether the sets share a feature.
    pub fn intersects(self, other: Self) -> bool {
        self.0 & other.0 != 0
    }

    /// Whether the set has no feature.
    pub fn is_empty(self) -> bool {
        self.0 == 0
    }

    /// The features in the set, in [`OverlayFeature::NAMES`] order.
    pub fn iter(self) -> impl Iterator<Item = OverlayFeature> {
        OverlayFeature::NAMES
            .into_iter()
            .map(|(feature, _)| feature)
            .filter(move |feature| self.contains(*feature))
    }
}

/// The size lengths in pixels are measured against.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RenderContext {
    /// Width of the displayed image in CSS pixels.
    pub width: f32,
    /// Height of the displayed image in CSS pixels.
    pub height: f32,
}

/// A catalog that passed validation, ready to render. Cheap to share.
#[derive(Debug)]
pub struct CompiledCatalog {
    metadata: CatalogMetadata,
    catalog: ir::Catalog,
}

/// Validates and compiles a catalog, naming every problem at once with its JSON location. Limits follow the
/// reference: depth 64, template depth 32, 10 000 expanded steps, collections of 4096, documents of 2 MB.
pub fn compile(source: &str) -> Result<CompiledCatalog, Vec<CatalogDiagnostic>> {
    let (metadata, catalog) = compile::catalog(source)?;
    Ok(CompiledCatalog { metadata, catalog })
}

impl CompiledCatalog {
    /// What the catalog says about itself.
    pub fn metadata(&self) -> &CatalogMetadata {
        &self.metadata
    }

    /// The features this catalog draws, which a viewer can offer to hide.
    pub fn features(&self) -> FeatureSet {
        self.catalog.features
    }

    /// The compiled recipes the evaluator runs.
    pub fn ir(&self) -> &ir::Catalog {
        &self.catalog
    }

    /// Draws the entities and relations of `view` into `out`.
    ///
    /// Each entity is routed by the primary classification of its latest observation (none: `unclassified`; a recipe
    /// of its own; else `classified`) and each relation by its type; every recipe is evaluated over its rows. A row
    /// that fails a check draws nothing for its recipe while the other rows draw. Problems go to `report`, each
    /// distinct one once per call (the caller deduplicates across calls).
    pub fn render(
        &self,
        view: &SceneView<'_>,
        context: &RenderContext,
        hidden: FeatureSet,
        out: &mut DrawingInstructions,
        report: &mut dyn FnMut(CatalogDiagnostic),
    ) {
        eval::render(&self.catalog, view, context, hidden, out, report);
    }
}
