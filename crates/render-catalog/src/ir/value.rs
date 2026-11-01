//! Values a compiled catalog computes with, and the structural types that describe them.

use std::sync::Arc;

/// One value of the catalog language.
///
/// A record holds its fields in the order of its static type's fields ([`ValueType::fields`]); the compiler
/// guarantees that every record reaching an expression has the layout of that expression's type, so fields are read
/// by index. Choice values (anchors, units, pen styles) are text.
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    /// No value.
    Null,
    /// True or false.
    Bool(bool),
    /// A whole number, as written in the catalog or produced by integer arithmetic on constants.
    Int(i64),
    /// A finite number.
    Number(f64),
    /// Text, including choice values.
    Text(Arc<str>),
    /// Items of a list.
    List(Arc<[Value]>),
    /// Fields of a record, in its type's field order.
    Record(Arc<[Value]>),
}

impl Value {
    /// Whether the value is null.
    pub fn is_null(&self) -> bool {
        matches!(self, Self::Null)
    }

    /// The number, for numbers and whole numbers.
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Self::Int(value) => Some(*value as f64),
            Self::Number(value) => Some(*value),
            _ => None,
        }
    }

    /// The flag, for booleans.
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Self::Bool(value) => Some(*value),
            _ => None,
        }
    }

    /// The text, for text and choice values.
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Self::Text(text) => Some(text),
            _ => None,
        }
    }

    /// The items, for lists.
    pub fn as_list(&self) -> Option<&[Value]> {
        match self {
            Self::List(items) => Some(items),
            _ => None,
        }
    }

    /// Field `index` of a record; null for null, for other values and past the end.
    pub fn field(&self, index: usize) -> &Value {
        match self {
            Self::Record(fields) => fields.get(index).unwrap_or(&Self::Null),
            _ => &Self::Null,
        }
    }
}

/// The name of a value type, used in messages and to pick type-specific rules.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[allow(missing_docs)] // Each variant is the type of the same name in the catalog language reference.
pub enum TypeName {
    Number,
    Integer,
    Bool,
    String,
    Null,
    Any,
    /// The item type of an empty list literal.
    Empty,
    /// A record literal or a record joined from branches.
    Record,
    /// A list literal.
    List,
    /// A template's parameters.
    Parameters,
    /// A recipe's bindings.
    Bindings,
    Rgb,
    ImagePoint,
    ImageBox,
    ImageVector,
    ImagePointList,
    Length,
    Unit,
    ImageArrow,
    PenStyle,
    Elision,
    Anchor,
    TextAnchor,
    CornerPosition,
    Stroke,
    Fill,
    ShapeStyle,
    TextStyle,
    TextStyleWrapper,
    LabelWeight,
    LabelRun,
    LabelRuns,
    LabelStyle,
    LabelStyleWrapper,
    ColorStop,
    ColorStops,
    Palette,
    TextEntry,
    TextMapping,
    Context,
    Entity,
    Observation,
    Classification,
    Scene,
    Endpoint,
    EndpointEntity,
    EndpointObservation,
    Relation,
    RelationScene,
    ColorBinding,
    BoxFields,
    LineFields,
    PolygonFields,
    PolylineFields,
    PointFields,
    CircleFields,
    TextFields,
    LabelFields,
}

