//! How resolver metadata reads in Information. The plugin ingestion budget bounds the input.

use handler_api::Settings;
use serde_json::Value;

/// All `(key, text)` rows of accepted metadata, sorted by key without truncating keys or values.
pub fn metadata_fields(metadata: &Settings) -> Vec<(String, String)> {
    let mut keys: Vec<&String> = metadata.keys().collect();
    keys.sort();
    keys.into_iter()
        .map(|key| {
            let mut text = String::new();
            write(&metadata[key], &mut text);
            (key.clone(), text)
        })
        .collect()
}

fn write(value: &Value, text: &mut String) {
    match value {
        Value::Null => text.push_str("None"),
        Value::Bool(flag) => text.push_str(if *flag { "Yes" } else { "No" }),
        Value::Number(number) => match number.as_f64().filter(|_| number.is_f64()) {
            Some(float) => {
                let digits = format!("{float:.3}");
                text.push_str(digits.trim_end_matches('0').trim_end_matches('.'));
            }
            None => text.push_str(&number.to_string()),
        },
        Value::String(string) => text.push_str(string),
        Value::Array(items) if items.is_empty() => text.push_str("(empty)"),
        Value::Array(items) => {
            for (at, item) in items.iter().enumerate() {
                if at > 0 {
                    text.push_str(", ");
                }
                write(item, text);
            }
        }
        Value::Object(fields) if fields.is_empty() => text.push_str("{}"),
        Value::Object(fields) => {
            let mut keys: Vec<&String> = fields.keys().collect();
            keys.sort();
            for (at, key) in keys.into_iter().enumerate() {
                if at > 0 {
                    text.push_str(", ");
                }
                text.push_str(key);
                text.push_str(": ");
                write(&fields[key], text);
            }
        }
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
            "j_big": 1e15,
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
                ("j_big", "1000000000000000"),
            ]
        );
    }

    #[test]
    fn all_accepted_metadata_remains_readable() {
        let long = "é".repeat(300);
        let metadata: Settings = (0..33)
            .map(|n| (format!("key{n:02}"), Value::from(long.clone())))
            .collect();
        let fields = metadata_fields(&metadata);
        assert_eq!(fields.len(), 33);
        assert_eq!(fields.last().unwrap(), &("key32".into(), long));
    }
}
