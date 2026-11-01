//! Folding operations over constants while compiling, so constant results are checked against their types early.
//!
//! Only operations whose result needs no render context or kernel are folded; the rest run when rendering, with the
//! same outcome. A fold that would fail leaves the operation to fail when rendering, for the rows that reach it.

use super::expr::CExpr;
use crate::document::Json;
use crate::ir::{Arithmetic, Op};

/// The constant result of `op` over constant `args`, when it is folded.
pub(super) fn operation(op: Op, args: &[CExpr]) -> Option<Json> {
    let values: Option<Vec<&Json>> = args.iter().map(CExpr::constant_value).collect();
    let values = values?;
    let flag = match (op, values.as_slice()) {
        (Op::IsPresent, [value]) => **value != Json::Null,
        (Op::IsNotEmpty, [value]) => match value {
            Json::Str(text) => !text.is_empty(),
            Json::Array(items) => !items.is_empty(),
            _ => false,
        },
        (Op::Gt, [left, right]) => left.as_f64()? > right.as_f64()?,
        (Op::HasArea, [geometry]) => {
            geometry.get("w")?.as_f64()? > 0.0 && geometry.get("h")?.as_f64()? > 0.0
        }
        _ => return None,
    };
    Some(Json::Bool(flag))
}

/// A constant number: whole numbers stay whole through `+`, `-`, `*`, `min`, `max` and `clamp`.
#[derive(Clone, Copy)]
enum Number {
    Int(i64),
    Float(f64),
}

impl Number {
    fn of(value: &Json) -> Option<Self> {
        match value {
            Json::Int(number) => Some(Self::Int(*number)),
            Json::Float(number) => Some(Self::Float(*number)),
            _ => None,
        }
    }

    fn float(self) -> f64 {
        match self {
            Self::Int(number) => number as f64,
            Self::Float(number) => number,
        }
    }

    fn combine(
        self,
        other: Self,
        ints: fn(i64, i64) -> Option<i64>,
        floats: fn(f64, f64) -> f64,
    ) -> Option<Self> {
        match (self, other) {
            (Self::Int(left), Self::Int(right)) => ints(left, right).map(Self::Int),
            _ => Some(Self::Float(floats(self.float(), other.float()))),
        }
    }

    /// The smaller (or larger, when `larger`) of the two, keeping the first on a tie.
    fn pick(self, other: Self, larger: bool) -> Self {
        let other_wins = if larger {
            other.float() > self.float()
        } else {
            other.float() < self.float()
        };
        if other_wins { other } else { self }
    }
}

/// The constant result of arithmetic over constant numbers, when it is folded: not for a zero denominator, a clamp
/// whose minimum exceeds its maximum, or a result that is not finite.
pub(super) fn arithmetic(op: Arithmetic, operands: &[CExpr]) -> Option<Json> {
    let numbers: Option<Vec<Number>> = operands
        .iter()
        .map(|operand| Number::of(operand.constant_value()?))
        .collect();
    let numbers = numbers?;
    let result = match (op, numbers.as_slice()) {
        (Arithmetic::Add, _) => numbers.iter().try_fold(Number::Int(0), |sum, number| {
            sum.combine(*number, i64::checked_add, |a, b| a + b)
        })?,
        (Arithmetic::Mul, _) => numbers.iter().try_fold(Number::Int(1), |product, number| {
            product.combine(*number, i64::checked_mul, |a, b| a * b)
        })?,
        (Arithmetic::Min, [first, rest @ ..]) => rest
            .iter()
            .fold(*first, |least, number| least.pick(*number, false)),
        (Arithmetic::Max, [first, rest @ ..]) => rest
            .iter()
            .fold(*first, |most, number| most.pick(*number, true)),
        (Arithmetic::Sub, [left, right]) => left.combine(*right, i64::checked_sub, |a, b| a - b)?,
        (Arithmetic::Div, [numerator, denominator]) => {
            if denominator.float() == 0.0 {
                return None;
            }
            Number::Float(numerator.float() / denominator.float())
        }
        (Arithmetic::Clamp, [value, minimum, maximum]) => {
            if minimum.float() > maximum.float() {
                return None;
            }
            minimum.pick(maximum.pick(*value, false), true)
        }
        _ => return None,
    };
    match result {
        Number::Int(number) => Some(Json::Int(number)),
        Number::Float(number) if number.is_finite() => Some(Json::Float(number)),
        Number::Float(_) => None,
    }
}
