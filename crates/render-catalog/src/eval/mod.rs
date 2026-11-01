//! Running a compiled catalog over a filtered Scene.
//!
//! Every recipe runs once per frame over the rows routed to it. A row is evaluated on its own: its slots are computed
//! lazily and at most once, only the chosen branch of an `if` runs, and a row that fails a check emits nothing for its
//! recipe while the other rows draw. Problems are reported once each per call.

mod color;
mod draw;
mod expr;
mod ops;
mod row;
mod text;
mod value;

use drawing::DrawingInstructions;
use scene::SceneView;

use crate::diagnostics::codes;
use crate::ir::{
    Action, Binding, Catalog, ContextField, Expr, Loc, Program, Recipe, SceneKind, Step, Value,
};
use crate::routing::{self, Routed};
use crate::{CatalogDiagnostic, FeatureSet, RenderContext};
use draw::Emitter;
use row::RowRef;

/// Most distinct problems one call reports, like the reference.
const MAX_REPORTED: usize = 256;

/// A failed check or operation, located at the expression that failed.
pub(super) struct Failure {
    code: &'static str,
    message: String,
    loc: Option<Loc>,
}

/// A failure, boxed so that results stay small.
pub(super) type Fail = Box<Failure>;

/// A failed check of a value.
pub(super) fn invalid(message: impl Into<String>) -> Fail {
    Box::new(Failure {
        code: codes::INVALID_VALUE,
        message: message.into(),
        loc: None,
    })
}

/// The render context as the numbers a catalog reads.
pub(super) struct Numbers {
    width: f64,
    height: f64,
    /// The smaller of width and height, what relative lengths are measured against.
    scale: f64,
}

impl Numbers {
    fn new(context: &RenderContext) -> Option<Self> {
        let (width, height) = (f64::from(context.width), f64::from(context.height));
        (width > 0.0 && height > 0.0 && width.is_finite() && height.is_finite()).then(|| Self {
            width,
            height,
            scale: width.min(height),
        })
    }

    fn field(&self, field: ContextField) -> f64 {
        match field {
            ContextField::Width => self.width,
            ContextField::Height => self.height,
            ContextField::AspectRatio => self.width / self.height,
            ContextField::ScaleFactor => self.scale,
            ContextField::PxH => 1.0 / self.width,
            ContextField::PxV => 1.0 / self.height,
            ContextField::PxMin => 1.0 / self.scale,
        }
    }
}

/// One running program: its parameters and the slots it has computed so far.
pub(super) struct Frame<'p> {
    program: &'p Program,
    params: Value,
    slots: Vec<Option<Value>>,
}

/// Everything a row's evaluation needs.
pub(super) struct Machine<'m, 'o> {
    numbers: &'m Numbers,
    hidden: FeatureSet,
    row: Option<RowRef<'m>>,
    bindings: &'m [Binding],
    binding_values: Vec<Option<Value>>,
    pool: Vec<Vec<Option<Value>>>,
    emitter: Emitter<'o>,
}

