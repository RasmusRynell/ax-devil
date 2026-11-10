//! ISO 8601 timestamps to Media Time microseconds.

use foundation::MediaTime;

const MICROS_PER_SECOND: i64 = 1_000_000;

/// Reads Python-compatible ISO calendar/week dates and times as UTC microseconds.
/// Naive values mean UTC; basic and extended forms and fractional seconds are accepted.
pub fn parse_timestamp(text: &str) -> Option<MediaTime> {
    let (days, date_len) = parse_date(text.as_bytes())?;
    let rest = text.get(date_len..)?;
    if rest.is_empty() {
        return Some(MediaTime(days * 86_400 * MICROS_PER_SECOND));
    }
    // Python permits any one Unicode character between date and time.
    let time = rest.get(rest.chars().next()?.len_utf8()..)?.as_bytes();
    let zone = time.iter().position(|b| matches!(b, b'Z' | b'+' | b'-'));
    let (clock, offset) = match zone {
        None => (time, 0),
        Some(at) => {
            let suffix = &time[at..];
            let offset = match suffix {
                [b'Z'] => 0,
                [sign @ (b'+' | b'-'), tail @ ..] => {
                    let value = parse_clock(tail)?;
                    if *sign == b'+' { value } else { -value }
                }
                _ => return None,
            };
            (&time[..at], offset)
        }
    };
    Some(MediaTime(
        days * 86_400 * MICROS_PER_SECOND + parse_clock(clock)? - offset,
    ))
}

/// Returns epoch days and the byte length of a calendar or ISO week date.
fn parse_date(bytes: &[u8]) -> Option<(i64, usize)> {
    let year = digits(bytes, 0, 4)?;
    if year == 0 {
        return None;
    }
    let extended = bytes.get(4) == Some(&b'-');
    let at = if extended { 5 } else { 4 };
    if bytes.get(at) == Some(&b'W') {
        let week = digits(bytes, at + 1, 2)?;
        let mut end = at + 3;
        let day = if extended && bytes.get(end) == Some(&b'-') {
            end += 2;
            digits(bytes, end - 1, 1)?
        } else if !extended && bytes.get(end).is_some_and(u8::is_ascii_digit) {
            end += 1;
            digits(bytes, end - 1, 1)?
        } else {
            1
        };
        let monday = |year| {
            let jan4 = days_from_civil(year, 1, 4);
            jan4 - (jan4 + 3).rem_euclid(7)
        };
        if !(1..=7).contains(&day) || week < 1 || week > (monday(year + 1) - monday(year)) / 7 {
            return None;
        }
        let days = monday(year) + (week - 1) * 7 + day - 1;
        if days >= days_from_civil(10000, 1, 1) {
            return None;
        }
        return Some((days, end));
    }
    let month = digits(bytes, at, 2)?;
    let day_at = at + if extended { 3 } else { 2 };
    if extended && bytes.get(at + 2) != Some(&b'-') {
        return None;
    }
    let day = digits(bytes, day_at, 2)?;
    if !(1..=12).contains(&month) || day < 1 || day > days_in_month(year, month) {
        return None;
    }
    Some((days_from_civil(year, month, day), day_at + 2))
}

/// A basic or extended clock/offset, with optional minutes, seconds and fractional seconds.
fn parse_clock(bytes: &[u8]) -> Option<i64> {
    let hour = digits(bytes, 0, 2)?;
    let mut at = 2;
    let extended = bytes.get(at) == Some(&b':');
    let mut parts = [hour, 0, 0];
    let mut count = 1;
    for part in &mut parts[1..] {
        if at == bytes.len() || matches!(bytes.get(at), Some(b'.' | b',')) {
            break;
        }
        if extended {
            if bytes.get(at) != Some(&b':') {
                return None;
            }
            at += 1;
        }
        *part = digits(bytes, at, 2)?;
        at += 2;
        count += 1;
    }
    if parts[0] > 23 || parts[1] > 59 || parts[2] > 59 {
        return None;
    }
    let mut micros = 0;
    if matches!(bytes.get(at), Some(b'.' | b',')) && count == 3 {
        let fraction = &bytes[at + 1..];
        if fraction.is_empty() || !fraction.iter().all(u8::is_ascii_digit) {
            return None;
        }
        let length = fraction.len().min(6);
        micros = digits(fraction, 0, length)? * 10_i64.pow(6 - length as u32);
        at = bytes.len();
    }
    (at == bytes.len())
        .then_some((parts[0] * 3600 + parts[1] * 60 + parts[2]) * MICROS_PER_SECOND + micros)
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
            ("2024-01-01", JAN_1_2024),
            ("20240101", JAN_1_2024),
            ("2024-W01", JAN_1_2024),
            ("2024-W01-1", JAN_1_2024),
            ("2024W011", JAN_1_2024),
            ("2024W01", JAN_1_2024),
            ("2020-W53-7", 1_609_632_000_000_000),
            ("2024-01-01T00", JAN_1_2024),
            ("2024-01-01T00:00", JAN_1_2024),
            ("20240101T000000", JAN_1_2024),
            ("20240101T0000", JAN_1_2024),
            ("2023-12-31T19:30:00-0430", JAN_1_2024),
            ("2024-01-01🐍00:00:00", JAN_1_2024),
            ("2024-01-01T02:00:00+0200", JAN_1_2024),
            ("2024-01-01T02:00:00+02", JAN_1_2024),
            ("2024-01-01T02:00:30.5+020030.5", JAN_1_2024),
            ("2024-01-01T02:00:30.5+02:00:30.5", JAN_1_2024),
            ("2024-01-01T00:00:00,123456789012Z", JAN_1_2024 + 123_456),
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
            "0000-01-01",
            "2023-W53-1",
            "2024-W01-8",
            "202401-01",
            "2024-01-01T00:0000",
            "2023-02-29T00:00:00Z",
            "2024-13-01T00:00:00Z",
            "2024-01-01T24:00:00Z",
            "2024-01-01T00:00:60Z",
            "2024-01-01T00:00:00.Z",
            "2024-01-01T00:00:00 Z",
            "2024-01-01T00:00:00Zjunk",
            "2024-01-01T00:00:00+24:00",
        ] {
            assert_eq!(parse_timestamp(text), None, "{text}");
        }
    }
}
