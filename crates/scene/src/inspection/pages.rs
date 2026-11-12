//! Bounded windows and text reads over the same deterministic field walk.

use super::{Entity, InspectionField, Leaf, Marks, Place, Sink, walk_entity};

/// Whole-value copy ceiling: the largest cached Scene record is 256 MiB. Sources without a text limit use this ceiling too.
pub const MAX_COPY_BYTES: usize = 256 << 20;
const PAGE_FIELDS: usize = super::MAX_FIELDS;
const PAGE_BYTES: usize = super::MAX_INSPECTION_BYTES;
const TEXT_BYTES: usize = 16 << 10;
pub(super) const CHUNK_BYTES: usize = 64 << 10;

/// A field and the position and full byte count of a text abbreviated for display.
pub struct PageField {
    /// Digest of the full segmented path when its display is abbreviated; stable across samples and ordering.
    pub identity: Option<String>,
    /// Bounded structural address in the held entity, independent of its display path.
    pub address: Vec<usize>,
    /// The displayed field.
    pub field: InspectionField,
    /// The ordinal and full UTF-8 size, only when the text is abbreviated.
    pub cut: Option<(usize, usize)>,
    /// Ordinal and dotted UTF-8 size when a path exceeds a page.
    pub path_cut: Option<(usize, usize)>,
}

/// One bounded window of fields; prior windows need not be retained.
pub struct FieldPage {
    /// Structural address of the next field.
    pub next_address: Option<Vec<usize>>,
    /// Fields in stable traversal order.
    pub fields: Vec<PageField>,
    /// First ordinal of the next page, if there is one.
    pub next: Option<usize>,
}

/// A UTF-8-aligned piece of one text value.
pub struct FieldChunk {
    /// At most 64 KiB of UTF-8.
    pub text: String,
    /// Full UTF-8 byte count.
    pub total: usize,
    /// Next UTF-8 offset, absent when complete.
    pub next: Option<usize>,
}

fn prefix(text: &str, bytes: usize) -> &str {
    let mut end = bytes.min(text.len());
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

struct Page<'a> {
    work: usize,
    digest: sha2::Sha256,
    order: Option<&'a super::Traversal>,
    resume: Vec<usize>,
    address: Vec<usize>,
    start: usize,
    ordinal: usize,
    bytes: usize,
    result: FieldPage,
}

impl Page<'_> {
    fn pause(&mut self, address: &[usize]) {
        self.result.next = Some(self.ordinal);
        self.result.next_address = Some(address.to_vec());
    }
}

