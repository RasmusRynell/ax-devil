//! Checks of values against the closed value types: structure, bounds and the rules bounds cannot express.
//!
//! The same checks serve constants while compiling and computed values while evaluating, so a value is rejected with
//! the same message wherever it comes from.

mod format;
mod literal;
pub(crate) mod structure;

pub(crate) use format::allowed_values;
pub(crate) use literal::literal;

use crate::definitions::{MAX_COLLECTION, quantity_field};
use crate::ir::{TypeName, Value, ValueType};

/// Names a record field in messages: a quantity's number takes the quantity's label, other fields their key.
pub(crate) fn field_label<'a>(kind: &ValueType, key: &'a str, label: &'a str) -> &'a str {
    if quantity_field(kind.name) == Some(key) {
        label
    } else {
        key
    }
}

/// Whether `kind` declares bounds or a rule for itself, or bounds for a direct scalar field or item.
pub(crate) fn has_constraints(kind: &ValueType) -> bool {
    let bounded_part = |part: &ValueType| part.is_scalar() && !part.bounds.is_empty();
    !kind.bounds.is_empty()
        || has_rule(kind.name)
        || kind.fields.iter().any(|(_, field)| bounded_part(field))
        || kind.item.as_deref().is_some_and(bounded_part)
}

fn has_rule(name: TypeName) -> bool {
    matches!(
        name,
        TypeName::PolygonFields
            | TypeName::PolylineFields
            | TypeName::ColorStops
            | TypeName::TextMapping
    )
}

/// Checks a value's own bounds and rule, and the bounds of its direct scalar fields or items. Null passes.
///
/// Nested records and lists are checked where they are built, so only the value's own level is checked here.
pub(crate) fn check(value: &Value, kind: &ValueType) -> Result<(), String> {
    if value.is_null() {
        return Ok(());
    }
    let label = kind.name.as_str();
    if let (Some(item), Value::List(items)) = (&kind.item, value)
        && item.is_scalar()
        && !item.bounds.is_empty()
    {
        let item_label = format!("{label} item");
        for value in items.iter() {
            check_bounds(value, item, &item_label)?;
        }
    }
    for (index, (key, field_kind)) in kind.fields.iter().enumerate() {
        let field = value.field(index);
        if !field.is_null() && field_kind.is_scalar() {
            check_bounds(field, field_kind, field_label(kind, key, label))?;
        }
    }
    check_own(value, kind, label)
}

/// Checks that `value` fully conforms to `kind`: structure, nullability, finite numbers, choices, bounds and rules at
/// every level. `label` names the value in bound messages.
pub(crate) fn conform(value: &Value, kind: &ValueType, label: &str) -> Result<(), String> {
    let name = kind.name.as_str();
    if value.is_null() {
        return if kind.nullable {
            Ok(())
        } else {
            Err(format!("Expected {name}, got null."))
        };
    }
    match kind.name {
        TypeName::Any => return Ok(()),
        TypeName::Number | TypeName::Integer => match value {
            Value::Int(_) => {}
            Value::Number(number) if kind.name == TypeName::Number && number.is_finite() => {}
            Value::Number(number) if !number.is_finite() => {
                return Err("number must be finite.".to_string());
            }
            Value::Number(_) => return Err("Expected an integer.".to_string()),
            _ => return Err("number must be a number.".to_string()),
        },
        TypeName::Bool if !matches!(value, Value::Bool(_)) => {
            return Err("Expected a boolean.".to_string());
        }
        _ if kind.name == TypeName::String || !kind.choices.is_empty() => {
            let fits = value
                .as_str()
                .is_some_and(|text| kind.choices.is_empty() || kind.choices.contains(&text));
            if !fits {
                return Err(expected_text(kind));
            }
        }
        _ if !kind.fields.is_empty() || is_open_record(kind.name) => {
            let Value::Record(fields) = value else {
                return Err(format!("Expected {name} record."));
            };
            for (index, (key, field_kind)) in kind.fields.iter().enumerate() {
                let field = fields.get(index).unwrap_or(&Value::Null);
                conform(field, field_kind, field_label(kind, key, label))?;
            }
        }
        _ if kind.item.is_some() => {
            let (Value::List(items), Some(item)) = (value, &kind.item) else {
                return Err(expected_list(kind));
            };
            if items.len() > MAX_COLLECTION {
                return Err(expected_list(kind));
            }
            let item_label = format!("{label} item");
            for value in items.iter() {
                conform(value, item, &item_label)?;
            }
        }
        _ => {}
    }
    check_own(value, kind, label)
}

