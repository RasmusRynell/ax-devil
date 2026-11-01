//! Type compatibility, branch unions, the types of constants and the conversions that make an expression fit a type.

use std::sync::Arc;

use super::expr::{CExpr, Form};
use crate::definitions::{Type, types};
use crate::diagnostics::{Problem, codes};
use crate::document::Json;
use crate::ir::{Conversion, FieldSource, Node, Reshape, TypeName, ValueType};
use crate::validate::has_constraints;

/// Whether a value of type `actual` may be used where `expected` is required.
pub(super) fn compatible(actual: &ValueType, expected: &ValueType) -> bool {
    if expected.name == TypeName::Any {
        return true;
    }
    if actual.nullable && !expected.nullable {
        return false;
    }
    if actual.name == TypeName::Null {
        return expected.nullable;
    }
    if actual.name == expected.name
        || (actual.name == TypeName::Integer && expected.name == TypeName::Number)
    {
        return true;
    }
    if !expected.choices.is_empty() {
        return actual.name == TypeName::String || !actual.choices.is_empty();
    }
    if expected.name == TypeName::String && !actual.choices.is_empty() {
        return true;
    }
    if !expected.fields.is_empty() && !actual.fields.is_empty() {
        return actual
            .fields
            .iter()
            .all(|(key, _)| expected.field(key).is_some())
            && expected
                .fields
                .iter()
                .all(|(key, kind)| match actual.field(key) {
                    Some((_, field)) => compatible(field, kind),
                    None => kind.nullable,
                });
    }
    if let (Some(actual), Some(expected)) = (&actual.item, &expected.item) {
        return compatible(actual, expected) || actual.name == TypeName::Empty;
    }
    false
}

/// The type holding values of either type: records join field by field, otherwise the wider type wins.
pub(super) fn union(left: &Type, right: &Type) -> Result<Type, Problem> {
    if left.name == TypeName::Null {
        return Ok(Arc::new(right.optional()));
    }
    if right.name == TypeName::Null {
        return Ok(Arc::new(left.optional()));
    }
    let nullable = left.nullable || right.nullable;
    if left.name == TypeName::Record && right.name == TypeName::Record {
        let null = &types().null;
        let mut keys: Vec<&Arc<str>> = left.fields.iter().map(|(key, _)| key).collect();
        keys.extend(
            right
                .fields
                .iter()
                .map(|(key, _)| key)
                .filter(|key| left.field(key).is_none()),
        );
        let fields = keys
            .into_iter()
            .map(|key| {
                let side = |kind: &Type| {
                    kind.field(key)
                        .map_or_else(|| Arc::clone(null), |(_, kind)| Arc::clone(kind))
                };
                Ok((Arc::clone(key), union(&side(left), &side(right))?))
            })
            .collect::<Result<_, Problem>>()?;
        return Ok(Arc::new(ValueType {
            fields,
            nullable,
            ..ValueType::scalar(TypeName::Record)
        }));
    }
    let (a, b) = (left.present(), right.present());
    if compatible(&a, &b) {
        return Ok(Arc::new(ValueType { nullable, ..b }));
    }
    if compatible(&b, &a) {
        return Ok(Arc::new(ValueType { nullable, ..a }));
    }
    let message = format!(
        "Incompatible branch types {} and {}.",
        left.name.as_str(),
        right.name.as_str()
    );
    Err(Problem::new(codes::TYPE_MISMATCH, message))
}

fn shown(kind: &ValueType) -> String {
    format!(
        "{}{}",
        kind.name.as_str(),
        if kind.nullable { "|null" } else { "" }
    )
}

/// The type of the constant `value`: objects are records of their keys in sorted order, and list items join to one
/// type, or `any` when they conflict.
pub(super) fn literal_type(value: &Json) -> Arc<ValueType> {
    let t = types();
    match value {
        Json::Null => Arc::clone(&t.null),
        Json::Bool(_) => Arc::clone(&t.boolean),
        Json::Int(_) => Arc::clone(&t.integer),
        Json::Float(_) => Arc::clone(&t.number),
        Json::Str(_) => Arc::clone(&t.string),
        Json::Object(members) => {
            let mut fields: Vec<(Arc<str>, Arc<ValueType>)> = members
                .iter()
                .map(|(key, member)| (Arc::from(key.as_str()), literal_type(member)))
                .collect();
            fields.sort_by(|left, right| left.0.cmp(&right.0));
            Arc::new(ValueType {
                fields: fields.into(),
                ..ValueType::scalar(TypeName::Record)
            })
        }
        Json::Array(items) => {
            let item = items.iter().map(literal_type).try_fold(
                None,
                |joined: Option<Arc<ValueType>>, kind| match joined {
                    None => Some(Some(kind)),
                    Some(joined) => union(&joined, &kind).ok().map(Some),
                },
            );
            let item = match item {
                Some(Some(kind)) => kind,
                Some(None) => Arc::clone(&t.empty),
                None => Arc::clone(&t.any),
            };
            Arc::new(ValueType {
                item: Some(item),
                ..ValueType::scalar(TypeName::List)
            })
        }
    }
}

