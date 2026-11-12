//! What the `meta/task` element says about the file.

use std::collections::BTreeSet;

use scene::{ClassType, Fields, Name, Value};

use super::shape::int;

/// The fields of the task the decoder reads; a missing one is 0 or empty.
pub(super) struct Essentials {
    pub width: i64,
    pub height: i64,
    pub task_name: String,
    pub start_frame: i64,
    pub stop_frame: i64,
    pub size: i64,
}

fn get<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    value
        .as_map()?
        .iter()
        .find(|(name, _)| name == key)
        .map(|(_, value)| value)
}

/// The text of an element value: itself when it is text, else its `#text`.
fn text(value: &Value) -> Option<&str> {
    value.as_str().or_else(|| get(value, "#text")?.as_str())
}

fn integer(parent: Option<&Value>, key: &str) -> Result<i64, String> {
    match parent.and_then(|parent| get(parent, key)).and_then(text) {
        None => Ok(0),
        Some(text) => {
            int(text).ok_or_else(|| format!("CVAT task {key} is {text:?}, not a whole number"))
        }
    }
}

impl Essentials {
    /// Reads the task of the `meta` element's value.
    pub(super) fn read(meta: &Value) -> Result<Self, String> {
        let task = get(meta, "task").filter(|task| task.as_map().is_some());
        let original_size = task.and_then(|task| get(task, "original_size"));
        let segment = task
            .and_then(|task| get(task, "segments"))
            .and_then(|segments| get(segments, "segment"))
            .and_then(|segment| match segment {
                Value::List(items) => items.first(),
                one => Some(one),
            });
        let (start_frame, stop_frame) = (integer(segment, "start")?, integer(segment, "stop")?);
        let mut size = integer(task, "size")?;
        if size == 0 && stop_frame >= start_frame {
            size = stop_frame - start_frame + 1;
        }
        Ok(Self {
            width: integer(original_size, "width")?,
            height: integer(original_size, "height")?,
            task_name: task
                .and_then(|task| get(task, "name"))
                .and_then(text)
                .unwrap_or_default()
                .to_owned(),
            start_frame,
            stop_frame,
            size,
        })
    }

    /// The file's metadata as the summary carries it.
    pub(super) fn into_metadata(
        self,
        labels: &BTreeSet<ClassType>,
        all_metadata: Fields,
    ) -> Fields {
        let string = |text: String| Value::Str(Name::Owned(text));
        let cvat = vec![
            ("width".into(), Value::Int(self.width)),
            ("height".into(), Value::Int(self.height)),
            ("task_name".into(), string(self.task_name)),
            ("start_frame".into(), Value::Int(self.start_frame)),
            ("stop_frame".into(), Value::Int(self.stop_frame)),
            ("size".into(), Value::Int(self.size)),
            (
                "labels".into(),
                Value::List(
                    labels
                        .iter()
                        .map(|label| string(label.as_str().to_owned()))
                        .collect(),
                ),
            ),
            ("all_metadata".into(), Value::Map(all_metadata)),
        ];
        vec![("cvat".into(), Value::Map(cvat))]
    }
}
