//! Shape checks on the raw document: objects, keys, lists, descriptive text, proven references and parameter
//! declarations.

use std::sync::Arc;

use crate::definitions::{quantity_field, types};
use crate::diagnostics::{Pointer, Problem, codes};
use crate::document::Json;
use crate::ir::{Bounds, TypeName, ValueType};

/// `value` as an object, or an `invalid_structure` problem at `at`.
pub(crate) fn object<'a>(value: &'a Json, at: &Pointer) -> Result<&'a [(String, Json)], Problem> {
    value
        .as_object()
        .ok_or_else(|| Problem::new(codes::INVALID_STRUCTURE, "Expected an object.").at(at))
}

/// `value` as a list, or an `invalid_structure` problem at `at`.
pub(crate) fn list<'a>(value: &'a Json, at: &Pointer) -> Result<&'a [Json], Problem> {
    match value {
        Json::Array(items) => Ok(items),
        _ => Err(Problem::new(codes::INVALID_STRUCTURE, "Expected an array.").at(at)),
    }
}

/// Checks that `value` has every `required` key and no key outside `allowed` (which includes `required`).
pub(crate) fn keys(
    value: &[(String, Json)],
    allowed: &[&str],
    required: &[&str],
    at: &Pointer,
) -> Result<(), Problem> {
    let unknown: Vec<&str> = value
        .iter()
        .map(|(key, _)| key.as_str())
        .filter(|key| !allowed.contains(key))
        .collect();
    let missing: Vec<&str> = required
        .iter()
        .copied()
        .filter(|key| !value.iter().any(|(name, _)| name == key))
        .collect();
    if unknown.is_empty() && missing.is_empty() {
        return Ok(());
    }
    let mut parts = Vec::new();
    if !unknown.is_empty() {
        parts.push(format!("unknown fields {}", sorted(unknown)));
    }
    if !missing.is_empty() {
        parts.push(format!("missing fields {}", sorted(missing)));
    }
    let message = format!("{}.", capitalized(&parts.join("; ")));
    Err(Problem::new(codes::INVALID_STRUCTURE, message).at(at))
}

/// Checks that the descriptive keys of `value` (`description`, `label`) hold text.
pub(crate) fn descriptive(value: &[(String, Json)], at: &Pointer) -> Result<(), Problem> {
    for (key, member) in value {
        if (key == "description" || key == "label") && member.as_str().is_none() {
            return Err(Problem::new(codes::INVALID_STRUCTURE, "Expected text.").at(&at.key(key)));
        }
    }
    Ok(())
}

fn sorted(mut names: Vec<&str>) -> String {
    names.sort_unstable();
    names.join(", ")
}

fn capitalized(text: &str) -> String {
    let mut chars = text.chars();
    chars
        .next()
        .map(|first| format!("{}{}", first.to_uppercase(), chars.as_str()))
        .unwrap_or_default()
}

/// The reference path a condition proves present: only a bare `is_present` or `is_not_empty` call on a `ref` does.
pub(crate) fn guard(condition: Option<&Json>) -> Option<Vec<String>> {
    let condition = condition?;
    let call = condition.get("call")?.as_str()?;
    if call != "is_present" && call != "is_not_empty" {
        return None;
    }
    let reference = condition.get("args")?.get("value")?;
    if reference.as_object()?.len() != 1 {
        return None;
    }
    let Json::Array(path) = reference.get("ref")? else {
        return None;
    };
    path.iter()
        .map(|part| part.as_str().map(str::to_string))
        .collect()
}

/// The bounds a parameter declaration may set.
const PARAMETER_BOUNDS: [&str; 4] = ["minimum", "exclusive_minimum", "maximum", "max_length"];

