//! The compiled form of a catalog: what the evaluator runs.
//!
//! The compiler resolves every scope, name, operation and primitive into the typed nodes below and checks every
//! type, so evaluation looks nothing up by name except the classification and relation types it routes by. Invariants
//! the evaluator may rely on:
//!
//! - Every [`Expr`] carries its static type, and its value has that type's layout: a record holds its fields in the
//!   type's field order. A value is null only where its type is nullable, or where a guard (`is_present` or
//!   `is_not_empty` on a reference, in an `enabled` or an `if` condition) narrowed the type and evaluation has not
//!   checked the guard yet; reads of the row narrowed this way are [`Node::Validate`]d.
//! - Whole numbers ([`Value::Int`]) may stand wherever a number is expected; read numbers with [`Value::as_f64`].
//! - Constants are checked against their type. Records, lists and simple operations over constants are folded to
//!   [`Node::Const`]; operations that need the render context or a kernel are left to the evaluator.
//! - Steps whose condition is constantly false and steps marked invisible are left out.
//! - Every node carries a [`Loc`]; a failing check reports it as the problem's location.
//!
//! Record layouts of the language's types (`?` marks a nullable field):
//!
//! | Type | Fields in order |
//! |------|-----------------|
//! | `image_point` | `x`, `y` |
//! | `image_box` | `x`, `y`, `w`, `h` (`w`, `h` nonnegative) |
//! | `image_vector` | `vx`, `vy` |
//! | `length` | `value`, `unit` (`px`, `image_width`, `image_height`, `image_min`) |
//! | `image_arrow` | `start`, `end`, `head_points` |
//! | `stroke` | `color` (`rgb`: a list of three integers 0..=255), `width` (length), `pattern` (`solid`, `dash`) |
//! | `fill` | `color`, `alpha` (integer 0..=255) |
//! | `shape_style` | `stroke?`, `fill?`, `radius?` (length) |
//! | `text_style_wrapper` | `text`: `text_style` = `color`, `size` (length), `family?` |
//! | `label_style_wrapper` | `label`: `label_style` |
//! | `label_style` | `size`, `family?`, `background?` (fill), `padding_x`, `padding_y`, `radius`, `gap` (lengths) |
//! | `label_run` | `text?`, `bar?`, `color`, `weight` (`regular`, `medium`, `semibold`, `bold`) |
//! | `color_stop` | `at`, `color`; `text_entry`: `key`, `text` |
//! | `color_binding` | `color`, `score` |
//! | `entity` | `id`, `motion_state?` |
//! | `observation` | `geometry`, `polygon_points?`, `velocity?` |
//! | `classification` | `type`, `score?`, `confidence?` |
//! | relation row: `relation`, `endpoint` | `type`; `entity` (`id`), `observation` (`geometry`) |

mod value;

use std::collections::HashMap;
use std::sync::Arc;

pub use value::{Bounds, TypeName, Value, ValueType};

use crate::{FeatureSet, OverlayFeature};

/// A compiled catalog's recipes, how entities and relations reach them, and the locations problems are reported at.
#[derive(Debug)]
pub struct Catalog {
    /// Every recipe, in document order (fallbacks, classifications, relations).
    pub recipes: Box<[Recipe]>,
    /// Which recipe draws which entity or relation.
    pub routing: Routing,
    /// The features some recipe draws, which a viewer can offer to hide.
    pub features: FeatureSet,
    /// JSON pointers into the catalog document, indexed by [`Loc`].
    pub locations: Box<[Box<str>]>,
}

impl Catalog {
    /// The JSON pointer `loc` stands for.
    pub fn location(&self, loc: Loc) -> &str {
        self.locations
            .get(loc.0 as usize)
            .map_or("", |location| location)
    }
}

/// A place in the catalog document, as an index into [`Catalog::locations`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Loc(pub u32);

/// Indices into [`Catalog::recipes`] by what each recipe draws.
#[derive(Debug, Default)]
pub struct Routing {
    /// The recipe for entities whose latest observation has no classification.
    pub unclassified: usize,
    /// The recipe for classified entities without a recipe of their own.
    pub classified: usize,
    /// Recipes by classification type.
    pub classifications: HashMap<Box<str>, usize>,
    /// Recipes by relation type.
    pub relations: HashMap<Box<str>, usize>,
}