impl Sink for Page<'_> {
    fn wants_fingerprint(&self) -> bool {
        true
    }
    fn fingerprint(&mut self, digest: &sha2::Sha256) {
        self.digest = digest.clone();
    }
    fn enter(&mut self, address: &[usize]) -> bool {
        if self.work == 0 {
            self.pause(address);
            return false;
        }
        self.work -= 1;
        true
    }
    fn colors(&mut self, address: &[usize], items: &[crate::Value]) -> Option<bool> {
        // A resume below an element's children proves this list was not a collapsed colour list.
        if self.resume.starts_with(address) && self.resume.len() > address.len() + 1 {
            return Some(false);
        }
        let result = if let Some(order) = self.order {
            order.colors(address, items, &mut self.work)
        } else {
            Some(
                !items.is_empty()
                    && items
                        .iter()
                        .all(|item| crate::ColorEntry::read_exact(item).is_some()),
            )
        };
        if result.is_none() {
            self.pause(address);
        }
        result
    }

    fn ranked(&self) -> Option<&[u32]> {
        self.order.map(super::Traversal::order)
    }
    fn resume(&self) -> &[usize] {
        &self.resume
    }
    fn address(&mut self, address: &[usize]) {
        self.address = address.to_vec();
    }

    fn full(&mut self) -> bool {
        self.result.next.is_some()
    }

    fn wants(&mut self, _: Place, path: &[&str], _: bool, bytes: usize) -> bool {
        if self.full() {
            return false;
        }
        let ordinal = self.ordinal;
        self.ordinal += 1;
        if ordinal < self.start {
            return false;
        }
        let path_size = path.iter().map(|part| part.len()).sum::<usize>();
        let size = if path_size > PAGE_BYTES - TEXT_BYTES {
            CHUNK_BYTES
        } else {
            path_size
        } + bytes;
        if self.result.fields.len() == PAGE_FIELDS || self.bytes + size > PAGE_BYTES {
            self.result.next = Some(ordinal);
            self.result.next_address = Some(self.address.clone());
            return false;
        }
        self.bytes += size;
        true
    }

    fn push(&mut self, place: Place, path: &[&str], value: Leaf, marks: Marks) {
        let display_bytes = marks.text.as_ref().map_or(0, String::len);
        if self.bytes + display_bytes > PAGE_BYTES {
            self.result.next = Some(self.ordinal - 1);
            self.result.next_address = Some(self.address.clone());
            return;
        }
        self.bytes += display_bytes;
        let path_bytes = path.iter().map(|part| part.len()).sum::<usize>();
        let path_cut = (path_bytes > PAGE_BYTES - TEXT_BYTES)
            .then_some((self.ordinal - 1, path_bytes + path.len().saturating_sub(1)));
        let mut remaining = if path_cut.is_some() {
            CHUNK_BYTES
        } else {
            path_bytes
        };
        let identity = path_cut.map(|_| {
            use sha2::Digest;
            self.digest
                .clone()
                .finalize()
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect()
        });
        let path = path
            .iter()
            .map(|part| {
                let shown = prefix(part, remaining);
                remaining -= shown.len();
                shown.to_owned()
            })
            .collect();
        self.result.fields.push(PageField {
            identity,
            address: self.address.clone(),
            field: InspectionField {
                group: place.group,
                origin: place.origin,
                path,
                value,
                color: marks.color,
                text: marks.text,
                fraction: marks.fraction,
            },
            cut: None,
            path_cut,
        });
    }

    fn text(&mut self, place: Place, path: &[&str], text: &str) {
        let shown = prefix(text, TEXT_BYTES);
        if !self.wants(place, path, false, shown.len()) {
            return;
        }
        self.push(place, path, Leaf::Text(shown.to_owned()), Marks::default());
        if shown.len() != text.len()
            && let Some(field) = self.result.fields.last_mut()
        {
            field.cut = Some((self.ordinal - 1, text.len()));
        }
    }
}

/// Reads at most 4096 fields and 8 MiB of paths/text. Text previews are 16 KiB; whole Raw copying uses its own bounded stream.
pub fn field_page(entity: &Entity, start: usize) -> FieldPage {
    field_page_from(entity, start, &[])
}

/// Resumes at a structural address returned with a page, without visiting preceding subtrees.
pub fn field_page_from(entity: &Entity, start: usize, resume: &[usize]) -> FieldPage {
    page_ordered(entity, start, resume, None)
}

pub(super) fn page_ordered(
    entity: &Entity,
    start: usize,
    resume: &[usize],
    order: Option<&super::Traversal>,
) -> FieldPage {
    use sha2::Digest;
    let mut page = Page {
        work: 1 << 20,
        digest: sha2::Sha256::new(),
        order,
        resume: resume.to_vec(),
        address: Vec::new(),
        start,
        ordinal: if resume.is_empty() { 0 } else { start },
        bytes: 0,
        result: FieldPage {
            next_address: None,
            fields: Vec::new(),
            next: None,
        },
    };
    walk_entity(entity, &mut page);
    page.result
}

struct Chunk<'a> {
    order: Option<&'a super::Traversal>,
    resume: Vec<usize>,
    wanted: usize,
    path: bool,
    ordinal: usize,
    offset: usize,
    result: Option<Result<FieldChunk, String>>,
}

