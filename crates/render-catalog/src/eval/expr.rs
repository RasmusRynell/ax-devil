//! Evaluating expressions: references, records, branches, arithmetic, checks and conversions.

use std::sync::Arc;

use super::value::{finite, number, pixel_length};
use super::{Fail, Frame, Machine, invalid};
use crate::ir::{Arithmetic, Conversion, Expr, FieldSource, Node, Operand, Reshape, Value};
use crate::validate::expected_text;

/// Most arguments any operation takes.
const MAX_ARGS: usize = 7;

impl Machine<'_, '_> {
    /// The value of `expr`; a failure is placed at the innermost expression that has a place.
    pub(super) fn eval(&mut self, frame: &mut Frame<'_>, expr: &Expr) -> Result<Value, Fail> {
        self.node(frame, expr)
            .map_err(|failure| failure.or_at(expr.loc))
    }

    fn node(&mut self, frame: &mut Frame<'_>, expr: &Expr) -> Result<Value, Fail> {
        match &expr.node {
            Node::Const(value) => Ok(value.clone()),
            Node::Slot(index) => self.slot(frame, *index as usize),
            Node::Parameter(index) => Ok(frame.params.field(*index as usize).clone()),
            Node::Context(field) => Ok(Value::Number(self.numbers.field(*field))),
            Node::Scene(field) => Ok(self.row.map_or(Value::Null, |row| row.read(*field))),
            Node::Binding(index) => self.binding(*index as usize),
            Node::Field { value, path } => {
                let base = self.eval(frame, value)?;
                let found = path
                    .iter()
                    .fold(&base, |value, index| value.field(*index as usize));
                Ok(found.clone())
            }
            Node::Record(items) => Ok(Value::Record(self.items(frame, items)?)),
            Node::List(items) => Ok(Value::List(self.items(frame, items)?)),
            Node::If(branches) => {
                let chosen = if self.eval(frame, &branches[0])?.as_bool() == Some(true) {
                    &branches[1]
                } else {
                    &branches[2]
                };
                self.eval(frame, chosen)
            }
            Node::Coalesce(items) => {
                for item in items {
                    let value = self.eval(frame, item)?;
                    if !value.is_null() {
                        return Ok(value);
                    }
                }
                Ok(Value::Null)
            }
            Node::Arithmetic {
                op,
                operands,
                length,
            } => {
                let result = self.arithmetic(frame, *op, operands)?;
                Ok(if *length {
                    pixel_length(result)
                } else {
                    Value::Number(result)
                })
            }
            Node::Call { op, args } => {
                let mut values: [Value; MAX_ARGS] = std::array::from_fn(|_| Value::Null);
                let slots = values
                    .get_mut(..args.len())
                    .ok_or_else(|| invalid("Too many arguments."))?;
                for (slot, arg) in slots.iter_mut().zip(args.iter()) {
                    *slot = self.eval(frame, arg)?;
                }
                self.call(*op, &values[..args.len()])
            }
            Node::Check(inner) => {
                let value = self.eval(frame, inner)?;
                expr.kind.check(&value).map_err(invalid)?;
                Ok(value)
            }
            Node::Convert { value, conversion } => {
                let value = self.eval(frame, value)?;
                convert(&value, conversion).map_err(invalid)
            }
            Node::Validate(inner) => {
                let value = self.eval(frame, inner)?;
                expr.kind.validate(&value).map_err(invalid)?;
                Ok(value)
            }
        }
    }

    fn items(&mut self, frame: &mut Frame<'_>, items: &[Expr]) -> Result<Arc<[Value]>, Fail> {
        let mut values = Vec::with_capacity(items.len());
        for item in items {
            values.push(self.eval(frame, item)?);
        }
        Ok(values.into())
    }

    /// Slot `index` of the running program, computed on first demand.
    fn slot(&mut self, frame: &mut Frame<'_>, index: usize) -> Result<Value, Fail> {
        if let Some(Some(value)) = frame.slots.get(index) {
            return Ok(value.clone());
        }
        let program = frame.program;
        let expr = program
            .slots
            .get(index)
            .ok_or_else(|| invalid("Unknown calculated value."))?;
        let value = self.eval(frame, expr)?;
        frame.slots[index] = Some(value.clone());
        Ok(value)
    }