/// The type a template parameter declaration accepts, after checking the declaration's shape; whether it has a
/// default is the caller's to read.
pub(crate) fn parameter(
    declaration: &[(String, Json)],
    at: &Pointer,
) -> Result<Arc<ValueType>, Problem> {
    let allowed: Vec<&str> = [
        "type",
        "required",
        "nullable",
        "default",
        "description",
        "label",
    ]
    .into_iter()
    .chain(PARAMETER_BOUNDS)
    .collect();
    keys(declaration, &allowed, &["type"], at)?;
    descriptive(declaration, at)?;
    let get = |key: &str| {
        declaration
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value)
    };
    let named = get("type").and_then(Json::as_str);
    let Some(kind) = named.and_then(|name| types().parameter(name)) else {
        let shown = named.map_or_else(|| "that is not text".to_string(), str::to_string);
        return Err(Problem::value(format!("Unknown parameter type {shown}.")).at(&at.key("type")));
    };
    let kind = bounded(kind, declaration, at)?;
    let flag = |key: &str| match get(key) {
        None => Ok(false),
        Some(Json::Bool(flag)) => Ok(*flag),
        Some(_) => Err(Problem::value("required and nullable must be booleans.").at(&at.key(key))),
    };
    let (required, nullable) = (flag("required")?, flag("nullable")?);
    let has_default = get("default").is_some();
    if required && has_default {
        return Err(Problem::value("Required parameters cannot have defaults.").at(at));
    }
    if !required && !has_default {
        return Err(Problem::value("Optional parameters require a literal default.").at(at));
    }
    Ok(if nullable {
        Arc::new(kind.optional())
    } else {
        kind
    })
}

/// `kind` limited by the bounds a declaration sets: numbers and text take them themselves, a quantity such as a
/// length on its numeric field.
fn bounded(
    kind: &Arc<ValueType>,
    declaration: &[(String, Json)],
    at: &Pointer,
) -> Result<Arc<ValueType>, Problem> {
    let accepted: &[&str] = match kind.name {
        TypeName::String => &["max_length"],
        TypeName::Number | TypeName::Integer => &["minimum", "exclusive_minimum", "maximum"],
        name if quantity_field(name).is_some() => &["minimum", "exclusive_minimum", "maximum"],
        _ => &[],
    };
    let mut bounds = Bounds::default();
    for (key, value) in declaration
        .iter()
        .filter(|(key, _)| PARAMETER_BOUNDS.contains(&key.as_str()))
    {
        let at = at.key(key);
        if !accepted.contains(&key.as_str()) {
            let accepts = if accepted.is_empty() {
                "no bounds".to_string()
            } else {
                accepted.join(", ")
            };
            let message = format!(
                "A {} parameter cannot declare {key}; it accepts {accepts}.",
                kind.name.as_str()
            );
            return Err(Problem::value(message).at(&at));
        }
        if key == "max_length" {
            let length = match value {
                Json::Int(length) => u64::try_from(*length).ok(),
                _ => None,
            };
            bounds.max_length = Some(length.ok_or_else(|| {
                Problem::value("max_length must be a nonnegative integer.").at(&at)
            })?);
            continue;
        }
        let number = value
            .as_f64()
            .ok_or_else(|| Problem::value(format!("{key} must be a number.")).at(&at))?;
        match key.as_str() {
            "minimum" => bounds.minimum = Some(number),
            "exclusive_minimum" => bounds.exclusive_minimum = Some(number),
            _ => bounds.maximum = Some(number),
        }
    }
    if bounds.is_empty() {
        return Ok(Arc::clone(kind));
    }
    let Some(field) = quantity_field(kind.name) else {
        return Ok(Arc::new(ValueType {
            bounds,
            ..(**kind).clone()
        }));
    };
    let fields = kind
        .fields
        .iter()
        .map(|(key, child)| {
            let child = if &**key == field {
                Arc::new(ValueType {
                    bounds,
                    ..(**child).clone()
                })
            } else {
                Arc::clone(child)
            };
            (Arc::clone(key), child)
        })
        .collect();
    Ok(Arc::new(ValueType {
        fields,
        ..(**kind).clone()
    }))
}