impl Sink for Chunk<'_> {
    fn colors(&mut self, address: &[usize], items: &[crate::Value]) -> Option<bool> {
        // A resume below an element's children proves this list was not a collapsed colour list.
        if self.resume.starts_with(address) && self.resume.len() > address.len() + 1 {
            return Some(false);
        }
        if let Some(order) = self.order {
            order.colors(address, items, &mut (1 << 20))
        } else {
            Some(
                !items.is_empty()
                    && items
                        .iter()
                        .all(|item| crate::ColorEntry::read_exact(item).is_some()),
            )
        }
    }

    fn ranked(&self) -> Option<&[u32]> {
        self.order.map(super::Traversal::order)
    }
    fn resume(&self) -> &[usize] {
        &self.resume
    }
    fn full(&mut self) -> bool {
        self.result.is_some()
    }
    fn wants(&mut self, _: Place, path: &[&str], _: bool, _: usize) -> bool {
        let ordinal = self.ordinal;
        self.ordinal += 1;
        if self.path && !self.full() && ordinal == self.wanted {
            self.result = Some(path_chunk(path, self.offset));
        }
        false
    }
    fn push(&mut self, _: Place, _: &[&str], _: Leaf, _: Marks) {}
    fn text(&mut self, place: Place, path: &[&str], text: &str) {
        if self.path {
            self.wants(place, path, false, text.len());
            return;
        }
        let ordinal = self.ordinal;
        self.ordinal += 1;
        if self.full() || ordinal != self.wanted {
            return;
        }
        self.result = Some(if text.len() > MAX_COPY_BYTES {
            Err(format!(
                "Value is {} bytes; copying supports at most {MAX_COPY_BYTES} bytes",
                text.len()
            ))
        } else if self.offset > text.len() || !text.is_char_boundary(self.offset) {
            Err("The value offset is invalid".into())
        } else {
            let part = prefix(&text[self.offset..], CHUNK_BYTES);
            let end = self.offset + part.len();
            Ok(FieldChunk {
                text: part.to_owned(),
                total: text.len(),
                next: (end < text.len()).then_some(end),
            })
        });
    }
}

/// Retrieves only one bounded piece, without copying the rest of the leaf.
pub fn field_chunk(
    entity: &Entity,
    ordinal: usize,
    offset: usize,
    path: bool,
) -> Result<FieldChunk, String> {
    field_chunk_from(entity, ordinal, offset, path, &[])
}

/// Reads a field directly at its bounded structural address.
pub fn field_chunk_from(
    entity: &Entity,
    ordinal: usize,
    offset: usize,
    path: bool,
    resume: &[usize],
) -> Result<FieldChunk, String> {
    chunk_ordered(entity, ordinal, offset, path, resume, None)
}

pub(super) fn chunk_ordered(
    entity: &Entity,
    ordinal: usize,
    offset: usize,
    path: bool,
    resume: &[usize],
    order: Option<&super::Traversal>,
) -> Result<FieldChunk, String> {
    let mut chunk = Chunk {
        order,
        resume: resume.to_vec(),
        wanted: ordinal,
        path,
        ordinal: if resume.is_empty() { 0 } else { ordinal },
        offset,
        result: None,
    };
    walk_entity(entity, &mut chunk);
    chunk
        .result
        .unwrap_or_else(|| Err("The inspected text is no longer available".into()))
}