/// One recipe: a program run once over every row routed to it.
#[derive(Debug)]
pub struct Recipe {
    /// The recipe's id, unique in its catalog.
    pub id: Box<str>,
    /// What one row is.
    pub scene: SceneKind,
    /// The attribute projections [`Node::Binding`] reads, by index.
    pub bindings: Box<[Binding]>,
    /// What the recipe draws for one row.
    pub program: Program,
}

/// What one row of a recipe is.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum SceneKind {
    /// An entity with its latest observation and that observation's primary classification.
    Entity,
    /// A relation with both endpoints' entities and latest observations.
    Relation,
}

/// A declared projection of one attribute of the row's primary classification. Its value is null without a
/// classification or without the attribute.
#[derive(Clone, Debug, PartialEq)]
pub struct Binding {
    /// The attribute's name.
    pub attribute: Box<str>,
    /// What is read from it.
    pub shape: BindingShape,
}

/// What a binding reads from its attribute.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum BindingShape {
    /// A boolean attribute (`bool | null`).
    Bool,
    /// A finite number attribute (`number | null`).
    Number,
    /// Of a list of color classifications, the one with the highest score (`color_binding | null`: a record of
    /// `color: rgb` with integer channels in 0..=255, then `score: number`).
    HighestScoreColor,
}

/// A program: a recipe's body or one specialized template.
#[derive(Debug)]
pub struct Program {
    /// Where the program is defined.
    pub loc: Loc,
    /// The feature that hides everything the program draws.
    pub feature: Option<OverlayFeature>,
    /// Whether the program draws anything for this row; when false nothing in it is evaluated.
    pub enabled: Expr,
    /// Values evaluated at most once per invocation and row, on first demand by [`Node::Slot`]: the calculated
    /// values, then validated reads of the row.
    pub slots: Box<[Expr]>,
    /// What the program draws, in order.
    pub steps: Box<[Step]>,
    /// The feature tags along every way the program can draw (its own feature, each step's and each called
    /// template's); see [`Program::draws`].
    pub outputs: Box<[FeatureSet]>,
}

impl Program {
    /// Whether anything is drawn when the `hidden` features are hidden: some way of drawing carries no hidden tag.
    pub fn draws(&self, hidden: FeatureSet) -> bool {
        self.outputs.iter().any(|tags| !tags.intersects(hidden))
    }
}

/// One step of a program: a guarded primitive or template call.
#[derive(Debug)]
pub struct Step {
    /// Where the step is defined.
    pub loc: Loc,
    /// The feature that hides this step.
    pub feature: Option<OverlayFeature>,
    /// Whether the step runs for this row.
    pub enabled: Expr,
    /// What the step does.
    pub action: Action,
}

/// What a step does.
#[derive(Debug)]
pub enum Action {
    /// Draw one primitive.
    Draw(Draw),
    /// Run a template with its own parameters and slots.
    Call(Call),
}

/// A primitive with its fields and style.
#[derive(Debug)]
pub struct Draw {
    /// Which primitive.
    pub primitive: Primitive,
    /// A record of the primitive's fields, in the order [`Primitive`] lists them.
    pub fields: Expr,
    /// The style: a `shape_style` record (`stroke | null`, `fill | null`, `radius: length | null`) for shapes, a
    /// `text_style_wrapper` (`text: text_style`) for text and a `label_style_wrapper` (`label: label_style`) for
    /// labels.
    pub style: Expr,
}

/// A template call.
#[derive(Debug)]
pub struct Call {
    /// The template, specialized for the call's constant inputs.
    pub program: Arc<Program>,
    /// The `parameters` record of the call, with fields sorted by name; [`Node::Parameter`] indexes it.
    pub inputs: Expr,
}

/// A primitive and the fields of its [`Draw::fields`] record, in order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Primitive {
    /// `geometry: image_box`; nothing is drawn without area.
    Box,
    /// `start: image_point`, `end: image_point`.
    Line,
    /// `points: image_point_list`, empty or at least three points.
    Polygon,
    /// `points: image_point_list`, empty or at least two points.
    Polyline,
    /// `position: image_point`.
    Point,
    /// `center: image_point`, `radius: length` (nonnegative).
    Circle,
    /// `position: image_point`, `text: string`, `anchor: text_anchor`.
    Text,
    /// `position: image_point`, `anchor: anchor`, `runs: label_runs`.
    Label,
}

