//! Wording of bounds in messages.

use crate::ir::ValueType;

/// Describes the values `kind`'s bounds allow, such as `at least 0 and at most 255`.
pub(crate) fn allowed_values(kind: &ValueType) -> String {
    let bounds = &kind.bounds;
    let mut parts = Vec::new();
    if let Some(minimum) = bounds.minimum {
        parts.push(format!("at least {}", general(minimum)));
    }
    if let Some(minimum) = bounds.exclusive_minimum {
        parts.push(format!("greater than {}", general(minimum)));
    }
    if let Some(maximum) = bounds.maximum {
        parts.push(format!("at most {}", general(maximum)));
    }
    if let Some(length) = bounds.max_length {
        parts.push(format!("at most {length} characters long"));
    }
    match (bounds.min_items, bounds.max_items) {
        (Some(minimum), Some(maximum)) if minimum == maximum => {
            parts.push(format!("a list of exactly {minimum} items"))
        }
        (Some(minimum), Some(maximum)) => {
            parts.push(format!("a list of {minimum} to {maximum} items"))
        }
        (Some(minimum), None) => parts.push(format!("a list of at least {minimum} items")),
        (None, Some(maximum)) => parts.push(format!("a list of at most {maximum} items")),
        (None, None) => {}
    }
    parts.join(" and ")
}

/// A number with six significant digits and no trailing zeros, in exponent form when very large or small.
fn general(number: f64) -> String {
    if number == 0.0 || !number.is_finite() {
        return format!("{number}");
    }
    let exponent = number.abs().log10().floor() as i32;
    if (-4..6).contains(&exponent) {
        let decimals = usize::try_from(5 - exponent).unwrap_or(0);
        trim_zeros(&format!("{number:.decimals$}"))
    } else {
        let formatted = format!("{number:.5e}");
        let (mantissa, power) = formatted.split_once('e').unwrap_or((&formatted, "0"));
        let power: i32 = power.parse().unwrap_or(0);
        let sign = if power < 0 { '-' } else { '+' };
        format!("{}e{sign}{:02}", trim_zeros(mantissa), power.abs())
    }
}

fn trim_zeros(text: &str) -> String {
    if text.contains('.') {
        text.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        text.to_string()
    }
}
