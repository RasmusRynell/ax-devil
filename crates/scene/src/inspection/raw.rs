//! Whole Raw section JSON, emitted incrementally with Python's default ASCII escaping and indentation.

use super::{Entity, FieldChunk, Leaf, MAX_COPY_BYTES, datetime_leaf, pages::CHUNK_BYTES};

/// Progress through one explicit Raw section copy; no complete JSON string is retained.
#[derive(Default)]
pub struct RawCopy {
    segment: usize,
    input: usize,
    output: usize,
    total: usize,
}

enum Segment<'a> {
    Literal(String),
    Escaped(&'a str),
}

fn escaped_len(character: char) -> usize {
    match character {
        '"' | '\\' | '\n' | '\r' | '\t' | '\u{8}' | '\u{c}' => 2,
        c if (' '..='~').contains(&c) => 1,
        c if c as u32 <= 0xffff => 6,
        _ => 12,
    }
}

fn escaped(character: char) -> String {
    match character {
        '"' => "\\\"".into(),
        '\\' => "\\\\".into(),
        '\n' => "\\n".into(),
        '\r' => "\\r".into(),
        '\t' => "\\t".into(),
        '\u{8}' => "\\b".into(),
        '\u{c}' => "\\f".into(),
        c if (' '..='~').contains(&c) => c.to_string(),
        c => c
            .encode_utf16(&mut [0; 2])
            .iter()
            .map(|unit| format!("\\u{unit:04x}"))
            .collect(),
    }
}

fn segments(entity: &Entity) -> Vec<Segment<'_>> {
    let mut entries = Vec::new();
    if let Some(reason) = entity.end_reason.as_deref().filter(|r| !r.is_empty()) {
        entries.push((
            "end_reason",
            vec![
                Segment::Literal("\"".into()),
                Segment::Escaped(reason),
                Segment::Literal("\"".into()),
            ],
        ));
    }
    if let Some(observation) = entity.latest_observation() {
        if let Some(time) = observation.time.timestamp() {
            let value = match datetime_leaf(time) {
                Leaf::Int(value) => value.to_string(),
                Leaf::Text(value) => format!("\"{value}\""),
                _ => unreachable!("datetime_leaf returns an integer or an ISO timestamp"),
            };
            entries.push(("timestamp", vec![Segment::Literal(value)]));
        }
        if let Some(frame) = observation.time.frame() {
            entries.push(("frame_number", vec![Segment::Literal(frame.to_string())]));
        }
    }
    if entries.is_empty() {
        return vec![Segment::Literal("{}".into())];
    }
    let mut result = vec![Segment::Literal("{\n".into())];
    for (index, (name, value)) in entries.into_iter().enumerate() {
        result.push(Segment::Literal(format!(
            "{}  \"{name}\": ",
            if index == 0 { "" } else { ",\n" }
        )));
        result.extend(value);
    }
    result.push(Segment::Literal("\n}".into()));
    result
}

/// Reads the next at-most-64-KiB piece of the complete Raw JSON; refuses a result above the shared copy ceiling.
pub fn raw_chunk(
    entity: &Entity,
    cursor: &mut RawCopy,
    offset: usize,
    cancel: &foundation::CancelToken,
) -> Result<FieldChunk, String> {
    let check = || {
        cancel
            .check()
            .map_err(|_| "Inspection cancelled".to_owned())
    };
    check()?;
    if offset != cursor.output {
        return Err("The Raw copy offset is invalid".into());
    }
    let segments = segments(entity);
    if offset == 0 {
        cursor.total = 0;
        for segment in &segments {
            cursor.total += match segment {
                Segment::Literal(text) => text.len(),
                Segment::Escaped(text) => {
                    let mut bytes = 0;
                    for character in text.chars() {
                        check()?;
                        bytes += escaped_len(character);
                    }
                    bytes
                }
            };
        }
        if cursor.total > MAX_COPY_BYTES {
            return Err(format!(
                "Raw group is {} bytes; copying supports at most {MAX_COPY_BYTES} bytes",
                cursor.total
            ));
        }
    }
    let mut text = String::with_capacity(CHUNK_BYTES);
    while let Some(segment) = segments.get(cursor.segment) {
        check()?;
        let (part, next) = match segment {
            Segment::Literal(value) => {
                let end = (cursor.input + (CHUNK_BYTES) - text.len()).min(value.len());
                (value[cursor.input..end].to_owned(), end)
            }
            Segment::Escaped(value) => {
                let Some(c) = value[cursor.input..].chars().next() else {
                    cursor.segment += 1;
                    cursor.input = 0;
                    continue;
                };
                (escaped(c), cursor.input + c.len_utf8())
            }
        };
        if text.len() + part.len() > CHUNK_BYTES {
            break;
        }
        text.push_str(&part);
        cursor.input = next;
        if let Segment::Literal(value) = segment
            && cursor.input == value.len()
        {
            cursor.segment += 1;
            cursor.input = 0;
        }
        if text.len() == CHUNK_BYTES {
            break;
        }
    }
    cursor.output += text.len();
    Ok(FieldChunk {
        text,
        total: cursor.total,
        next: (cursor.output < cursor.total).then_some(cursor.output),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{BoundingBox, EntityId, Geometry, Observation, SceneTime};

    #[test]
    fn the_whole_raw_group_matches_python_even_when_one_value_spans_pages() {
        let mut entity = Entity::new(EntityId::from(7_i64));
        entity.end_reason = Some(format!("{}é😀\n\"", "x".repeat(9 << 20)).into());
        entity.push_observation(Observation::new(
            SceneTime::Frame(17),
            Geometry::Box(BoundingBox::new(0., 0., 1., 1.).unwrap()),
        ));
        let copied = copy(&entity);
        let fixtures: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../fixtures/inspection/python-copy.json"
        ))
        .unwrap();
        for case in fixtures.as_array().unwrap() {
            let scene = crate::serial::scene_from_json_slice(
                serde_json::to_string(&case["scene"]).unwrap().as_bytes(),
            )
            .unwrap();
            assert_eq!(
                copy(scene.entity("7").unwrap()),
                case["raw"].as_str().unwrap(),
                "{}",
                case["name"]
            );
        }
        // Python: json.dumps({"end_reason": "x" * (9 << 20) + 'é😀\n"', "frame_number": 17}, indent=2).
        use sha2::{Digest, Sha256};
        let digest: String = Sha256::digest(copied.as_bytes())
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        assert_eq!(
            digest,
            "85bb3771c16237eb34e21441eb5a0c7ba323a3de056d1bc4b6e122dff06bece3"
        );
    }
    fn copy(entity: &Entity) -> String {
        let mut cursor = RawCopy::default();
        let mut offset = 0;
        let mut copied = String::new();
        loop {
            let chunk =
                raw_chunk(entity, &mut cursor, offset, &foundation::CancelToken::new()).unwrap();
            assert!(chunk.text.len() <= 65536);
            copied.push_str(&chunk.text);
            if let Some(next) = chunk.next {
                offset = next;
            } else {
                break;
            }
        }
        copied
    }
}