/// An expression with its static type and location.
#[derive(Clone, Debug)]
pub struct Expr {
    /// What is computed.
    pub node: Node,
    /// The static type of the result.
    pub kind: Arc<ValueType>,
    /// Where the expression is written.
    pub loc: Loc,
}

/// What an expression computes.
#[derive(Clone, Debug)]
pub enum Node {
    /// A constant, already checked against the expression's type.
    Const(Value),
    /// Slot `n` of the running program ([`Program::slots`]), evaluated on first demand.
    Slot(u32),
    /// Field `n` of the running template's parameters record ([`Call::inputs`]).
    Parameter(u32),
    /// A number of the render context.
    Context(ContextField),
    /// A part of the row.
    Scene(SceneField),
    /// Binding `n` of the running recipe ([`Recipe::bindings`]).
    Binding(u32),
    /// The record field at each index in turn; null when a record on the way is null.
    Field {
        /// The record read from.
        value: Box<Expr>,
        /// Field indices, outermost first.
        path: Box<[u32]>,
    },
    /// A record of these fields, in the type's field order.
    Record(Box<[Expr]>),
    /// A list of these items.
    List(Box<[Expr]>),
    /// `condition`, `then`, `else`: only the chosen branch is evaluated.
    If(Box<[Expr; 3]>),
    /// The first non-null value; later values are evaluated only while earlier ones are null.
    Coalesce(Box<[Expr]>),
    /// Arithmetic over numbers and lengths; a length result is a `px` length.
    Arithmetic {
        /// The operation.
        op: Arithmetic,
        /// The operands: every value of `add`, `mul`, `min` and `max`; `left`, `right` of `sub`; `numerator`,
        /// `denominator` of `div`; `value`, `minimum`, `maximum` of `clamp`.
        operands: Box<[Operand]>,
        /// Whether the result is a length.
        length: bool,
    },
    /// An operation with its arguments in [`Op`]'s order; omitted optional arguments are null constants.
    Call {
        /// The operation.
        op: Op,
        /// The arguments.
        args: Box<[Expr]>,
    },
    /// The value, failing unless it meets the bounds and rules of the expression's type for itself and its direct
    /// scalar fields or items. Null passes.
    Check(Box<Expr>),
    /// The value reshaped to the expression's type.
    Convert {
        /// The value converted.
        value: Box<Expr>,
        /// How.
        conversion: Arc<Conversion>,
    },
    /// The value, failing unless it fully conforms to the expression's type (null only when nullable). Reads of the
    /// row are validated this way.
    Validate(Box<Expr>),
}

/// An arithmetic operation.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Arithmetic {
    /// Sum.
    Add,
    /// Difference.
    Sub,
    /// Product.
    Mul,
    /// Quotient; fails on a zero denominator.
    Div,
    /// Smallest.
    Min,
    /// Largest.
    Max,
    /// Value limited to minimum and maximum; fails when minimum exceeds maximum.
    Clamp,
}

/// One arithmetic operand.
#[derive(Clone, Debug)]
pub struct Operand {
    /// A non-null number, or a length when `length`.
    pub value: Expr,
    /// Whether the operand is a length, converted to pixels before computing.
    pub length: bool,
}

/// An operation and the arguments of [`Node::Call`], in order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Op {
    /// `left: number`, `right: number` -> `bool`.
    Gt,
    /// `value: any` -> `bool`.
    IsPresent,
    /// `value: string | list | null` -> `bool`.
    IsNotEmpty,
    /// `geometry: image_box` -> `bool`.
    HasArea,
    /// `geometry: image_box`, `anchor: anchor` -> `image_point`.
    BoxAnchor,
    /// `geometry: image_box` -> `length`.
    BoxMinSize,
    /// `point: image_point`, `dx: length`, `dy: length` -> `image_point`.
    OffsetPoint,
    /// `geometry: image_box`, `margin: length | null`, `fraction: number | null` (exactly one) -> `image_box`.
    InsetBox,
    /// `geometry: image_box`, `width: length | null`, `height: length | null`, `anchor: anchor` -> `image_box`.
    ResizeBox,
    /// `geometry: image_box`, `x_factor: number`, `y_factor: number`, `anchor: anchor` -> `image_box`.
    ScaleBox,
    /// `container: image_box`, `width: length`, `height: length`, `anchor: anchor`, `margin: length` -> `image_box`.
    PlaceBox,
    /// `origin: image_point`, `vector: image_vector | null`, `gain: number`, `max_length: length`, `head_length:
    /// length`, `head_half_width: length`, `min_magnitude: number` -> `image_arrow | null`.
    VectorArrow,
    /// `text: string | null`, `max_length: integer`, `delimiter: string | null`, `suffix: string`, `elide: elision |
    /// null` -> `string`, null when `text` is.
    TrimText,
    /// `value: number | null`, `precision: integer`, `hide_zero: bool` -> `string | null`.
    NumberText,
    /// `text: string | null`, `mapping: text_mapping`, `default: string` -> `string`, null when `text` is.
    LookupText,
    /// `value: number`, `stops: color_stops` -> `rgb`.
    ColorRamp,
    /// `text: string`, `colors: palette` -> `rgb`.
    PickColor,
}

