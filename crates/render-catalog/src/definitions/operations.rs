//! The operations a `call` names: their arguments, which may be omitted, and their declared result.

use std::sync::{Arc, LazyLock};

use super::{Type, Types, types};
use crate::ir::{Arithmetic, Op};

/// How an operation compiles.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum OperationKind {
    /// `if`: a lazy choice between two branches.
    If,
    /// `coalesce`: the first non-null of lazy values.
    Coalesce,
    /// Arithmetic over numbers and lengths.
    Arithmetic(Arithmetic),
    /// Any other operation.
    Op(Op),
}

/// One operation's contract.
pub(crate) struct Operation {
    /// The name a `call` uses.
    pub(crate) name: &'static str,
    /// How it compiles.
    pub(crate) kind: OperationKind,
    /// Arguments in order, with the type each is compiled against.
    pub(crate) arguments: Vec<(&'static str, Type)>,
    /// Arguments that may be omitted; they are null then.
    pub(crate) optional: &'static [&'static str],
    /// The declared result; `if`, `coalesce`, arithmetic and some text operations refine it.
    pub(crate) result: Type,
}

impl Operation {
    /// The operation a `call` names.
    pub(crate) fn named(name: &str) -> Option<&'static Operation> {
        static OPERATIONS: LazyLock<Vec<Operation>> = LazyLock::new(|| build(types()));
        OPERATIONS.iter().find(|operation| operation.name == name)
    }
}

fn operation(
    name: &'static str,
    kind: OperationKind,
    result: &Type,
    optional: &'static [&'static str],
    arguments: &[(&'static str, &Type)],
) -> Operation {
    Operation {
        name,
        kind,
        arguments: arguments
            .iter()
            .map(|(key, kind)| (*key, Arc::clone(kind)))
            .collect(),
        optional,
        result: Arc::clone(result),
    }
}

fn op(name: &'static str, op: Op, result: &Type, arguments: &[(&'static str, &Type)]) -> Operation {
    operation(name, OperationKind::Op(op), result, &[], arguments)
}

fn build(t: &Types) -> Vec<Operation> {
    let optional = |kind: &Type| Arc::new(kind.optional());
    let any = &t.any;
    let values: &[(&str, &Type)] = &[("values", &t.any_list)];
    let arithmetic = |name, op, arguments: &[(&'static str, &Type)]| {
        operation(name, OperationKind::Arithmetic(op), any, &[], arguments)
    };
    let (length, number, string, image_box, point) =
        (&t.length, &t.number, &t.string, &t.image_box, &t.point);
    let (optional_length, optional_number, optional_string) =
        (optional(length), optional(number), optional(string));
    vec![
        operation(
            "if",
            OperationKind::If,
            any,
            &[],
            &[("condition", &t.boolean), ("then", any), ("else", any)],
        ),
        operation("coalesce", OperationKind::Coalesce, any, &[], values),
        arithmetic("add", Arithmetic::Add, values),
        arithmetic("mul", Arithmetic::Mul, values),
        arithmetic("min", Arithmetic::Min, values),
        arithmetic("max", Arithmetic::Max, values),
        arithmetic("sub", Arithmetic::Sub, &[("left", any), ("right", any)]),
        arithmetic(
            "div",
            Arithmetic::Div,
            &[("numerator", any), ("denominator", any)],
        ),
        arithmetic(
            "clamp",
            Arithmetic::Clamp,
            &[("value", any), ("minimum", any), ("maximum", any)],
        ),
        op(
            "gt",
            Op::Gt,
            &t.boolean,
            &[("left", number), ("right", number)],
        ),
        op("is_present", Op::IsPresent, &t.boolean, &[("value", any)]),
        op(
            "is_not_empty",
            Op::IsNotEmpty,
            &t.boolean,
            &[("value", any)],
        ),
        op(
            "has_area",
            Op::HasArea,
            &t.boolean,
            &[("geometry", image_box)],
        ),
        op(
            "box_anchor",
            Op::BoxAnchor,
            point,
            &[("geometry", image_box), ("anchor", &t.anchor)],
        ),
        op(
            "box_min_size",
            Op::BoxMinSize,
            length,
            &[("geometry", image_box)],
        ),
        op(
            "offset_point",
            Op::OffsetPoint,
            point,
            &[("point", point), ("dx", length), ("dy", length)],
        ),
        operation(
            "inset_box",
            OperationKind::Op(Op::InsetBox),
            image_box,
            &["margin", "fraction"],
            &[
                ("geometry", image_box),
                ("margin", &optional_length),
                ("fraction", &optional_number),
            ],
        ),
        operation(
            "resize_box",
            OperationKind::Op(Op::ResizeBox),
            image_box,
            &["width", "height"],
            &[
                ("geometry", image_box),
                ("width", &optional_length),
                ("height", &optional_length),
                ("anchor", &t.anchor),
            ],
        ),
        op(
            "scale_box",
            Op::ScaleBox,
            image_box,
            &[
                ("geometry", image_box),
                ("x_factor", number),
                ("y_factor", number),
                ("anchor", &t.anchor),
            ],
        ),
        op(
            "place_box",
            Op::PlaceBox,
            image_box,
            &[
                ("container", image_box),
                ("width", length),
                ("height", length),
                ("anchor", &t.anchor),
                ("margin", length),
            ],
        ),
        op(
            "vector_arrow",
            Op::VectorArrow,
            &optional(&t.arrow),
            &[
                ("origin", point),
                ("vector", &optional(&t.vector)),
                ("gain", number),
                ("max_length", length),
                ("head_length", length),
                ("head_half_width", length),
                ("min_magnitude", number),
            ],
        ),
        operation(
            "trim_text",
            OperationKind::Op(Op::TrimText),
            &optional_string,
            &["elide"],
            &[
                ("text", &optional_string),
                ("max_length", &t.integer),
                ("delimiter", &optional_string),
                ("suffix", string),
                ("elide", &optional(&t.elision)),
            ],
        ),
        op(
            "number_text",
            Op::NumberText,
            &optional_string,
            &[
                ("value", &optional_number),
                ("precision", &t.integer),
                ("hide_zero", &t.boolean),
            ],
        ),
        op(
            "lookup_text",
            Op::LookupText,
            &optional_string,
            &[
                ("text", &optional_string),
                ("mapping", &t.text_mapping),
                ("default", string),
            ],
        ),
        op(
            "color_ramp",
            Op::ColorRamp,
            &t.rgb,
            &[("value", number), ("stops", &t.color_stops)],
        ),
        op(
            "pick_color",
            Op::PickColor,
            &t.rgb,
            &[("text", string), ("colors", &t.palette)],
        ),
    ]
}