fn path_chunk(path: &[&str], offset: usize) -> Result<FieldChunk, String> {
    let total = path.iter().map(|part| part.len()).sum::<usize>() + path.len().saturating_sub(1);
    if total > MAX_COPY_BYTES {
        return Err(format!(
            "Path is {total} bytes; copying supports at most {MAX_COPY_BYTES} bytes"
        ));
    }
    if offset > total {
        return Err("The path offset is invalid".into());
    }
    let mut skip = offset;
    let mut text = String::new();
    for (index, part) in path.iter().enumerate() {
        for part in [if index == 0 { "" } else { "." }, *part] {
            if skip >= part.len() {
                skip -= part.len();
                continue;
            }
            if !part.is_char_boundary(skip) {
                return Err("The path offset is invalid".into());
            }
            let rest = &part[skip..];
            let shown = prefix(rest, CHUNK_BYTES - text.len());
            text.push_str(shown);
            skip = 0;
            if shown.len() < rest.len() || text.len() == CHUNK_BYTES {
                let next = offset + text.len();
                return Ok(FieldChunk {
                    text,
                    total,
                    next: (next < total).then_some(next),
                });
            }
        }
    }
    Ok(FieldChunk {
        text,
        total,
        next: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        BoundingBox, EntityId, Fields, Geometry, Name, Observation, ObservationTime, SceneTime,
        Value,
    };

    fn object(fields: Fields) -> Entity {
        let observation = Observation::new(
            ObservationTime::from(SceneTime::Frame(0)),
            Geometry::Box(BoundingBox::new(0.0, 0.0, 1.0, 1.0).expect("bounds")),
        )
        .with_details(fields, Vec::new())
        .expect("valid source data");
        let mut entity = Entity::new(EntityId::from(7_i64));
        entity.push_observation(observation);
        entity
    }

    #[test]
    fn pages_reach_the_last_field_and_charge_the_last_included_byte() {
        let fields = vec![
            (Name::Owned("a".repeat(4 * 1024 * 1024 - 7)), Value::Int(0)),
            (
                Name::Owned("b".repeat(4 * 1024 * 1024 - 7)),
                Value::Str("x".into()),
            ),
            (Name::Owned("z".into()), Value::Null),
        ];
        let entity = object(fields);
        let page = field_page(&entity, 0);
        let bytes: usize = page
            .fields
            .iter()
            .map(|entry| {
                entry.field.path.iter().map(String::len).sum::<usize>()
                    + match &entry.field.value {
                        Leaf::Text(text) => text.len(),
                        _ => 0,
                    }
            })
            .sum();
        assert_eq!(bytes, 8 * 1024 * 1024);
        assert_eq!(page.fields.len(), 6);
        assert_eq!(page.next, Some(6));
        let last = field_page_from(
            &entity,
            6,
            page.next_address.as_deref().expect("next address"),
        );
        assert_eq!(last.fields[0].field.path, ["z"]);
        assert_eq!(
            last.fields.last().expect("raw frame").field.path,
            ["frame_number"]
        );
        assert_eq!(last.next, None);
        let many = object(
            (0..5000)
                .map(|i| (Name::Owned(format!("field{i}")), Value::Int(i)))
                .collect(),
        );
        let first = field_page(&many, 0);
        assert_eq!(first.fields.len(), 4096);
        let next = field_page_from(
            &many,
            first.next.expect("next"),
            first.next_address.as_deref().expect("next address"),
        );
        assert_eq!(next.fields[next.fields.len() - 2].field.path, ["field4999"]);
        assert_eq!(next.next, None);
    }

    #[test]
    fn the_copy_ceiling_includes_its_last_byte_and_large_paths_still_advance() {
        for size in [268_435_456, 268_435_457] {
            let entity = object(vec![(
                Name::Owned("text".into()),
                Value::Str("x".repeat(size).into()),
            )]);
            let result = field_chunk(&entity, 4, size - 1, false);
            if size == 268_435_456 {
                let chunk = result.expect("exactly the ceiling is copyable");
                assert_eq!(chunk.text, "x");
                assert_eq!(chunk.next, None);
            } else {
                assert_eq!(
                    result.err().as_deref(),
                    Some("Value is 268435457 bytes; copying supports at most 268435456 bytes")
                );
            }
        }
        let entity = object(vec![(
            Name::Owned("p".repeat(9 * 1024 * 1024)),
            Value::Int(1),
        )]);
        let page = field_page(&entity, 0);
        assert_eq!(page.next, None);
        let (ordinal, bytes) = page.fields[4].path_cut.expect("path preview");
        assert_eq!(bytes, 9 * 1024 * 1024);
        let chunk = field_chunk(&entity, ordinal, bytes - 1, true).expect("last path byte");
        assert_eq!(chunk.text, "p");
        assert_eq!(chunk.next, None);
    }

    #[test]
    fn a_chunk_includes_ascii_byte_65536_and_stops_before_65537() {
        let entity = object(vec![(
            Name::Owned("text".into()),
            Value::Str(format!("{}z", "a".repeat(65536)).into()),
        )]);
        let first = field_chunk(&entity, 4, 0, false).expect("first chunk");
        assert_eq!(first.text, "a".repeat(65536));
        assert_eq!(first.next, Some(65536));
        let last = field_chunk(&entity, 4, 65536, false).expect("last chunk");
        assert_eq!(last.text, "z");
        assert_eq!(last.next, None);
    }

    #[test]
    fn an_oversized_value_is_read_in_utf8_aligned_chunks() {
        let text = format!("{}😀é", "a".repeat(65535));
        let entity = object(vec![(
            Name::Owned("text".into()),
            Value::Str(text.clone().into()),
        )]);
        let page = field_page(&entity, 0);
        let (ordinal, total) = page.fields[4].cut.expect("preview is cut");
        assert_eq!(total, text.len());
        let first = field_chunk(&entity, ordinal, 0, false).expect("first chunk");
        assert_eq!(first.text.len(), 65535);
        let last =
            field_chunk(&entity, ordinal, first.next.expect("more"), false).expect("last chunk");
        assert_eq!(last.text, "😀é");
        assert_eq!(last.next, None);
        assert_eq!(format!("{}{}", first.text, last.text), text);
    }
}
