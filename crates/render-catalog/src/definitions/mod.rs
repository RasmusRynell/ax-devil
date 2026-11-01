//! The language's authoritative value types, operations, primitives and limits.

mod operations;

use std::sync::{Arc, LazyLock};

pub(crate) use operations::{Operation, OperationKind};

use crate::ir::{Bounds, Primitive, TypeName, ValueType};

/// Deepest nesting of a document or an expression.
pub(crate) const MAX_DEPTH: usize = 64;
/// Deepest nesting of template calls.
pub(crate) const MAX_TEMPLATE_DEPTH: usize = 32;
/// Most steps a recipe may run once every template call is expanded.
pub(crate) const MAX_EXPANDED_STEPS: usize = 10_000;
/// Most items of a list or members of an object.
pub(crate) const MAX_COLLECTION: usize = 4096;
/// Largest catalog document in bytes.
pub(crate) const MAX_DOCUMENT_BYTES: usize = 2_000_000;

const UNITS: &[&str] = &["px", "image_width", "image_height", "image_min"];
const ANCHORS: &[&str] = &[
    "top-left",
    "top-center",
    "top-right",
    "center-left",
    "center",
    "center-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
    "top",
    "bottom",
    "left",
    "right",
];
const TEXT_ANCHORS: &[&str] = &[
    "top-left",
    "top-center",
    "top-right",
    "center-left",
    "center",
    "center-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
    "top",
    "bottom",
    "left",
    "right",
    "baseline",
];

/// Shared type: cheap to clone, compared structurally.
pub(crate) type Type = Arc<ValueType>;

/// Every named type of the language.
#[allow(missing_docs)] // Each field is the type of the same name in the catalog language reference.
pub(crate) struct Types {
    pub(crate) number: Type,
    pub(crate) integer: Type,
    pub(crate) boolean: Type,
    pub(crate) string: Type,
    pub(crate) null: Type,
    pub(crate) any: Type,
    pub(crate) empty: Type,
    pub(crate) any_list: Type,
    pub(crate) rgb: Type,
    pub(crate) point: Type,
    pub(crate) image_box: Type,
    pub(crate) vector: Type,
    pub(crate) points: Type,
    pub(crate) length: Type,
    pub(crate) arrow: Type,
    pub(crate) pen_style: Type,
    pub(crate) elision: Type,
    pub(crate) anchor: Type,
    pub(crate) text_anchor: Type,
    pub(crate) corner: Type,
    pub(crate) shape_style: Type,
    pub(crate) text_style_wrapper: Type,
    pub(crate) label_weight: Type,
    pub(crate) label_runs: Type,
    pub(crate) label_style_wrapper: Type,
    pub(crate) color_stops: Type,
    pub(crate) palette: Type,
    pub(crate) text_mapping: Type,
    pub(crate) context: Type,
    pub(crate) scene: Type,
    pub(crate) relation_scene: Type,
    pub(crate) color_binding: Type,
    primitives: [(Primitive, &'static str, Type); 8],
}

/// The language's types, built once.
pub(crate) fn types() -> &'static Types {
    static TYPES: LazyLock<Types> = LazyLock::new(Types::build);
    &TYPES
}

fn scalar(name: TypeName, bounds: Bounds) -> Type {
    Arc::new(ValueType {
        bounds,
        ..ValueType::scalar(name)
    })
}

fn choice(name: TypeName, choices: &'static [&'static str]) -> Type {
    Arc::new(ValueType {
        choices,
        ..ValueType::scalar(name)
    })
}

fn record(name: TypeName, fields: &[(&str, &Type)]) -> Type {
    let fields = fields
        .iter()
        .map(|(key, kind)| (Arc::from(*key), Arc::clone(kind)))
        .collect();
    Arc::new(ValueType {
        fields,
        ..ValueType::scalar(name)
    })
}

fn list(name: TypeName, item: &Type, min_items: Option<u64>, max_items: Option<u64>) -> Type {
    let bounds = Bounds {
        min_items,
        max_items,
        ..Bounds::default()
    };
    Arc::new(ValueType {
        item: Some(Arc::clone(item)),
        bounds,
        ..ValueType::scalar(name)
    })
}

fn optional(kind: &Type) -> Type {
    Arc::new(kind.optional())
}

