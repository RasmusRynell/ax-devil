//! The overlay of one lane frame: lookup, Scene, filter, catalog.

use drawing::DrawingInstructions;
use foundation::{FrameId, Timeline, UserError};
use overlay_sync::{Selection, select};
use render_catalog::{CatalogDiagnostic, CompiledCatalog, FeatureSet, RenderContext};
use scene::SceneView;
use scene_store::Track;

use crate::LaneView;

/// How many distinct catalog problems one frame reports; a catalog failing on every box says it once or twice.
const PROBLEMS_PER_FRAME: usize = 16;

/// What preparing a frame produced.
pub(crate) struct Prepared {
    pub selection: Selection,
    pub drawing: DrawingInstructions,
    /// Problems the catalog reported while drawing, without repeats.
    pub problems: Vec<CatalogDiagnostic>,
}

/// The overlay of one lane frame: sample lookup, Scene, filter, catalog. Pure apart from the track's Scene cache;
/// export reuses it.
///
/// A frame with no sample, or that a dense file covers with nothing, has an empty drawing. Problems the catalog
/// reports while drawing are dropped here; a viewer reports them to the user.
pub fn prepare_lane_frame(
    track: &Track,
    timeline: &Timeline,
    view: &LaneView,
    catalog: &CompiledCatalog,
    frame: FrameId,
) -> Result<(Selection, DrawingInstructions), UserError> {
    let prepared = prepare(track, timeline, view, Some(catalog), frame)?;
    Ok((prepared.selection, prepared.drawing))
}

/// [`prepare_lane_frame`] for a lane that may have no compiled catalog yet: it draws nothing, but still selects.
pub(crate) fn prepare(
    track: &Track,
    timeline: &Timeline,
    view: &LaneView,
    catalog: Option<&CompiledCatalog>,
    frame: FrameId,
) -> Result<Prepared, UserError> {
    let selection = select(
        track.keys(),
        track.coverage(),
        frame,
        timeline,
        &view.policy,
    );
    let mut drawing = DrawingInstructions::default();
    let mut problems: Vec<CatalogDiagnostic> = Vec::new();
    if let (Selection::Sample { index, .. }, Some(catalog)) = (selection, catalog) {
        let scene = track.scene(index)?;
        let filtered = track.filter().view(&view.filter, &scene);
        render(catalog, &filtered, view, &mut drawing, &mut problems);
    }
    Ok(Prepared {
        selection,
        drawing,
        problems,
    })
}

fn render(
    catalog: &CompiledCatalog,
    scene: &SceneView<'_>,
    view: &LaneView,
    drawing: &mut DrawingInstructions,
    problems: &mut Vec<CatalogDiagnostic>,
) {
    let context = RenderContext {
        width: view.target.width.max(1) as f32,
        height: view.target.height.max(1) as f32,
    };
    catalog.render(
        scene,
        &context,
        FeatureSet::default(),
        drawing,
        &mut |diagnostic| {
            let known = problems
                .iter()
                .any(|seen| seen.code == diagnostic.code && seen.location == diagnostic.location);
            if !known && problems.len() < PROBLEMS_PER_FRAME {
                problems.push(diagnostic);
            }
        },
    );
}
