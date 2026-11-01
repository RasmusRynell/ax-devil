//! Reading a catalog document: JSON with every key unique, within the size, depth and collection limits.

use std::cell::RefCell;
use std::fmt;

use serde::de::{DeserializeSeed, Deserializer, MapAccess, SeqAccess, Visitor};

use crate::definitions::{MAX_COLLECTION, MAX_DEPTH, MAX_DOCUMENT_BYTES};
use crate::diagnostics::{Pointer, Problem, Problems, codes};

/// A JSON value with object members in document order. Catalog constants are kept in this form while compiling.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Json {
    Null,
    Bool(bool),
    /// A number written without fraction or exponent that fits 64 bits.
    Int(i64),
    /// Any other number.
    Float(f64),
    Str(String),
    Array(Vec<Json>),
    Object(Vec<(String, Json)>),
}

impl Json {
    /// The members of an object.
    pub(crate) fn as_object(&self) -> Option<&[(String, Json)]> {
        match self {
            Self::Object(members) => Some(members),
            _ => None,
        }
    }

    /// The member `key` of an object.
    pub(crate) fn get(&self, key: &str) -> Option<&Json> {
        self.as_object()?
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value)
    }

    /// The text of a string.
    pub(crate) fn as_str(&self) -> Option<&str> {
        match self {
            Self::Str(text) => Some(text),
            _ => None,
        }
    }

    /// The number of an integer or a float.
    pub(crate) fn as_f64(&self) -> Option<f64> {
        match self {
            Self::Int(value) => Some(*value as f64),
            Self::Float(value) => Some(*value),
            _ => None,
        }
    }
}

/// Parses `source` as a catalog document, recording every problem that stops it from being one.
pub(crate) fn parse(source: &str, problems: &mut Problems) -> Option<Json> {
    if source.len() > MAX_DOCUMENT_BYTES {
        problems.push(Problem::new(
            codes::EXECUTION_LIMIT,
            "Catalog document size limit exceeded.",
        ));
        return None;
    }
    let duplicates = RefCell::new(Vec::new());
    let mut deserializer = serde_json::Deserializer::from_str(source);
    let parsed = Seed {
        duplicates: &duplicates,
        at: Pointer::root(),
    }
    .deserialize(&mut deserializer)
    .and_then(|value| deserializer.end().map(|()| value));
    let document = match parsed {
        Ok(document) => document,
        Err(error) => {
            problems.push(Problem::new(codes::INVALID_JSON, error.to_string()));
            return None;
        }
    };
    let duplicates = duplicates.into_inner();
    let clean = duplicates.is_empty();
    for problem in duplicates {
        problems.push(problem);
    }
    let within = check_limits(&document, 0, &Pointer::root(), problems);
    (clean && within).then_some(document)
}

/// Records where `value` nests deeper than [`MAX_DEPTH`] or holds more than [`MAX_COLLECTION`] items.
fn check_limits(value: &Json, depth: usize, at: &Pointer, problems: &mut Problems) -> bool {
    if depth > MAX_DEPTH {
        problems
            .push(Problem::new(codes::EXECUTION_LIMIT, "Document depth limit exceeded.").at(at));
        return false;
    }
    let (count, what) = match value {
        Json::Array(items) => (items.len(), "Collection"),
        Json::Object(members) => (members.len(), "Record"),
        _ => return true,
    };
    if count > MAX_COLLECTION {
        problems.push(
            Problem::new(
                codes::EXECUTION_LIMIT,
                format!("{what} size limit exceeded."),
            )
            .at(at),
        );
        return false;
    }
    match value {
        Json::Array(items) => items
            .iter()
            .enumerate()
            .all(|(index, item)| check_limits(item, depth + 1, &at.index(index), problems)),
        Json::Object(members) => members
            .iter()
            .all(|(key, member)| check_limits(member, depth + 1, &at.key(key), problems)),
        _ => true,
    }
}

/// Builds a [`Json`] value, noting every repeated object key at its place.
struct Seed<'a> {
    duplicates: &'a RefCell<Vec<Problem>>,
    at: Pointer,
}

impl<'de> DeserializeSeed<'de> for Seed<'_> {
    type Value = Json;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<Json, D::Error> {
        deserializer.deserialize_any(self)
    }
}

impl<'de> Visitor<'de> for Seed<'_> {
    type Value = Json;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a JSON value")
    }

    fn visit_unit<E>(self) -> Result<Json, E> {
        Ok(Json::Null)
    }

    fn visit_bool<E>(self, value: bool) -> Result<Json, E> {
        Ok(Json::Bool(value))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Json, E> {
        Ok(Json::Int(value))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Json, E> {
        Ok(i64::try_from(value).map_or(Json::Float(value as f64), Json::Int))
    }

    fn visit_f64<E: serde::de::Error>(self, value: f64) -> Result<Json, E> {
        if value.is_finite() {
            Ok(Json::Float(value))
        } else {
            Err(E::custom("number must be finite"))
        }
    }

    fn visit_str<E>(self, value: &str) -> Result<Json, E> {
        Ok(Json::Str(value.to_string()))
    }

    fn visit_string<E>(self, value: String) -> Result<Json, E> {
        Ok(Json::Str(value))
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Json, A::Error> {
        let mut items = Vec::new();
        while let Some(item) = seq.next_element_seed(Seed {
            duplicates: self.duplicates,
            at: self.at.index(items.len()),
        })? {
            items.push(item);
        }
        Ok(Json::Array(items))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Json, A::Error> {
        let mut members: Vec<(String, Json)> = Vec::new();
        while let Some(key) = map.next_key::<String>()? {
            let at = self.at.key(&key);
            let value = map.next_value_seed(Seed {
                duplicates: self.duplicates,
                at: at.clone(),
            })?;
            if members.iter().any(|(name, _)| *name == key) {
                let problem = Problem::new(
                    codes::DUPLICATE_DEFINITION,
                    format!("Duplicate JSON key: {key}."),
                );
                self.duplicates.borrow_mut().push(problem.at(&at));
            } else {
                members.push((key, value));
            }
        }
        Ok(Json::Object(members))
    }
}