    fn binding(&mut self, index: usize) -> Result<Value, Fail> {
        if let Some(Some(value)) = self.binding_values.get(index) {
            return Ok(value.clone());
        }
        let (Some(row), Some(binding)) = (self.row, self.bindings.get(index)) else {
            return Ok(Value::Null);
        };
        let value = row.binding(binding)?;
        self.binding_values[index] = Some(value.clone());
        Ok(value)
    }

    /// A length in pixels; lengths in image units follow the render target.
    pub(super) fn pixels(&self, length: &Value) -> Result<f64, Fail> {
        let amount = number(length.field(0));
        let scale = match length.field(1).as_str() {
            Some("image_width") => self.numbers.width,
            Some("image_height") => self.numbers.height,
            Some("image_min") => self.numbers.scale,
            _ => return Ok(amount),
        };
        finite(amount * scale)
    }

    fn arithmetic(
        &mut self,
        frame: &mut Frame<'_>,
        op: Arithmetic,
        operands: &[Operand],
    ) -> Result<f64, Fail> {
        let mut values = [0.0; 3];
        let mut fold: Option<f64> = None;
        let sums = matches!(
            op,
            Arithmetic::Add | Arithmetic::Mul | Arithmetic::Min | Arithmetic::Max
        );
        for (position, operand) in operands.iter().enumerate() {
            let value = self.eval(frame, &operand.value)?;
            let value = if operand.length {
                self.pixels(&value)?
            } else {
                number(&value)
            };
            if sums {
                fold = Some(match (op, fold) {
                    (Arithmetic::Add, previous) => previous.unwrap_or(0.0) + value,
                    (Arithmetic::Mul, previous) => previous.unwrap_or(1.0) * value,
                    (Arithmetic::Min, Some(previous)) if value < previous => value,
                    (Arithmetic::Max, Some(previous)) if value > previous => value,
                    (_, Some(previous)) => previous,
                    (_, None) => value,
                });
            } else if let Some(slot) = values.get_mut(position) {
                *slot = value;
            }
        }
        let result = match op {
            Arithmetic::Sub => values[0] - values[1],
            Arithmetic::Div => {
                if values[1] == 0.0 {
                    return Err(invalid("Cannot divide by zero."));
                }
                values[0] / values[1]
            }
            Arithmetic::Clamp => {
                let [value, minimum, maximum] = values;
                if minimum > maximum {
                    return Err(invalid("Clamp minimum exceeds maximum."));
                }
                let capped = if value < maximum { value } else { maximum };
                if capped > minimum { capped } else { minimum }
            }
            _ => fold.unwrap_or(0.0),
        };
        finite(result)
    }
}

/// `value` reshaped as `conversion` says; null stays null.
fn convert(value: &Value, conversion: &Conversion) -> Result<Value, String> {
    if value.is_null() {
        return Ok(Value::Null);
    }
    let converted = match &conversion.shape {
        Reshape::Choice => {
            let fits = value
                .as_str()
                .is_some_and(|text| conversion.kind.choices.contains(&text));
            if !fits {
                return Err(expected_text(&conversion.kind));
            }
            value.clone()
        }
        Reshape::Record(sources) => {
            let mut fields = Vec::with_capacity(sources.len());
            for source in sources.iter() {
                fields.push(match source {
                    FieldSource::Null => Value::Null,
                    FieldSource::Field(index) => value.field(*index as usize).clone(),
                    FieldSource::Converted(index, inner) => {
                        convert(value.field(*index as usize), inner)?
                    }
                });
            }
            Value::Record(fields.into())
        }
        Reshape::Items(inner) => match (inner, value.as_list()) {
            (Some(inner), Some(items)) => Value::List(
                items
                    .iter()
                    .map(|item| convert(item, inner))
                    .collect::<Result<_, _>>()?,
            ),
            _ => value.clone(),
        },
    };
    if conversion.check {
        conversion.kind.check(&converted)?;
    }
    Ok(converted)
}
