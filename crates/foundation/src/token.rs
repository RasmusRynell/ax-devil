use std::fmt::Write as _;

/// The lowercase hex of `bytes`, two digits per byte.
pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().fold(String::new(), |mut text, byte| {
        // Writing to a String cannot fail.
        let _ = write!(text, "{byte:02x}");
        text
    })
}

/// `N` bytes from the operating system's randomness.
pub fn random_bytes<const N: usize>() -> [u8; N] {
    let mut bytes = [0; N];
    // Without operating-system randomness there is no sound way to make an unguessable value; stopping is safer
    // than falling back to something guessable.
    getrandom::fill(&mut bytes)
        .unwrap_or_else(|error| panic!("the operating system has no randomness: {error}"));
    bytes
}

/// A fresh unguessable token: 128 random bits as 32 hex digits.
pub fn random_token() -> String {
    hex(&random_bytes::<16>())
}
