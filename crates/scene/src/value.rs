use std::{borrow::Cow, fmt};

use serde::{
    Deserialize, Deserializer, Serialize, Serializer,
    de::{MapAccess, SeqAccess, Visitor},
    ser::{SerializeMap, SerializeSeq},
};

/// A name that is usually a static string and sometimes owned.
pub type Name = Cow<'static, str>;

/// An ordered map: the order fields were added is the order they are shown.
pub type Fields = Vec<(Name, Value)>;

/// How deeply a [`Value`] may nest: the limit `serde_json` applies when reading. A decoder that builds Values from
/// another format fails the decode when the source would go deeper.
pub const MAX_VALUE_DEPTH: usize = 128;

/// A source value kept as it was: JSON-like data limited to what a Scene can show.
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    /// No value.
    Null,
    /// A truth value.
    Bool(bool),
    /// A whole number.
    Int(i64),
    /// A fractional number.
    Float(f64),
    /// Text.
    Str(Name),
    /// An ordered list.
    List(Vec<Value>),
    /// Named values in source order.
    Map(Fields),
}

impl Value {
    /// The truth value, if this is a `Bool`.
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Self::Bool(value) => Some(*value),
            _ => None,
        }
    }

    /// The whole number, if this is an `Int`.
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Self::Int(value) => Some(*value),
            _ => None,
        }
    }

    /// The number as a float, if this is an `Int` or a `Float`; never a `Bool`.
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            Self::Int(value) => Some(*value as f64),
            Self::Float(value) => Some(*value),
            _ => None,
        }
    }

    /// The text, if this is a `Str`.
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Self::Str(value) => Some(value),
            _ => None,
        }
    }

    /// The items, if this is a `List`.
    pub fn as_list(&self) -> Option<&[Value]> {
        match self {
            Self::List(items) => Some(items),
            _ => None,
        }
    }

    /// The fields, if this is a `Map`.
    pub fn as_map(&self) -> Option<&Fields> {
        match self {
            Self::Map(fields) => Some(fields),
            _ => None,
        }
    }
}

impl Serialize for Value {
    /// Writes the matching JSON-like value; a `Map` keeps its field order.
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self {
            Self::Null => serializer.serialize_unit(),
            Self::Bool(value) => serializer.serialize_bool(*value),
            Self::Int(value) => serializer.serialize_i64(*value),
            Self::Float(value) => serializer.serialize_f64(*value),
            Self::Str(value) => serializer.serialize_str(value),
            Self::List(items) => {
                let mut seq = serializer.serialize_seq(Some(items.len()))?;
                for item in items {
                    seq.serialize_element(item)?;
                }
                seq.end()
            }
            Self::Map(fields) => {
                let mut map = serializer.serialize_map(Some(fields.len()))?;
                for (key, value) in fields {
                    map.serialize_entry(key, value)?;
                }
                map.end()
            }
        }
    }
}

impl<'de> Deserialize<'de> for Value {
    /// Reads any self-describing document: whole numbers become `Int`, other numbers `Float`.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(ValueVisitor)
    }
}

struct ValueVisitor;

impl<'de> Visitor<'de> for ValueVisitor {
    type Value = Value;

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("any JSON value")
    }

    fn visit_unit<E>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }

    fn visit_none<E>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }

    fn visit_some<D: Deserializer<'de>>(self, deserializer: D) -> Result<Value, D::Error> {
        Value::deserialize(deserializer)
    }

    fn visit_bool<E>(self, v: bool) -> Result<Value, E> {
        Ok(Value::Bool(v))
    }

    fn visit_i64<E>(self, v: i64) -> Result<Value, E> {
        Ok(Value::Int(v))
    }

    fn visit_u64<E>(self, v: u64) -> Result<Value, E> {
        // Past i64::MAX no integer fits, so the number is kept approximately.
        Ok(i64::try_from(v).map_or(Value::Float(v as f64), Value::Int))
    }

    fn visit_f64<E>(self, v: f64) -> Result<Value, E> {
        Ok(Value::Float(v))
    }

    fn visit_str<E>(self, v: &str) -> Result<Value, E> {
        Ok(Value::Str(Name::Owned(v.to_owned())))
    }

    fn visit_string<E>(self, v: String) -> Result<Value, E> {
        Ok(Value::Str(Name::Owned(v)))
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
        let mut items = Vec::with_capacity(seq.size_hint().unwrap_or(0).min(4096));
        while let Some(item) = seq.next_element()? {
            items.push(item);
        }
        Ok(Value::List(items))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
        let mut fields = Fields::with_capacity(map.size_hint().unwrap_or(0).min(4096));
        while let Some((key, value)) = map.next_entry::<String, Value>()? {
            fields.push((Name::Owned(key), value));
        }
        Ok(Value::Map(fields))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn nested(depth: usize) -> String {
        format!("{}1{}", "[".repeat(depth), "]".repeat(depth))
    }

    #[test]
    fn numbers_read_as_floats_but_booleans_do_not() {
        assert_eq!(Value::Int(3).as_f64(), Some(3.0));
        assert_eq!(Value::Float(0.5).as_f64(), Some(0.5));
        assert_eq!(Value::Bool(true).as_f64(), None);
    }

    #[test]
    fn maps_keep_their_order_through_json() {
        let text = r#"{"b":1,"a":[true,null,"x",1.5]}"#;
        let value: Value = serde_json::from_str(text).expect("valid JSON");

        assert_eq!(value.as_map().map(|fields| fields[0].0.as_ref()), Some("b"));
        assert_eq!(serde_json::to_string(&value).as_deref().ok(), Some(text));
    }

    #[test]
    fn reading_stops_at_the_nesting_limit() {
        assert!(serde_json::from_str::<Value>(&nested(MAX_VALUE_DEPTH / 2)).is_ok());
        assert!(serde_json::from_str::<Value>(&nested(MAX_VALUE_DEPTH + 1)).is_err());
    }
}
