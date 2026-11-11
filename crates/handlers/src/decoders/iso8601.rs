//! ISO 8601 timestamps to Media Time microseconds.

use foundation::MediaTime;

const MICROS_PER_SECOND: i64 = 1_000_000;

/// Reads ISO timestamps using the Python plugins’ CPython 3.12 parsing and UTC conversion.
/// Naive values mean UTC; basic and extended forms and fractional seconds are accepted.
pub fn parse_timestamp(text: &str) -> Option<MediaTime> {
    // The plugins replace a terminal Z before calling datetime.fromisoformat.
    let normalized = text
        .strip_suffix('Z')
        .map(|prefix| format!("{prefix}+00:00"));
    let text = normalized.as_deref().unwrap_or(text);
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
                    let value = parse_clock(tail, true, false)?;
                    if *sign == b'+' { value } else { -value }
                }
                _ => return None,
            };
            (&time[..at], offset)
        }
    };
    Some(MediaTime(
        days * 86_400 * MICROS_PER_SECOND + parse_clock(clock, false, zone.is_some())? - offset,
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
        let week_end = at + 3;
        // Resolve the date/time boundary before reading a weekday, as CPython does.
        let end = if extended {
            if bytes.get(week_end) == Some(&b'-')
                && !bytes.get(week_end + 2).is_some_and(u8::is_ascii_digit)
            {
                week_end + 2
            } else {
                week_end
            }
        } else {
            let run_end = week_end
                + bytes[week_end..]
                    .iter()
                    .take_while(|b| b.is_ascii_digit())
                    .count();
            if run_end < 9 {
                run_end
            } else if run_end % 2 == 0 {
                7
            } else {
                8
            }
        };
        let day = if end == week_end {
            1
        } else {
            digits(bytes, end - 1, 1)?
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
fn parse_clock(bytes: &[u8], offset: bool, zoned: bool) -> Option<i64> {
    let hour = digits(bytes, 0, 2)?;
    let mut at = 2;
    let extended = bytes.get(at) == Some(&b':');
    let mut parts = [hour, 0, 0];
    let mut count = 1;
    for part in &mut parts[1..] {
        if at == bytes.len() || matches!(bytes.get(at), Some(b'.' | b',')) {
            break;
        }
        // CPython's clock parser permits an unfinished component immediately before a zone.
        if zoned && bytes.len() - at < 2 {
            at = bytes.len();
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
    if !offset && (parts[0] > 23 || parts[1] > 59 || parts[2] > 59) {
        return None;
    }
    let mut micros = 0;
    let fraction_start = if matches!(bytes.get(at), Some(b'.' | b',')) {
        Some(at + 1)
    } else if count == 3 && !extended && bytes.get(at).is_some_and(u8::is_ascii_digit) {
        Some(at)
    } else {
        None
    };
    if let Some(start) = fraction_start {
        let fraction = &bytes[start..];
        if (!zoned && fraction.is_empty()) || !fraction.iter().all(u8::is_ascii_digit) {
            return None;
        }
        let length = fraction.len().min(6);
        if start == at && length == 1 {
            if !zoned {
                return None;
            }
        } else {
            micros = digits(fraction, 0, length)? * 10_i64.pow(6 - length as u32);
        }
        at = bytes.len();
    }
    let seconds = parts[0] * 3600 + parts[1] * 60 + parts[2];
    if offset && seconds == 0 {
        // CPython 3.10/3.12 returns timezone.utc when the integral offset is zero.
        micros = 0;
    }
    (at == bytes.len() && (!offset || seconds < 86_400))
        .then_some(seconds * MICROS_PER_SECOND + micros)
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
    fn reference_timestamps_give_utc_microseconds() {
        // Recorded from the unchanged Python parse_timestamp -> datetime_to_us chain on
        // CPython 3.12.15. Valid-form superset of the checkout's CPython 3.10.22;
        // unlike 3.10, multiple zone designators (ZZ) are rejected.
        for (text, expected) in [
            ("2024-01-01", Some(1704067200000000)),
            ("20240101", Some(1704067200000000)),
            ("2024-W01", Some(1704067200000000)),
            ("2024W011", Some(1704067200000000)),
            ("2020-W53-7", Some(1609632000000000)),
            ("2024-W01-120000", Some(1704110400000000)),
            ("2024W01111111111", Some(1704107471110000)),
            ("2024-01-01🐍12", Some(1704110400000000)),
            ("2024-01-01T12.500000", Some(1704110400500000)),
            ("2024-01-01T12:30,5", Some(1704112200500000)),
            ("2024-01-01T12.5+02", Some(1704103200500000)),
            ("2024-01-01T120000,123456789012Z", Some(1704110400123456)),
            ("2024-01-01T12+0200", Some(1704103200000000)),
            ("2024-01-01T12-0430", Some(1704126600000000)),
            ("2024-01-01T12+02:00:30.5", Some(1704103169500000)),
            ("2024-01-01T12-02:00:30,5", Some(1704117630500000)),
            (
                "2024-01-01T00:00:00+00:00:00.500000",
                Some(1704067200000000),
            ),
            (
                "2024-01-01T00:00:00-00:00:00.500000",
                Some(1704067200000000),
            ),
            ("2024-01-01T12+01:60", Some(1704103200000000)),
            ("2024-01-01T12+00:00:60", Some(1704110340000000)),
            ("2024-01-01T12+23:59:59.999999", Some(1704024000000001)),
            ("2024-01-01T12-23:59:59.999999", Some(1704196799999999)),
            ("0001-01-01 00+0200", Some(-62135604000000000)),
            ("9999-12-31 12-00:00:00.500000", Some(253402257600000000)),
            ("2024-01-01Z", Some(1704067200000000)),
            ("2024-01-01T12:Z", Some(1704110400000000)),
            ("2024-01-01T12.Z", Some(1704110400000000)),
            ("2024-01-01T12:.Z", None),
            ("2024-01-01T24:00", None),
            ("2024-01-01T00:00:60", None),
            ("2023-W53-1", None),
            ("9999-W52-7", None),
            ("2024-W01-8", None),
            ("0000-01-01", None),
            ("2023-02-29", None),
            ("2024-01-01T12+24:00", None),
            ("2024-01-01T12-24:00", None),
            ("2024-01-01T12ZZ", None),
            ("2024-01-01T12.x", None),
            ("2024-01-01T12:3000", None),
            ("not-a-timestamp", None),
        ] {
            assert_eq!(parse_timestamp(text), expected.map(MediaTime), "{text}");
        }
    }
    #[test]
    fn decided_malformed_timestamps_are_rejected() {
        // Each JSON string is followed by its measured CPython 3.12.15 UTC value.
        for table in [
            include_str!("../../../fixtures/timestamp-rejections/owner-1.txt"),
            include_str!("../../../fixtures/timestamp-rejections/ai-1.txt"),
            include_str!("../../../fixtures/timestamp-rejections/ai-2.txt"),
            include_str!("../../../fixtures/timestamp-rejections/ai-3.txt"),
            include_str!("../../../fixtures/timestamp-rejections/ai-4.txt"),
        ] {
            for line in table.lines() {
                let (input, python) = line
                    .split_once(" # Python ")
                    .expect("recorded Python value");
                let text: String = serde_json::from_str(input).expect("JSON timestamp");
                assert_eq!(
                    parse_timestamp(&text),
                    None,
                    "{input}; Python reads {python}"
                );
            }
        }
    }
}