/// A number of the render context.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum ContextField {
    /// Target width in pixels.
    Width,
    /// Target height in pixels.
    Height,
    /// Width over height.
    AspectRatio,
    /// The smaller of width and height.
    ScaleFactor,
    /// One pixel as a fraction of the width.
    PxH,
    /// One pixel as a fraction of the height.
    PxV,
    /// One pixel as a fraction of the scale factor.
    PxMin,
}

/// A part of the row, with the type it reads as. Deeper fields are read with [`Node::Field`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum SceneField {
    /// Entity rows: `entity` record (`id`, `motion_state`).
    Entity,
    /// Entity rows: `entity.id: string`.
    EntityId,
    /// Entity rows: `entity.motion_state: string | null`.
    MotionState,
    /// Entity rows: `observation` record (`geometry`, `polygon_points`, `velocity`).
    Observation,
    /// Entity rows: `observation.geometry: image_box`, the observation's bounds.
    Geometry,
    /// Entity rows: `observation.polygon_points: image_point_list | null`, for polygon geometry.
    PolygonPoints,
    /// Entity rows: `observation.velocity: image_vector | null`.
    Velocity,
    /// Entity rows: `classification | null` record (`type`, `score`, `confidence`); never null outside the
    /// `unclassified` recipe.
    Classification,
    /// Entity rows: `classification.type: string`.
    ClassType,
    /// Entity rows: `classification.score: number | null`.
    Score,
    /// Entity rows: `classification.confidence: number | null`.
    Confidence,
    /// Relation rows: `relation` record (`type`).
    Relation,
    /// Relation rows: `relation.type: string`.
    RelationType,
    /// Relation rows: an `endpoint` record (`entity`, `observation`).
    Endpoint(End),
    /// Relation rows: an endpoint's `entity` record (`id`).
    EndpointEntity(End),
    /// Relation rows: an endpoint's `entity.id: string`.
    EndpointId(End),
    /// Relation rows: an endpoint's `observation` record (`geometry`).
    EndpointObservation(End),
    /// Relation rows: an endpoint's `observation.geometry: image_box`.
    EndpointGeometry(End),
}

/// Which end of a relation.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum End {
    /// `source`.
    Source,
    /// `target`.
    Target,
}

/// How [`Node::Convert`] reshapes a value; null stays null.
#[derive(Clone, Debug, PartialEq)]
pub struct Conversion {
    /// The type converted to.
    pub kind: Arc<ValueType>,
    /// What changes.
    pub shape: Reshape,
    /// Whether the result is then checked like [`Node::Check`] against `kind`.
    pub check: bool,
}

/// What a [`Conversion`] changes.
#[derive(Clone, Debug, PartialEq)]
pub enum Reshape {
    /// Nothing; text must be one of `kind`'s choices.
    Choice,
    /// A record built field by field in `kind`'s order.
    Record(Box<[FieldSource]>),
    /// Each list item converted, or kept when `None`.
    Items(Option<Box<Conversion>>),
}

/// Where one field of a converted record comes from.
#[derive(Clone, Debug, PartialEq)]
pub enum FieldSource {
    /// Null: the source record has no such field.
    Null,
    /// Field `n` of the source record, unchanged.
    Field(u32),
    /// Field `n` of the source record, converted.
    Converted(u32, Box<Conversion>),
}
