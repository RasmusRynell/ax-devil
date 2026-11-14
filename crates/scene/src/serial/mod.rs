//! How Scenes are written and read: one data model, two encodings, and the presentation columns of [`columns`].
//!
//! - **JSON** for goldens and the plugin wire: `{"scene_version": "4.3", "scenes": [...]}`. A Scene's time is
//!   `{"frame": N}` or `{"us": N}`; an observation's has either key or both. A box is `{"box": [left, top, right,
//!   bottom]}`, a polygon `{"polygon": [[x, y], ...]}`; source values are plain JSON; a picture's bytes are base64. Parts that are absent are left out (an entity's `images`, an
//!   observation's `measurements`, `source_data` and `debug`); `end_reason`, `motion_state`, scores and confidences
//!   are `null` instead. The reference Python app's Scene dumps (`fixtures/oracle/`) are this form.
//! - **Postcard** for cache artifacts, from the same declarations, with every field written. A record carries no
//!   version of its own: the artifact that holds it records the Scene version.
//!
//! Reading builds every value through the model's constructors, so a document or record that breaks a model rule is
//! refused with the reason instead of loaded.

pub mod columns;
mod picture;
mod read;
mod value;
mod wire;
mod write;

use serde::Deserialize;
use serde_json::Value as Json;

use crate::{Fields, MAX_VALUE_DEPTH, SCENE_VERSION, Scene, SceneError, SceneVersion};
use read::{DocumentIn, SceneIn};
use value::{FieldsIn, FieldsOut};
use write::{Document, W};

fn failed(reason: impl std::fmt::Display) -> SceneError {
    SceneError::Serial(reason.to_string())
}

/// The JSON document of `scenes`: `{"scene_version": "4.3", "scenes": [...]}`.
///
/// A `serde_json::Value` does not keep the order of an object's keys, so the order of source values inside a Scene is
/// lost here; [`to_json_string`] keeps it.
pub fn to_json(scenes: &[Scene]) -> Json {
    // Serializing the model cannot fail: every key is text and the model holds no unsupported values.
    serde_json::to_value(Document(scenes)).unwrap_or(Json::Null)
}

/// The JSON text of `scenes`, compact, with source values in the order they were added.
pub fn to_json_string(scenes: &[Scene]) -> Result<String, SceneError> {
    serde_json::to_string(&Document(scenes)).map_err(failed)
}

/// Reads a JSON document written by [`to_json`], validating every value through the model's constructors.
///
/// Fails when the document's `scene_version` has another major version or a newer minor version than this build.
pub fn from_json(value: &Json) -> Result<Vec<Scene>, SceneError> {
    // Text is limited while it is read; a document built in memory is checked before the recursion starts.
    let mut pending = vec![(value, 1_usize)];
    while let Some((json, depth)) = pending.pop() {
        if depth > MAX_VALUE_DEPTH {
            return Err(failed(format!(
                "the document nests deeper than {MAX_VALUE_DEPTH} levels"
            )));
        }
        match json {
            Json::Array(items) => pending.extend(items.iter().map(|item| (item, depth + 1))),
            Json::Object(fields) => pending.extend(fields.values().map(|item| (item, depth + 1))),
            _ => {}
        }
    }
    document(DocumentIn::deserialize(value).map_err(failed)?)
}

/// Reads JSON text written by [`to_json_string`], keeping source values in the order they are written in.
pub fn from_json_str(text: &str) -> Result<Vec<Scene>, SceneError> {
    document(serde_json::from_str(text).map_err(failed)?)
}

/// Reads one Scene from JSON text in the per-Scene shape [`to_json_string`] writes, building every value through the
/// model's constructors.
pub fn scene_from_json_slice(bytes: &[u8]) -> Result<Scene, SceneError> {
    serde_json::from_slice::<SceneIn>(bytes)
        .map_err(failed)?
        .build()
}

fn document(document: DocumentIn) -> Result<Vec<Scene>, SceneError> {
    let version = parse_version(&document.scene_version)?;
    if !SCENE_VERSION.accepts(version) {
        return Err(failed(format!(
            "scene version {version} is not readable by this build (version {SCENE_VERSION})"
        )));
    }
    document.scenes.into_iter().map(SceneIn::build).collect()
}

fn parse_version(text: &str) -> Result<SceneVersion, SceneError> {
    let part = |part: Option<&str>| part.and_then(|number| number.parse::<u16>().ok());
    let mut numbers = text.split('.');
    match (part(numbers.next()), part(numbers.next()), numbers.next()) {
        (Some(major), Some(minor), None) => Ok(SceneVersion { major, minor }),
        _ => Err(failed(format!(
            "'{text}' is not a scene version such as 4.1"
        ))),
    }
}

/// Appends the postcard encoding of `scene` to `out`; on failure `out` is left empty.
pub fn encode(scene: &Scene, out: &mut Vec<u8>) -> Result<(), SceneError> {
    append(&W(scene), out)
}

/// Reads one Scene from the postcard bytes [`encode`] wrote.
pub fn decode(bytes: &[u8]) -> Result<Scene, SceneError> {
    read_all::<SceneIn>(bytes)?.build()
}

/// Appends the postcard encoding of `fields` to `out`, for the file facts an artifact keeps next to its Scenes.
pub fn encode_fields(fields: &Fields, out: &mut Vec<u8>) -> Result<(), SceneError> {
    append(&FieldsOut(fields), out)
}

/// Reads the fields [`encode_fields`] wrote.
pub fn decode_fields(bytes: &[u8]) -> Result<Fields, SceneError> {
    read_all::<FieldsIn>(bytes).map(|fields| fields.0)
}

fn append(value: &impl serde::Serialize, out: &mut Vec<u8>) -> Result<(), SceneError> {
    let written = postcard::to_extend(value, std::mem::take(out)).map_err(failed)?;
    *out = written;
    Ok(())
}

fn read_all<'a, T: serde::Deserialize<'a>>(bytes: &'a [u8]) -> Result<T, SceneError> {
    let (value, rest) = postcard::take_from_bytes::<T>(bytes).map_err(failed)?;
    if rest.is_empty() {
        Ok(value)
    } else {
        Err(failed(format!(
            "{} unexpected bytes after the record",
            rest.len()
        )))
    }
}

#[cfg(test)]
mod tests;