impl<'m> Machine<'m, '_> {
    /// Draws one row of `recipe`; on failure everything the row drew is taken back.
    fn run_row(&mut self, recipe: &'m Recipe, row: RowRef<'m>) -> Result<(), Fail> {
        self.row = Some(row);
        self.bindings = &recipe.bindings;
        self.binding_values.clear();
        self.binding_values.resize(recipe.bindings.len(), None);
        let mark = self.emitter.mark();
        let result = self.run(&recipe.program, Value::Null);
        if result.is_err() {
            self.emitter.rollback(mark);
        }
        result
    }

    /// Runs a program: its steps in order, each when its feature is shown and it is enabled.
    fn run(&mut self, program: &Program, params: Value) -> Result<(), Fail> {
        let mut slots = self.pool.pop().unwrap_or_default();
        slots.clear();
        slots.resize(program.slots.len(), None);
        let mut frame = Frame {
            program,
            params,
            slots,
        };
        let result = self.steps(&mut frame);
        self.pool.push(frame.slots);
        result
    }

    fn steps(&mut self, frame: &mut Frame<'_>) -> Result<(), Fail> {
        let program = frame.program;
        if self.is_hidden(program.feature) || !self.enabled(frame, &program.enabled)? {
            return Ok(());
        }
        for step in &program.steps {
            if self.is_hidden(step.feature) || !self.enabled(frame, &step.enabled)? {
                continue;
            }
            self.step(frame, step)
                .map_err(|failure| failure.or_at(step.loc))?;
        }
        Ok(())
    }

    fn step(&mut self, frame: &mut Frame<'_>, step: &Step) -> Result<(), Fail> {
        match &step.action {
            Action::Draw(draw) => self.draw(frame, draw),
            Action::Call(call) => {
                if !call.program.draws(self.hidden) {
                    return Ok(());
                }
                let params = self.eval(frame, &call.inputs)?;
                self.run(&call.program, params)
            }
        }
    }

    fn is_hidden(&self, feature: Option<crate::OverlayFeature>) -> bool {
        feature.is_some_and(|feature| self.hidden.contains(feature))
    }

    fn enabled(&mut self, frame: &mut Frame<'_>, expr: &Expr) -> Result<bool, Fail> {
        Ok(self.eval(frame, expr)?.as_bool().unwrap_or(false))
    }
}

impl Failure {
    /// The failure, placed at `loc` unless it is placed already.
    fn or_at(mut self: Box<Self>, loc: Loc) -> Box<Self> {
        self.loc.get_or_insert(loc);
        self
    }
}

/// The problems of one call, each reported once.
struct Reporter<'r> {
    catalog: &'r Catalog,
    seen: Vec<(&'static str, Loc)>,
    report: &'r mut dyn FnMut(CatalogDiagnostic),
}

impl Reporter<'_> {
    fn fail(&mut self, failure: &Failure, fallback: Loc) {
        let catalog = self.catalog;
        let located = failure.loc.filter(|loc| !catalog.location(*loc).is_empty());
        let loc = located.unwrap_or(fallback);
        if self.seen.len() >= MAX_REPORTED || self.seen.contains(&(failure.code, loc)) {
            return;
        }
        self.seen.push((failure.code, loc));
        (self.report)(CatalogDiagnostic {
            code: failure.code,
            location: catalog.location(loc).to_string(),
            message: failure.message.clone(),
        });
    }
}

/// Draws the entities and relations of `view` into `out`; see [`crate::CompiledCatalog::render`].
pub(crate) fn render(
    catalog: &Catalog,
    view: &SceneView<'_>,
    context: &RenderContext,
    hidden: FeatureSet,
    out: &mut DrawingInstructions,
    report: &mut dyn FnMut(CatalogDiagnostic),
) {
    let mut reporter = Reporter {
        catalog,
        seen: Vec::new(),
        report,
    };
    let Some(numbers) = Numbers::new(context) else {
        let failure = Failure {
            code: codes::EVALUATION_ERROR,
            message: "Render target dimensions must be positive.".to_string(),
            loc: None,
        };
        reporter.fail(&failure, Loc(0));
        return;
    };
    let wanted = |index: usize| catalog.recipes[index].program.draws(hidden);
    let routed: Routed<'_> = routing::route(catalog, view, wanted);
    let mut machine = Machine {
        numbers: &numbers,
        hidden,
        row: None,
        bindings: &[],
        binding_values: Vec::new(),
        pool: Vec::new(),
        emitter: Emitter::new(out, &numbers),
    };
    for (index, recipe) in catalog.recipes.iter().enumerate() {
        let fallback = recipe.program.loc;
        match recipe.scene {
            SceneKind::Entity => {
                for row in &routed.entities[index] {
                    if let Err(failure) = machine.run_row(recipe, RowRef::Entity(row)) {
                        reporter.fail(&failure, fallback);
                    }
                }
            }
            SceneKind::Relation => {
                for row in &routed.relations[index] {
                    if let Err(failure) = machine.run_row(recipe, RowRef::Relation(row)) {
                        reporter.fail(&failure, fallback);
                    }
                }
            }
        }
    }
}