impl Types {
    fn build() -> Self {
        let none = Bounds::default();
        let number = scalar(TypeName::Number, none);
        let integer = scalar(TypeName::Integer, none);
        let boolean = scalar(TypeName::Bool, none);
        let string = scalar(TypeName::String, none);
        let null = Arc::new(ValueType {
            nullable: true,
            ..ValueType::scalar(TypeName::Null)
        });
        let any = Arc::new(ValueType {
            nullable: true,
            ..ValueType::scalar(TypeName::Any)
        });
        let empty = scalar(TypeName::Empty, none);
        let any_list = list(TypeName::List, &any, None, None);
        let nonnegative = scalar(
            TypeName::Number,
            Bounds {
                minimum: Some(0.0),
                ..none
            },
        );
        let positive = scalar(
            TypeName::Number,
            Bounds {
                exclusive_minimum: Some(0.0),
                ..none
            },
        );
        let channel = scalar(
            TypeName::Integer,
            Bounds {
                minimum: Some(0.0),
                maximum: Some(255.0),
                ..none
            },
        );
        let rgb = list(TypeName::Rgb, &channel, Some(3), Some(3));
        let point = record(TypeName::ImagePoint, &[("x", &number), ("y", &number)]);
        let image_box = record(
            TypeName::ImageBox,
            &[
                ("x", &number),
                ("y", &number),
                ("w", &nonnegative),
                ("h", &nonnegative),
            ],
        );
        let vector = record(TypeName::ImageVector, &[("vx", &number), ("vy", &number)]);
        let points = list(TypeName::ImagePointList, &point, None, None);
        let unit = choice(TypeName::Unit, UNITS);
        let length = record(TypeName::Length, &[("value", &number), ("unit", &unit)]);
        let nonnegative_length = record(
            TypeName::Length,
            &[("value", &nonnegative), ("unit", &unit)],
        );
        let positive_length = record(TypeName::Length, &[("value", &positive), ("unit", &unit)]);
        let arrow = record(
            TypeName::ImageArrow,
            &[("start", &point), ("end", &point), ("head_points", &points)],
        );
        let pen_style = choice(TypeName::PenStyle, &["solid", "dash"]);
        let stroke = record(
            TypeName::Stroke,
            &[
                ("color", &rgb),
                ("width", &nonnegative_length),
                ("pattern", &pen_style),
            ],
        );
        let fill = record(TypeName::Fill, &[("color", &rgb), ("alpha", &channel)]);
        let shape_style = record(
            TypeName::ShapeStyle,
            &[
                ("stroke", &optional(&stroke)),
                ("fill", &optional(&fill)),
                ("radius", &optional(&nonnegative_length)),
            ],
        );
        let text_style = record(
            TypeName::TextStyle,
            &[
                ("color", &rgb),
                ("size", &positive_length),
                ("family", &optional(&string)),
            ],
        );
        let label_weight = choice(
            TypeName::LabelWeight,
            &["regular", "medium", "semibold", "bold"],
        );
        let label_run = record(
            TypeName::LabelRun,
            &[
                ("text", &optional(&string)),
                ("bar", &optional(&number)),
                ("color", &rgb),
                ("weight", &label_weight),
            ],
        );
        let label_style = record(
            TypeName::LabelStyle,
            &[
                ("size", &positive_length),
                ("family", &optional(&string)),
                ("background", &optional(&fill)),
                ("padding_x", &nonnegative_length),
                ("padding_y", &nonnegative_length),
                ("radius", &nonnegative_length),
                ("gap", &nonnegative_length),
            ],
        );
        let color_stop = record(TypeName::ColorStop, &[("at", &number), ("color", &rgb)]);
        let text_entry = record(TypeName::TextEntry, &[("key", &string), ("text", &string)]);
        let context_fields = [
            "width",
            "height",
            "aspect_ratio",
            "scale_factor",
            "px_h",
            "px_v",
            "px_min",
        ];
        let context = record(
            TypeName::Context,
            &context_fields.map(|name| (name, &number)),
        );
        let entity = record(
            TypeName::Entity,
            &[("id", &string), ("motion_state", &optional(&string))],
        );
        let observation = record(
            TypeName::Observation,
            &[
                ("geometry", &image_box),
                ("polygon_points", &optional(&points)),
                ("velocity", &optional(&vector)),
            ],
        );
        let classification = record(
            TypeName::Classification,
            &[
                ("type", &string),
                ("score", &optional(&number)),
                ("confidence", &optional(&number)),
            ],
        );
        let scene = record(
            TypeName::Scene,
            &[
                ("entity", &entity),
                ("observation", &observation),
                ("classification", &optional(&classification)),
            ],
        );
        let endpoint = record(
            TypeName::Endpoint,
            &[
                (
                    "entity",
                    &record(TypeName::EndpointEntity, &[("id", &string)]),
                ),
                (
                    "observation",
                    &record(TypeName::EndpointObservation, &[("geometry", &image_box)]),
                ),
            ],
        );
        let relation_scene = record(
            TypeName::RelationScene,
            &[
                (
                    "relation",
                    &record(TypeName::Relation, &[("type", &string)]),
                ),
                ("source", &endpoint),
                ("target", &endpoint),
            ],
        );
        let label_runs = list(TypeName::LabelRuns, &label_run, Some(1), Some(8));
        let primitives = [
            (
                Primitive::Box,
                "box",
                record(TypeName::BoxFields, &[("geometry", &image_box)]),
            ),
            (
                Primitive::Line,
                "line",
                record(TypeName::LineFields, &[("start", &point), ("end", &point)]),
            ),
            (
                Primitive::Polygon,
                "polygon",
                record(TypeName::PolygonFields, &[("points", &points)]),
            ),
            (
                Primitive::Polyline,
                "polyline",
                record(TypeName::PolylineFields, &[("points", &points)]),
            ),
            (
                Primitive::Point,
                "point",
                record(TypeName::PointFields, &[("position", &point)]),
            ),
            (
                Primitive::Circle,
                "circle",
                record(
                    TypeName::CircleFields,
                    &[("center", &point), ("radius", &nonnegative_length)],
                ),
            ),
            (
                Primitive::Text,
                "text",
                record(
                    TypeName::TextFields,
                    &[
                        ("position", &point),
                        ("text", &string),
                        ("anchor", &choice(TypeName::TextAnchor, TEXT_ANCHORS)),
                    ],
                ),
            ),
            (
                Primitive::Label,
                "label",
                record(
                    TypeName::LabelFields,
                    &[
                        ("position", &point),
                        ("anchor", &choice(TypeName::Anchor, ANCHORS)),
                        ("runs", &label_runs),
                    ],
                ),
            ),
        ];
        Self {
            color_binding: record(
                TypeName::ColorBinding,
                &[("color", &rgb), ("score", &number)],
            ),
            text_style_wrapper: record(TypeName::TextStyleWrapper, &[("text", &text_style)]),
            label_style_wrapper: record(TypeName::LabelStyleWrapper, &[("label", &label_style)]),
            color_stops: list(TypeName::ColorStops, &color_stop, Some(1), None),
            palette: list(TypeName::Palette, &rgb, Some(1), None),
            text_mapping: list(TypeName::TextMapping, &text_entry, None, None),
            elision: choice(TypeName::Elision, &["end", "middle"]),
            anchor: choice(TypeName::Anchor, ANCHORS),
            text_anchor: choice(TypeName::TextAnchor, TEXT_ANCHORS),
            corner: choice(
                TypeName::CornerPosition,
                &["top-left", "top-right", "bottom-left", "bottom-right"],
            ),
            number,
            integer,
            boolean,
            string,
            null,
            any,
            empty,
            any_list,
            rgb,
            point,
            image_box,
            vector,
            points,
            length,
            arrow,
            pen_style,
            shape_style,
            label_weight,
            label_runs,
            context,
            scene,
            relation_scene,
            primitives,
        }
    }

