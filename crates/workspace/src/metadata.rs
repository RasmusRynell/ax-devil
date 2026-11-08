//! How the facts a resolver states about a playlist, entry or lane read in Information.

use handler_api::Settings;
use serde_json::Value;

/// The `(key, text)` rows of `metadata`, keys sorted.
pub fn metadata_fields(metadata: &Settings) -> Vec<(String, String)> {
    let mut keys: Vec<&String> = metadata.keys().collect();
    keys.sort();
    keys.into_iter()
        .map(|key| (key.clone(), text(&metadata[key])))
        .collect()
}

/// A value as text: yes or no, numbers with at most three decimals, a list joined by commas, `(empty)` for an empty
/// one, `{}` for an empty object.
fn text(value: &Value) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Bool(flag) => if *flag { "Yes" } else { "No" }.to_owned(),
        Value::Number(number) => match number.as_f64().filter(|_| number.is_f64()) {
            Some(float) => format!("{float:.3}")
                .trim_end_matches('0')
                .trim_end_matches('.')
                .to_owned(),
            None => number.to_string(),
        },
        Value::String(text) => text.clone(),
        Value::Array(items) if items.is_empty() => "(empty)".to_owned(),
        Value::Array(items) => items.iter().map(text).collect::<Vec<_>>().join(", "),
        Value::Object(fields) if fields.is_empty() => "{}".to_owned(),
        Value::Object(fields) => metadata_fields(fields)
            .into_iter()
            .map(|(key, value)| format!("{key}: {value}"))
            .collect::<Vec<_>>()
            .join(", "),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn values_read_as_in_the_python_app_with_keys_sorted() {
        let metadata = json!({
            "b_ok": true, "a_done": false, "c_rate": 0.12345, "d_whole": 3.0, "e_count": 7,
            "f_tags": ["x", 1.5, true], "g_none": [], "h_map": {}, "i_text": "plain",
        });
        let fields = metadata_fields(metadata.as_object().unwrap());
        let shown: Vec<(&str, &str)> = fields
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect();
        assert_eq!(
            shown,
            [
                ("a_done", "No"),
                ("b_ok", "Yes"),
                ("c_rate", "0.123"),
                ("d_whole", "3"),
                ("e_count", "7"),
                ("f_tags", "x, 1.5, Yes"),
                ("g_none", "(empty)"),
                ("h_map", "{}"),
                ("i_text", "plain"),
            ]
        );
    }
}