impl TypeName {
    /// The name the catalog language and its messages use.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Number => "number",
            Self::Integer => "integer",
            Self::Bool => "bool",
            Self::String => "string",
            Self::Null => "null",
            Self::Any => "any",
            Self::Empty => "empty",
            Self::Record => "record",
            Self::List => "list",
            Self::Parameters => "parameters",
            Self::Bindings => "bindings",
            Self::Rgb => "rgb",
            Self::ImagePoint => "image_point",
            Self::ImageBox => "image_box",
            Self::ImageVector => "image_vector",
            Self::ImagePointList => "image_point_list",
            Self::Length => "length",
            Self::Unit => "unit",
            Self::ImageArrow => "image_arrow",
            Self::PenStyle => "pen_style",
            Self::Elision => "elision",
            Self::Anchor => "anchor",
            Self::TextAnchor => "text_anchor",
            Self::CornerPosition => "corner_position",
            Self::Stroke => "stroke",
            Self::Fill => "fill",
            Self::ShapeStyle => "shape_style",
            Self::TextStyle => "text_style",
            Self::TextStyleWrapper => "text_style_wrapper",
            Self::LabelWeight => "label_weight",
            Self::LabelRun => "label_run",
            Self::LabelRuns => "label_runs",
            Self::LabelStyle => "label_style",
            Self::LabelStyleWrapper => "label_style_wrapper",
            Self::ColorStop => "color_stop",
            Self::ColorStops => "color_stops",
            Self::Palette => "palette",
            Self::TextEntry => "text_entry",
            Self::TextMapping => "text_mapping",
            Self::Context => "context",
            Self::Entity => "entity",
            Self::Observation => "observation",
            Self::Classification => "classification",
            Self::Scene => "scene",
            Self::Endpoint => "endpoint",
            Self::EndpointEntity => "endpoint_entity",
            Self::EndpointObservation => "endpoint_observation",
            Self::Relation => "relation",
            Self::RelationScene => "relation_scene",
            Self::ColorBinding => "color_binding",
            Self::BoxFields => "box_fields",
            Self::LineFields => "line_fields",
            Self::PolygonFields => "polygon_fields",
            Self::PolylineFields => "polyline_fields",
            Self::PointFields => "point_fields",
            Self::CircleFields => "circle_fields",
            Self::TextFields => "text_fields",
            Self::LabelFields => "label_fields",
        }
    }
}

/// Limits a type puts on its values, with JSON Schema's meaning.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Bounds {
    /// Numbers at least this.
    pub minimum: Option<f64>,
    /// Numbers greater than this.
    pub exclusive_minimum: Option<f64>,
    /// Numbers at most this.
    pub maximum: Option<f64>,
    /// Text of at most this many characters.
    pub max_length: Option<u64>,
    /// Lists of at least this many items.
    pub min_items: Option<u64>,
    /// Lists of at most this many items.
    pub max_items: Option<u64>,
}

impl Bounds {
    /// Whether no bound is set.
    pub fn is_empty(&self) -> bool {
        *self == Self::default()
    }
}

/// A closed structural type: a scalar, a record of named fields or a list of items, with explicit nullability.
#[derive(Clone, Debug, PartialEq)]
pub struct ValueType {
    /// What the type is called.
    pub name: TypeName,
    /// Record fields in layout order; empty for scalars and lists.
    pub fields: Box<[(Arc<str>, Arc<ValueType>)]>,
    /// The item type of a list.
    pub item: Option<Arc<ValueType>>,
    /// Whether null is allowed.
    pub nullable: bool,
    /// The allowed texts of a choice type, such as an anchor; empty otherwise.
    pub choices: &'static [&'static str],
    /// Limits on the value itself.
    pub bounds: Bounds,
}

impl ValueType {
    /// A non-null scalar type without bounds.
    pub fn scalar(name: TypeName) -> Self {
        Self {
            name,
            fields: Box::new([]),
            item: None,
            nullable: false,
            choices: &[],
            bounds: Bounds::default(),
        }
    }

    /// Whether values are single numbers, booleans or text.
    pub fn is_scalar(&self) -> bool {
        self.fields.is_empty() && self.item.is_none()
    }

    /// The nullable form of this type.
    pub fn optional(&self) -> Self {
        Self {
            nullable: true,
            ..self.clone()
        }
    }

    /// The non-null form of this type.
    pub fn present(&self) -> Self {
        Self {
            nullable: false,
            ..self.clone()
        }
    }

    /// Checks the bounds and rules of this type for `value` itself and its direct scalar fields or items, as
    /// [`Node::Check`](super::Node::Check) demands; null passes. The error is the message for the user.
    pub fn check(&self, value: &Value) -> Result<(), String> {
        crate::validate::check(value, self)
    }

    /// Checks that `value` fully conforms to this type at every level, as
    /// [`Node::Validate`](super::Node::Validate) demands. The error is the message for the user.
    pub fn validate(&self, value: &Value) -> Result<(), String> {
        crate::validate::conform(value, self, self.name.as_str())
    }

    /// The index and type of the record field `name`.
    pub fn field(&self, name: &str) -> Option<(usize, &Arc<ValueType>)> {
        self.fields
            .iter()
            .enumerate()
            .find(|(_, (key, _))| &**key == name)
            .map(|(index, (_, kind))| (index, kind))
    }
}
