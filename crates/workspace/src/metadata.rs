//! How the facts a resolver states about a playlist, entry or lane read in Information.
//!
//! The plugin crate keeps a resolver's metadata small when it is read; what is shown is bounded again here, so a
//! shape the byte limit allows, such as thousands of tiny keys, still gives a short list.

use std::fmt::Write;

use handler_api::Settings;
use serde_json::Value;

/// The most rows one metadata object shows, a last row counting the keys left out when there are more.
const MAX_ROWS: usize = 32;
/// The most characters a key or a value shows, its `…` included.
const MAX_CHARS: usize = 200;

/// The `(key, text)` rows of `metadata`, keys sorted. At most [`MAX_ROWS`] rows; when there are more keys than that,
/// the last row is `("…", "N more")` and counts every key it replaces, so the rows before it are [`MAX_ROWS`] - 1. A
/// key or value of more than [`MAX_CHARS`] characters ends in `…`.
pub fn metadata_fields(metadata: &Settings) -> Vec<(String, String)> {
    let mut keys: Vec<&String> = metadata.keys().collect();
    keys.sort();
    let shown = if keys.len() > MAX_ROWS {
        MAX_ROWS - 1
    } else {
        keys.len()
    };
    let more = keys.len() - shown;
    let mut rows: Vec<(String, String)> = keys
        .into_iter()
        .take(shown)
        .map(|key| {
            (
                bounded(|text| text.push_str(key)),
                bounded(|text| write(&metadata[key], text)),
            )
        })
        .collect();
    if more > 0 {
        rows.push(("…".to_owned(), format!("{more} more")));
    }
    rows
}

/// Text built by `fill`, kept to [`MAX_CHARS`] characters: it stops taking characters at the limit, so nothing
/// larger is ever allocated, and ends in `…` once it has had to.
fn bounded(fill: impl FnOnce(&mut Bounded)) -> String {
    let mut text = Bounded {
        out: String::new(),
        chars: 0,
        over: false,
    };
    fill(&mut text);
    if text.over {
        text.out.pop();
        text.out.push('…');
    }
    text.out
}

struct Bounded {
    out: String,
    chars: usize,
    /// More was offered than fits; never cleared.
    over: bool,
}

/// A float's digits as `{:.3}` writes them, passed on without the fractional zeros that end it and the point they
/// leave last. Only the point and its zeros (at most `.000`) are held back, until a nonzero digit shows they are
/// not the end, so the whole number is never built and the bound is judged on the trimmed text.
struct Number<'a> {
    text: &'a mut Bounded,
    /// The point and the zeros since it, not yet passed on.
    held: String,
    point: bool,
}

impl Write for Number<'_> {
    fn write_str(&mut self, digits: &str) -> std::fmt::Result {
        for char in digits.chars() {
            let mut one = [0; 4];
            let one = &*char.encode_utf8(&mut one);
            match (self.point, char) {
                (false, '.') => {
                    self.point = true;
                    self.held.push('.');
                }
                (false, _) => self.text.push_str(one),
                (true, '0') => self.held.push('0'),
                (true, _) => {
                    self.text.push_str(&self.held);
                    self.held.clear();
                    self.text.push_str(one);
                }
            }
        }
        Ok(())
    }
}

impl Bounded {
    fn push_str(&mut self, text: &str) {
        for char in text.chars() {
            if self.chars == MAX_CHARS {
                self.over = true;
                return;
            }
            self.out.push(char);
            self.chars += 1;
        }
    }
}

/// A value as text: yes or no, numbers with at most three decimals, a list joined by commas, `(empty)` for an empty
/// one, `{}` for an empty object. A float's digits go straight into the bounded buffer (see [`Number`]), so a huge
/// one is cut at the limit without its full text ever being built.
fn write(value: &Value, text: &mut Bounded) {
    if text.over {
        return;
    }
    match value {
        Value::Null => text.push_str("None"),
        Value::Bool(flag) => text.push_str(if *flag { "Yes" } else { "No" }),
        Value::Number(number) => match number.as_f64().filter(|_| number.is_f64()) {
            Some(float) => {
                let mut number = Number {
                    text,
                    held: String::new(),
                    point: false,
                };
                let _ = write!(number, "{float:.3}");
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
                if text.over {
                    return;
                }
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
                if text.over {
                    return;
                }
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
    fn rows_and_text_stop_exactly_at_their_limits_and_say_so() {
        let rows = |count: usize| {
            let object: serde_json::Map<String, Value> =
                (0..count).map(|n| (format!("k{n:03}"), n.into())).collect();
            metadata_fields(&object)
        };
        let full = rows(MAX_ROWS);
        assert_eq!(full.len(), MAX_ROWS);
        assert_eq!(
            full.last().unwrap().0,
            "k031",
            "all 32 keys fit without a summary"
        );
        let over = rows(MAX_ROWS + 1);
        assert_eq!(over.len(), MAX_ROWS, "31 keys and the summary");
        assert_eq!(over.last().unwrap(), &("…".to_owned(), "2 more".to_owned()));
        assert_eq!(rows(MAX_ROWS + 3).last().unwrap().1, "4 more");

        let value = |chars: usize| {
            let object = json!({ "k": "x".repeat(chars), "y".repeat(chars): 1 });
            metadata_fields(object.as_object().unwrap())
        };
        let exact = value(MAX_CHARS);
        assert_eq!(exact[0].1.chars().count(), MAX_CHARS);
        assert!(!exact[0].1.ends_with('…') && !exact[1].0.ends_with('…'));
        let long = value(MAX_CHARS + 1);
        assert_eq!(long[0].1.chars().count(), MAX_CHARS);
        assert!(long[0].1.ends_with('…') && long[1].0.ends_with('…'));
        // Fractional zeros that are trimmed do not count against the limit: 197 characters, `, ` and `1` make 200.
        let list = json!({ "k": [Value::from("x".repeat(197)), Value::from(1.0)] });
        let shown = &metadata_fields(list.as_object().unwrap())[0].1;
        assert_eq!(shown, &format!("{}, 1", "x".repeat(197)));
        assert_eq!(shown.chars().count(), MAX_CHARS);
        // A float past the limit is written only as far as the limit, in fixed point.
        let huge = json!({ "k": 1e308 });
        let shown = &metadata_fields(huge.as_object().unwrap())[0].1;
        assert_eq!(shown.chars().count(), MAX_CHARS);
        assert!(shown.starts_with("1000000000") && shown.ends_with('…'));
        // A list far past the limit stops being read at it.
        let list = json!({ "k": vec![1e308; 100_000] });
        let shown = &metadata_fields(list.as_object().unwrap())[0].1;
        assert_eq!(shown.chars().count(), MAX_CHARS);
        assert!(shown.ends_with('…'));
    }
}