/// Whether `name` is a record type whose fields are not fixed by the language.
pub(crate) fn is_open_record(name: TypeName) -> bool {
    matches!(
        name,
        TypeName::Record | TypeName::Parameters | TypeName::Bindings
    )
}

pub(crate) fn expected_text(kind: &ValueType) -> String {
    if kind.choices.is_empty() {
        format!("Expected {}: text.", kind.name.as_str())
    } else {
        format!(
            "Expected {}: one of {}.",
            kind.name.as_str(),
            kind.choices.join(", ")
        )
    }
}

pub(crate) fn expected_list(kind: &ValueType) -> String {
    format!(
        "Expected {} with at most {MAX_COLLECTION} items.",
        kind.name.as_str()
    )
}

/// Checks the bounds `kind` declares for the value itself and the rule of its type.
pub(crate) fn check_own(value: &Value, kind: &ValueType, label: &str) -> Result<(), String> {
    check_bounds(value, kind, label)?;
    rule(value, kind.name)
}

fn check_bounds(value: &Value, kind: &ValueType, label: &str) -> Result<(), String> {
    if kind.bounds.is_empty() || within_bounds(value, kind) {
        Ok(())
    } else {
        Err(format!("{label} must be {}.", allowed_values(kind)))
    }
}

fn within_bounds(value: &Value, kind: &ValueType) -> bool {
    let bounds = &kind.bounds;
    match value {
        Value::Text(text) => bounds
            .max_length
            .is_none_or(|limit| text.chars().count() as u64 <= limit),
        Value::List(items) => {
            let count = items.len() as u64;
            bounds.min_items.is_none_or(|limit| count >= limit)
                && bounds.max_items.is_none_or(|limit| count <= limit)
        }
        _ => value.as_f64().is_none_or(|number| {
            bounds.minimum.is_none_or(|limit| number >= limit)
                && bounds.exclusive_minimum.is_none_or(|limit| number > limit)
                && bounds.maximum.is_none_or(|limit| number <= limit)
        }),
    }
}

fn rule(value: &Value, name: TypeName) -> Result<(), String> {
    match name {
        TypeName::PolygonFields | TypeName::PolylineFields => {
            let minimum = if name == TypeName::PolygonFields {
                3
            } else {
                2
            };
            let count = value.field(0).as_list().map_or(0, <[Value]>::len);
            if count != 0 && count < minimum {
                return Err(format!("Geometry requires at least {minimum} points."));
            }
        }
        TypeName::ColorStops => {
            let stops = value.as_list().unwrap_or_default();
            let at = |stop: &Value| stop.field(0).as_f64().unwrap_or(f64::NAN);
            let increasing = |pair: &[Value]| {
                at(&pair[0]).partial_cmp(&at(&pair[1])) == Some(std::cmp::Ordering::Less)
            };
            if !stops.windows(2).all(increasing) {
                return Err("Color stops must be strictly increasing.".to_string());
            }
        }
        TypeName::TextMapping => {
            let entries = value.as_list().unwrap_or_default();
            let keys: Vec<&str> = entries
                .iter()
                .filter_map(|entry| entry.field(0).as_str())
                .collect();
            if keys
                .iter()
                .enumerate()
                .any(|(index, key)| keys[..index].contains(key))
            {
                return Err("Text mapping keys must be unique.".to_string());
            }
        }
        _ => {}
    }
    Ok(())
}