/// Converts `expression` to `expected`, adding the checks its bounds need. A bounded scalar is checked by itself
/// unless it is a `member` of a record or list, whose own check covers its scalar parts.
pub(super) fn coerce(expression: CExpr, expected: &Type, member: bool) -> Result<CExpr, Problem> {
    if expected.name == TypeName::Any {
        return Ok(expression);
    }
    let expected = if expected.nullable && !expression.kind.nullable {
        Arc::new(expected.present())
    } else {
        Arc::clone(expected)
    };
    if let Form::Const(value, _) = expression.form {
        return CExpr::constant(value, expected, expression.loc, &expression.at);
    }
    if !compatible(&expression.kind, &expected) {
        let message = format!(
            "Expected {}, got {}.",
            shown(&expected),
            shown(&expression.kind)
        );
        return Err(Problem::new(codes::TYPE_MISMATCH, message).at(&expression.at));
    }
    if expression.kind == expected {
        return Ok(expression);
    }
    if let Form::Members { members, .. } = &expression.form
        && !expected.fields.is_empty()
    {
        return rebuild(members, &expression, expected);
    }
    Ok(match conversion(&expression.kind, &expected, true) {
        None if expected.is_scalar() && !expected.bounds.is_empty() && !member => {
            let (loc, at) = (expression.loc, expression.at.clone());
            CExpr::node(
                Node::Check(Box::new(expression.lower())),
                expected,
                loc,
                &at,
            )
        }
        None => expression.retyped(expected),
        Some(conversion) => {
            let (loc, at) = (expression.loc, expression.at.clone());
            let node = Node::Convert {
                value: Box::new(expression.lower()),
                conversion: Arc::new(conversion),
            };
            CExpr::node(node, expected, loc, &at)
        }
    })
}

/// A record expression rebuilt member by member as `expected`; absent members are null.
fn rebuild(
    members: &[(Arc<str>, CExpr)],
    record: &CExpr,
    expected: Type,
) -> Result<CExpr, Problem> {
    let mut rebuilt = Vec::with_capacity(expected.fields.len());
    for (key, kind) in expected.fields.iter() {
        let supplied = members
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, member)| member.clone());
        let member = match supplied {
            Some(member) => coerce(member, kind, true)?,
            None if kind.nullable => {
                CExpr::constant(Json::Null, Arc::clone(kind), record.loc, &record.at)?
            }
            None => {
                return Err(
                    Problem::value(format!("Expected {}, got null.", kind.name.as_str()))
                        .at(&record.at),
                );
            }
        };
        rebuilt.push((Arc::clone(key), member));
    }
    let check = has_constraints(&expected);
    CExpr::record(rebuilt, expected, check, record.loc, &record.at)
}

/// `expression` laid out as `kind`, a type it is known to fit (such as a union of branches), without checks.
pub(super) fn fit(expression: CExpr, kind: &Type) -> Result<CExpr, Problem> {
    if expression.kind == *kind || kind.name == TypeName::Any {
        return Ok(expression);
    }
    if let Form::Const(value, _) = expression.form {
        return CExpr::constant(value, Arc::clone(kind), expression.loc, &expression.at);
    }
    Ok(match conversion(&expression.kind, kind, false) {
        None => expression.retyped(Arc::clone(kind)),
        Some(conversion) => {
            let (loc, at) = (expression.loc, expression.at.clone());
            let node = Node::Convert {
                value: Box::new(expression.lower()),
                conversion: Arc::new(conversion),
            };
            CExpr::node(node, Arc::clone(kind), loc, &at)
        }
    })
}

/// How a value of `actual` becomes a value of `expected`, or `None` when its layout already fits and no check is
/// due. `checked` asks for the constraints of `expected` to be checked.
fn conversion(actual: &ValueType, expected: &Type, checked: bool) -> Option<Conversion> {
    if actual == &**expected || expected.name == TypeName::Any {
        return None;
    }
    let check = checked && has_constraints(expected);
    if !expected.fields.is_empty() {
        let sources: Box<[FieldSource]> = expected
            .fields
            .iter()
            .map(|(key, kind)| match actual.field(key) {
                None => FieldSource::Null,
                Some((index, field)) => {
                    let index = u32::try_from(index).unwrap_or(u32::MAX);
                    match conversion(field, kind, checked) {
                        None => FieldSource::Field(index),
                        Some(conversion) => FieldSource::Converted(index, Box::new(conversion)),
                    }
                }
            })
            .collect();
        let identity = actual.fields.len() == sources.len()
            && sources
                .iter()
                .enumerate()
                .all(|(position, source)| *source == FieldSource::Field(position as u32));
        if identity && !check {
            return None;
        }
        return Some(Conversion {
            kind: Arc::clone(expected),
            shape: Reshape::Record(sources),
            check,
        });
    }
    if let (Some(actual_item), Some(expected_item)) = (&actual.item, &expected.item) {
        let item = if actual_item.name == TypeName::Empty {
            None
        } else {
            conversion(actual_item, expected_item, checked).map(Box::new)
        };
        if item.is_none() && !check {
            return None;
        }
        return Some(Conversion {
            kind: Arc::clone(expected),
            shape: Reshape::Items(item),
            check,
        });
    }
    if !expected.choices.is_empty() && actual.choices != expected.choices {
        return Some(Conversion {
            kind: Arc::clone(expected),
            shape: Reshape::Choice,
            check: false,
        });
    }
    None
}
