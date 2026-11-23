//! `$NAME` references: a setting that accepts them stores the reference as written and expands it where it is used
//! (see `expand.rs`), so the value is never copied into the file.

/// The variable name if the whole `value` is a reference such as `$ADDRESS`.
pub fn reference(value: &str) -> Option<&str> {
    let name = value.strip_prefix('$')?;
    let mut chars = name.chars();
    let first = chars.next()?;
    let valid = (first.is_ascii_alphabetic() || first == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_');
    valid.then_some(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_whole_value_that_is_a_name_is_a_reference() {
        for text in ["$HOST", "$_a1", "$a"] {
            assert!(reference(text).is_some(), "{text}");
        }
        for text in ["", "$", "HOST", "$1A", "$A-B", "a $A", "$A ", "$$A", "$A.b"] {
            assert_eq!(reference(text), None, "{text}");
        }
    }
}
