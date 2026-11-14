//! Source values and ordered field maps on the wire.
//!
//! JSON gets the plain JSON shape. Postcard is not self-describing, so it gets an explicit variant index per value.

use std::{fmt, marker::PhantomData};

use serde::{
    Deserialize, Deserializer, Serialize, Serializer,
    de::{self, DeserializeSeed, EnumAccess, MapAccess, SeqAccess, VariantAccess, Visitor},
    ser::SerializeMap,
};

use crate::{Fields, MAX_VALUE_DEPTH, Name, Value};

const VALUE: &str = "Value";
const VARIANTS: &[&str] = &["Null", "Bool", "Int", "Float", "Str", "List", "Map"];

/// A borrowed [`Value`] being written.
pub(super) struct ValueOut<'a>(pub &'a Value);

impl Serialize for ValueOut<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        if serializer.is_human_readable() {
            return self.0.serialize(serializer);
        }
        match self.0 {
            Value::Null => serializer.serialize_unit_variant(VALUE, 0, "Null"),
            Value::Bool(v) => serializer.serialize_newtype_variant(VALUE, 1, "Bool", v),
            Value::Int(v) => serializer.serialize_newtype_variant(VALUE, 2, "Int", v),
            Value::Float(v) => serializer.serialize_newtype_variant(VALUE, 3, "Float", v),
            Value::Str(v) => serializer.serialize_newtype_variant(VALUE, 4, "Str", v.as_ref()),
            Value::List(items) => {
                serializer.serialize_newtype_variant(VALUE, 5, "List", &ListOut(items))
            }
            Value::Map(fields) => {
                serializer.serialize_newtype_variant(VALUE, 6, "Map", &FieldsOut(fields))
            }
        }
    }
}

struct ListOut<'a>(&'a [Value]);

impl Serialize for ListOut<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_seq(self.0.iter().map(ValueOut))
    }
}

/// Borrowed [`Fields`] being written, as an ordered map.
pub(super) struct FieldsOut<'a>(pub &'a Fields);

impl Serialize for FieldsOut<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(Some(self.0.len()))?;
        for (key, value) in self.0 {
            map.serialize_entry(key.as_ref(), &ValueOut(value))?;
        }
        map.end()
    }
}

/// A [`Value`] being read, `depth` containers deep; deeper than [`MAX_VALUE_DEPTH`] is refused, so a crafted record
/// cannot exhaust the stack.
#[derive(Clone, Copy)]
struct ValueSeed {
    depth: usize,
}

impl ValueSeed {
    fn inside(self) -> Result<Self, String> {
        match self.depth {
            depth if depth < MAX_VALUE_DEPTH => Ok(Self { depth: depth + 1 }),
            _ => Err(format!(
                "a value nests deeper than {MAX_VALUE_DEPTH} levels"
            )),
        }
    }
}

impl<'de> DeserializeSeed<'de> for ValueSeed {
    type Value = Value;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<Value, D::Error> {
        if deserializer.is_human_readable() {
            // JSON text is already limited by the reader; see `from_json` for documents built in memory.
            Value::deserialize(deserializer)
        } else {
            deserializer.deserialize_enum(VALUE, VARIANTS, TaggedVisitor(self))
        }
    }
}

struct TaggedVisitor(ValueSeed);

impl<'de> Visitor<'de> for TaggedVisitor {
    type Value = Value;

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("a tagged value")
    }

    fn visit_enum<A: EnumAccess<'de>>(self, data: A) -> Result<Value, A::Error> {
        let (tag, variant) = data.variant::<u32>()?;
        Ok(match tag {
            0 => {
                variant.unit_variant()?;
                Value::Null
            }
            1 => Value::Bool(variant.newtype_variant()?),
            2 => Value::Int(variant.newtype_variant()?),
            3 => Value::Float(variant.newtype_variant()?),
            4 => Value::Str(Name::Owned(variant.newtype_variant::<String>()?)),
            5 => Value::List(
                variant
                    .newtype_variant_seed(ListSeed(self.0.inside().map_err(de::Error::custom)?))?,
            ),
            6 => {
                Value::Map(variant.newtype_variant_seed(FieldsSeed(
                    self.0.inside().map_err(de::Error::custom)?,
                ))?)
            }
            other => return Err(de::Error::custom(format!("unknown value tag {other}"))),
        })
    }
}

struct ListSeed(ValueSeed);

impl<'de> DeserializeSeed<'de> for ListSeed {
    type Value = Vec<Value>;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<Vec<Value>, D::Error> {
        deserializer.deserialize_seq(self)
    }
}

impl<'de> Visitor<'de> for ListSeed {
    type Value = Vec<Value>;

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("a list of values")
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Vec<Value>, A::Error> {
        let mut items = Vec::with_capacity(seq.size_hint().unwrap_or(0).min(4096));
        while let Some(item) = seq.next_element_seed(self.0)? {
            items.push(item);
        }
        Ok(items)
    }
}

struct FieldsSeed(ValueSeed);

impl<'de> DeserializeSeed<'de> for FieldsSeed {
    type Value = Fields;

    fn deserialize<D: Deserializer<'de>>(self, deserializer: D) -> Result<Fields, D::Error> {
        deserializer.deserialize_map(self)
    }
}

impl<'de> Visitor<'de> for FieldsSeed {
    type Value = Fields;

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("a map of named values")
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Fields, A::Error> {
        let mut fields = Fields::with_capacity(map.size_hint().unwrap_or(0).min(4096));
        while let Some((key, value)) = map.next_entry_seed(PhantomData::<String>, self.0)? {
            fields.push((Name::Owned(key), value));
        }
        Ok(fields)
    }
}

/// [`Fields`] being read from an ordered map; the map itself is the first level of nesting.
#[derive(Default)]
pub(super) struct FieldsIn(pub Fields);

impl<'de> Deserialize<'de> for FieldsIn {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let top = ValueSeed { depth: 0 }.inside().map_err(de::Error::custom)?;
        FieldsSeed(top).deserialize(deserializer).map(Self)
    }
}
