//! `$NAME` references: a setting that accepts them stores the reference as written and expands it where it is used,
//! so the value is never copied into the file.

use serde_json::Value;

/// The variable name if the whole `value` is a reference such as `$ADDRESS`.
pub fn reference(value: &str) -> Option<&str> {
    let name = value.strip_prefix('$')?;
    let mut chars = name.chars();
    let first = chars.next()?;
    let valid = (first.is_ascii_alphabetic() || first == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_');
    valid.then_some(name)
}

/// `value` with a reference replaced by what `lookup` gives for its name, or by an empty text when it is unset.
/// Anything that is not a reference is returned unchanged.
pub fn resolve(value: &Value, lookup: &dyn Fn(&str) -> Option<String>) -> Value {
    match value.as_str().and_then(reference) {
        Some(name) => Value::String(lookup(name).unwrap_or_default()),
        None => value.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_a_whole_value_that_is_a_name_is_a_reference() {
        for text in ["$HOST", "$_a1", "$a"] {
            assert!(reference(text).is_some(), "{text}");
        }
        for text in ["", "$", "HOST", "$1A", "$A-B", "a $A", "$A ", "$$A", "$A.b"] {
            assert_eq!(reference(text), None, "{text}");
        }
        let lookup = |name: &str| (name == "HOST").then(|| "cam".to_owned());
        assert_eq!(resolve(&json!("$HOST"), &lookup), "cam");
        assert_eq!(
            resolve(&json!("$MISSING"), &lookup),
            "",
            "an unset variable is empty"
        );
        assert_eq!(resolve(&json!("literal"), &lookup), "literal");
        assert_eq!(resolve(&json!(5), &lookup), 5);
    }
}
