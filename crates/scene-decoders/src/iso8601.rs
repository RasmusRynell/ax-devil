//! ISO 8601 timestamps to Media Time microseconds.

use foundation::MediaTime;

const MICROS_PER_SECOND: i64 = 1_000_000;

/// Reads `YYYY-MM-DDTHH:MM:SS[.fraction][Z|+HH:MM|-HH:MM]` to microseconds since the Unix epoch, UTC. A space may replace the `T`, a
/// missing zone means UTC, and a fraction of 1 to 9 digits is cut to microseconds.
pub fn parse_timestamp(text: &str) -> Option<MediaTime> {
    let bytes = text.as_bytes();
    if bytes.get(4) != Some(&b'-')
        || bytes.get(7) != Some(&b'-')
        || !matches!(bytes.get(10), Some(b'T' | b' '))
        || bytes.get(13) != Some(&b':')
        || bytes.get(16) != Some(&b':')
    {
        return None;
    }
    let year = digits(bytes, 0, 4)?;
    let month = digits(bytes, 5, 2)?;
    let day = digits(bytes, 8, 2)?;
    let (hour, minute, second) = (
        digits(bytes, 11, 2)?,
        digits(bytes, 14, 2)?,
        digits(bytes, 17, 2)?,
    );
    if !(1..=12).contains(&month)
        || day < 1
        || day > days_in_month(year, month)
        || hour > 23
        || minute > 59
        || second > 59
    {
        return None;
    }

    let mut rest = &bytes[19..];
    let mut micros = 0;
    if let Some(fraction) = rest.strip_prefix(b".") {
        let length = fraction
            .iter()
            .take_while(|byte| byte.is_ascii_digit())
            .count();
        if !(1..=9).contains(&length) {
            return None;
        }
        micros = digits(&fraction[..length.min(6)], 0, length.min(6))?
            * 10_i64.pow(6 - length.min(6) as u32);
        rest = &fraction[length..];
    }
    let offset_seconds = match rest {
        [] | [b'Z'] => 0,
        [sign @ (b'+' | b'-'), h1, h2, b':', m1, m2] => {
            let (hours, minutes) = (digits(&[*h1, *h2], 0, 2)?, digits(&[*m1, *m2], 0, 2)?);
            if hours > 23 || minutes > 59 {
                return None;
            }
            let seconds = hours * 3600 + minutes * 60;
            if *sign == b'+' { seconds } else { -seconds }
        }
        _ => return None,
    };

    let local_seconds =
        days_from_civil(year, month, day) * 86_400 + hour * 3600 + minute * 60 + second;
    Some(MediaTime(
        (local_seconds - offset_seconds) * MICROS_PER_SECOND + micros,
    ))
}

/// The decimal number written by `count` ASCII digits at `at`.
fn digits(bytes: &[u8], at: usize, count: usize) -> Option<i64> {
    bytes
        .get(at..at + count)?
        .iter()
        .try_fold(0_i64, |value, byte| {
            byte.is_ascii_digit()
                .then(|| value * 10 + i64::from(byte - b'0'))
        })
}

fn days_in_month(year: i64, month: i64) -> i64 {
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    match month {
        2 if leap => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// Days since 1970-01-01 of a proleptic Gregorian date.
fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let year_of_era = year - era * 400;
    let day_of_year = (153 * (month + if month > 2 { -3 } else { 9 }) + 2) / 5 + day - 1;
    let day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    era * 146_097 + day_of_era - 719_468
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepted_forms_give_utc_microseconds() {
        const JAN_1_2024: i64 = 1_704_067_200_000_000;
        for (text, expected) in [
            ("2024-01-01T00:00:00Z", JAN_1_2024),
            ("2024-01-01T00:00:00", JAN_1_2024),
            ("2024-01-01 00:00:00", JAN_1_2024),
            ("2024-01-01T02:00:00+02:00", JAN_1_2024),
            ("2023-12-31T19:30:00-04:30", JAN_1_2024),
            ("2024-01-01T00:00:00.5Z", JAN_1_2024 + 500_000),
            ("2024-01-01T00:00:00.123456789Z", JAN_1_2024 + 123_456),
            ("2025-03-11T08:11:54.008178Z", 1_741_680_714_008_178),
            ("1969-12-31T23:59:59Z", -1_000_000),
            ("2024-02-29T00:00:00Z", JAN_1_2024 + 59 * 86_400_000_000),
        ] {
            assert_eq!(parse_timestamp(text), Some(MediaTime(expected)), "{text}");
        }
    }

    #[test]
    fn other_forms_are_rejected() {
        for text in [
            "",
            "not-a-timestamp",
            "2024-01-01",
            "2024-01-01T00:00",
            "2023-02-29T00:00:00Z",
            "2024-13-01T00:00:00Z",
            "2024-01-01T24:00:00Z",
            "2024-01-01T00:00:60Z",
            "2024-01-01T00:00:00.Z",
            "2024-01-01T00:00:00.1234567890Z",
            "2024-01-01T00:00:00+0200",
            "2024-01-01T00:00:00 Z",
            "2024-01-01T00:00:00Zjunk",
            "2024-01-01T00:00:00+24:00",
        ] {
            assert_eq!(parse_timestamp(text), None, "{text}");
        }
    }
}
