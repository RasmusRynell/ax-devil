//! Text from a camera, made safe to put in a log line.

/// `text` with every control character (a newline, a carriage return, ESC) written as an escape such as `\n`, so a
/// value the camera supplied cannot forge a log line of its own.
pub(crate) fn escaped(text: &str) -> String {
    text.chars()
        .map(|c| {
            if c.is_control() {
                c.escape_default().to_string()
            } else {
                c.to_string()
            }
        })
        .collect()
}
