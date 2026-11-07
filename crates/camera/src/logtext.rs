//! Text from a camera, made safe to put in a log line or a terminal.

/// `text` with everything that is not plain printable text written as an escape such as `\n` or `\u{2028}`, so a value
/// the camera supplied cannot forge a log line, move the cursor or reorder what is shown. Printable text of any
/// script passes. Not printable: control characters (newline, carriage return, ESC, NEL), the zero-width, line and
/// paragraph separators and the bidirectional marks and overrides (U+200B-U+200F, U+2028-U+202E, U+2060-U+2069),
/// and the byte order mark.
pub(crate) fn escaped(text: &str) -> String {
    text.chars()
        .map(|c| match c {
            c if c.is_control() => c.escape_default().to_string(),
            '\u{200b}'..='\u{200f}'
            | '\u{2028}'..='\u{202e}'
            | '\u{2060}'..='\u{2069}'
            | '\u{feff}' => c.escape_unicode().to_string(),
            c => c.to_string(),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::escaped;

    #[test]
    fn only_printable_text_passes() {
        assert_eq!(
            escaped("a\r\n\u{1b}[0m\u{85}\u{2028}\u{2029}\u{202e}b"),
            "a\\r\\n\\u{1b}[0m\\u{85}\\u{2028}\\u{2029}\\u{202e}b"
        );
        assert_eq!(escaped("ä 日本#1"), "ä 日本#1");
    }
}
