//! The `input` of a request that reads lanes: the files of a bundle by key, and the video.

use std::path::Path;

use handler_api::Bundle;
use serde_json::{Map, Value, json};

/// The path as text, absolute: the plugin works in a folder of its own, so a path it is given must not depend on
/// ours. Plugins need UTF-8 paths.
pub(crate) fn path_text(path: &Path, what: &str) -> Result<String, String> {
    let absolute = std::path::absolute(path)
        .map_err(|error| format!("Cannot resolve the {what} path: {error}"))?;
    absolute
        .to_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("The {what} path is not valid UTF-8, which plugins need"))
}

/// `{"lanes": {key: path}, "video": path}`, without `video` when the bundle has none.
pub(crate) fn input(bundle: &Bundle) -> Result<Value, String> {
    let mut lanes = Map::new();
    for (key, lane) in &bundle.lanes {
        lanes.insert(key.clone(), json!(path_text(&lane.path, "lane file")?));
    }
    let mut input = Map::new();
    input.insert("lanes".to_owned(), Value::Object(lanes));
    if let Some(video) = &bundle.video {
        input.insert("video".to_owned(), json!(path_text(&video.path, "video")?));
    }
    Ok(Value::Object(input))
}
