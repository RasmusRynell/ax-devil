//! Constants checked against a type and laid out as a [`Value`] of it.

use std::sync::Arc;

use super::{check_own, expected_list, expected_text, field_label, is_open_record};
use crate::definitions::MAX_COLLECTION;
use crate::document::Json;
use crate::ir::{TypeName, Value, ValueType};

/// Checks the constant `value` against `kind` and lays it out as a [`Value`] of that type: records take `kind`'s
/// field order, absent fields are null and unknown fields are rejected. `label` names the value in bound messages.
pub(crate) fn literal(value: &Json, kind: &ValueType, label: &str) -> Result<Value, String> {
    let name = kind.name.as_str();
    if *value == Json::Null {
        return if kind.nullable {
            Ok(Value::Null)
        } else {
            Err(format!("Expected {name}, got null."))
        };
    }
    let result = match kind.name {
        TypeName::Any | TypeName::Null | TypeName::Empty => return Ok(own(value)),
        TypeName::Number | TypeName::Integer => match value {
            Json::Int(number) => Value::Int(*number),
            Json::Float(_) if kind.name == TypeName::Integer => {
                return Err("Expected an integer.".to_string());
            }
            Json::Float(number) => Value::Number(*number),
            _ => return Err("number must be a number.".to_string()),
        },
        TypeName::Bool => match value {
            Json::Bool(flag) => Value::Bool(*flag),
            _ => return Err("Expected a boolean.".to_string()),
        },
        _ if kind.name == TypeName::String || !kind.choices.is_empty() => match value {
            Json::Str(text) if kind.choices.is_empty() || kind.choices.contains(&text.as_str()) => {
                Value::Text(Arc::from(text.as_str()))
            }
            _ => return Err(expected_text(kind)),
        },
        _ if !kind.fields.is_empty() || is_open_record(kind.name) => {
            let Json::Object(members) = value else {
                return Err(format!("Expected {name} record."));
            };
            let mut unknown: Vec<&str> = members
                .iter()
                .map(|(key, _)| key.as_str())
                .filter(|key| kind.field(key).is_none())
                .collect();
            if !unknown.is_empty() {
                unknown.sort_unstable();
                return Err(format!("Unknown {name} fields: {}.", unknown.join(", ")));
            }
            let fields = kind
                .fields
                .iter()
                .map(|(key, field_kind)| {
                    let member = value.get(key).unwrap_or(&Json::Null);
                    literal(member, field_kind, field_label(kind, key, label))
                })
                .collect::<Result<Vec<_>, _>>()?;
            Value::Record(fields.into())
        }
        _ => match (value, &kind.item) {
            (Json::Array(items), Some(item)) if items.len() <= MAX_COLLECTION => {
                let item_label = format!("{label} item");
                let items = items
                    .iter()
                    .map(|value| literal(value, item, &item_label))
                    .collect::<Result<Vec<_>, _>>()?;
                Value::List(items.into())
            }
            _ => return Err(expected_list(kind)),
        },
    };
    check_own(&result, kind, label)?;
    Ok(result)
}

/// `value` laid out by its own literal type.
fn own(value: &Json) -> Value {
    match value {
        Json::Null => Value::Null,
        Json::Bool(flag) => Value::Bool(*flag),
        Json::Int(number) => Value::Int(*number),
        Json::Float(number) => Value::Number(*number),
        Json::Str(text) => Value::Text(Arc::from(text.as_str())),
        Json::Array(items) => Value::List(items.iter().map(own).collect()),
        Json::Object(members) => {
            let mut sorted: Vec<&(String, Json)> = members.iter().collect();
            sorted.sort_by(|left, right| left.0.cmp(&right.0));
            Value::Record(sorted.into_iter().map(|(_, member)| own(member)).collect())
        }
    }
}