    /// The type a template parameter declares with `"type": name`.
    pub(crate) fn parameter(&self, name: &str) -> Option<&Type> {
        Some(match name {
            "number" => &self.number,
            "integer" => &self.integer,
            "bool" => &self.boolean,
            "string" => &self.string,
            "rgb" => &self.rgb,
            "image_point" => &self.point,
            "image_box" => &self.image_box,
            "image_vector" => &self.vector,
            "image_point_list" => &self.points,
            "length" => &self.length,
            "image_arrow" => &self.arrow,
            "pen_style" => &self.pen_style,
            "elision" => &self.elision,
            "anchor" => &self.anchor,
            "text_anchor" => &self.text_anchor,
            "corner_position" => &self.corner,
            "shape_style" => &self.shape_style,
            "color_stops" => &self.color_stops,
            "palette" => &self.palette,
            "label_weight" => &self.label_weight,
            "label_runs" => &self.label_runs,
            _ => return None,
        })
    }

    /// The primitive called `name` with the type of its fields and of its style.
    pub(crate) fn primitive(&self, name: &str) -> Option<(Primitive, &Type, &Type)> {
        let (primitive, _, fields) = self
            .primitives
            .iter()
            .find(|(_, candidate, _)| *candidate == name)?;
        let style = match primitive {
            Primitive::Text => &self.text_style_wrapper,
            Primitive::Label => &self.label_style_wrapper,
            _ => &self.shape_style,
        };
        Some((*primitive, fields, style))
    }
}

/// The record field whose bounds a quantity type's own bounds mean, such as a length's `value`.
pub(crate) fn quantity_field(name: TypeName) -> Option<&'static str> {
    (name == TypeName::Length).then_some("value")
}
