//! The value of an inspected field, and the leaves the scalars of an entity become.

use foundation::MediaTime;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

/// The value of a field: a leaf of the entity's data. On the wire an integer is its decimal digits in a string, so
/// that it stays exact in a JavaScript number-only world, and `Int(3)` differs from `Float(3.0)`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(tag = "kind", content = "value", rename_all = "lowercase")]
#[ts(rename = "LeafWire")]
pub enum Leaf {
    /// No value.
    Null,
    /// A truth value.
    Bool(bool),
    /// A whole number, exactly.
    Int(
        #[serde(with = "int_text")]
        #[ts(type = "string")]
        i64,
    ),
    /// A finite number.
    Float(f64),
    /// Text, at most [`MAX_TEXT_CHARS`](super::MAX_TEXT_CHARS) characters.
    Text(String),
}

/// An integer as the string of its decimal digits.
mod int_text {
    use serde::{Deserialize, Deserializer, Serializer, de::Error};

    pub(super) fn serialize<S: Serializer>(whole: &i64, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_str(whole)
    }

    pub(super) fn deserialize<'de, D: Deserializer<'de>>(deserializer: D) -> Result<i64, D::Error> {
        String::deserialize(deserializer)?
            .parse()
            .map_err(|_| D::Error::custom("an integer in decimal digits"))
    }
}

/// A number, or its name when the source's float is not finite.
pub(super) fn number_leaf(number: f64) -> Leaf {
    if number.is_finite() {
        Leaf::Float(number)
    } else {
        Leaf::Text(number.to_string())
    }
}

/// A Scene datetime as Python's `isoformat` writes it in UTC, `2024-05-01T12:00:00.250000+00:00` (the fraction only when
/// not zero); the number of microseconds itself when the year is outside 1 to 9999.
pub(super) fn datetime_leaf(time: MediaTime) -> Leaf {
    const MICROS_PER_DAY: i64 = 86_400_000_000;
    let (days, micros) = (
        time.0.div_euclid(MICROS_PER_DAY),
        time.0.rem_euclid(MICROS_PER_DAY),
    );
    // Days since 1970-01-01 to a civil date (Howard Hinnant's algorithm).
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_index = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_index + 2) / 5 + 1;
    let month = if month_index < 10 {
        month_index + 3
    } else {
        month_index - 9
    };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    if !(1..=9999).contains(&year) {
        return Leaf::Int(time.0);
    }
    let seconds = micros / 1_000_000;
    let fraction = match micros % 1_000_000 {
        0 => String::new(),
        micros => format!(".{micros:06}"),
    };
    Leaf::Text(format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}{fraction}+00:00",
        seconds / 3600,
        seconds / 60 % 60,
        seconds % 60
    ))
}
