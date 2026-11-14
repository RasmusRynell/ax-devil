//! Typed reads of a manifest's JSON objects. Each read takes the field out, so what is left is unknown, and every
//! error names the field by its path from the top, such as `handlers[1].options[0].kind`.

use std::{collections::HashSet, path::Path};

use handler_api::ScanCount;
use scene::{MatchBasis, SCENE_VERSION, SceneVersion};
use serde_json::{Map, Value};

mod form;
mod kinds;

use crate::{
    LaneInputs,
    manifest::{
        DecoderHandler, DecoderInput, Handler, ManifestError, ResolverHandler, SkippedHandler,
        valid_id,
    },
};

/// The flag names (`_` written as `-`) that every `ax-devil` command line already takes, so no resolver setting may
/// be named one: clap refuses an argument that repeats them. A test of the real command keeps this in step.
pub const RESERVED_SETTING_KEYS: [&str; 4] = ["help", "config", "cache-dir", "log-level"];

/// Why a handler of an unknown kind is not used.
const NEWER: &str = "needs a newer ax-devil";

/// One JSON object being read; `prefix` is the path to it, ending in `.` unless it is the manifest itself.
pub(crate) struct Fields<'a> {
    map: &'a mut Map<String, Value>,
    prefix: String,
}

impl<'a> Fields<'a> {
    pub(crate) fn new(map: &'a mut Map<String, Value>, prefix: String) -> Self {
        Self { map, prefix }
    }

    fn path(&self, key: &str) -> String {
        format!("{}{key}", self.prefix)
    }

    fn error(&self, key: &str, reason: impl Into<String>) -> ManifestError {
        ManifestError::new(self.path(key), reason)
    }

    /// Refuses whatever no read took.
    pub(crate) fn finish(self) -> Result<(), ManifestError> {
        match self.map.keys().next() {
            Some(unknown) => Err(self.error(unknown, "unknown field")),
            None => Ok(()),
        }
    }

    fn take(&mut self, key: &str) -> Result<Value, ManifestError> {
        self.map
            .remove(key)
            .ok_or_else(|| self.error(key, "is required"))
    }

    pub(crate) fn string(&mut self, key: &str) -> Result<String, ManifestError> {
        match self.take(key)? {
            Value::String(text) => Ok(text),
            _ => Err(self.error(key, "must be text")),
        }
    }

    pub(crate) fn non_empty(&mut self, key: &str) -> Result<String, ManifestError> {
        let text = self.string(key)?;
        if text.trim().is_empty() {
            return Err(self.error(key, "must not be empty"));
        }
        Ok(text)
    }

    fn optional_non_empty(&mut self, key: &str) -> Result<Option<String>, ManifestError> {
        if self.map.contains_key(key) {
            self.non_empty(key).map(Some)
        } else {
            Ok(None)
        }
    }

    pub(crate) fn id(&mut self, key: &str) -> Result<String, ManifestError> {
        let id = self.string(key)?;
        if valid_id(&id) {
            Ok(id)
        } else {
            Err(self.error(
                key,
                format!(
                    "'{id}' must be 1-64 characters of a-z, 0-9, '.', '_' or '-', starting with a letter or digit"
                ),
            ))
        }
    }

    pub(crate) fn number(&mut self, key: &str) -> Result<u64, ManifestError> {
        self.take(key)?
            .as_u64()
            .ok_or_else(|| self.error(key, "must be a whole number"))
    }

    pub(crate) fn optional_number(&mut self, key: &str) -> Result<Option<u64>, ManifestError> {
        if self.map.contains_key(key) {
            self.number(key).map(Some)
        } else {
            Ok(None)
        }
    }

    fn optional_integer(&mut self, key: &str) -> Result<Option<i64>, ManifestError> {
        match self.map.remove(key) {
            None => Ok(None),
            Some(value) => value
                .as_i64()
                .map(Some)
                .ok_or_else(|| self.error(key, "must be a whole number")),
        }
    }

    fn optional_bool(&mut self, key: &str) -> Result<bool, ManifestError> {
        match self.map.remove(key) {
            None => Ok(false),
            Some(Value::Bool(flag)) => Ok(flag),
            Some(_) => Err(self.error(key, "must be true or false")),
        }
    }

    /// The optional `count` of a scan: the sentences of a scan that only counts.
    fn scan_count(&mut self) -> Result<Option<ScanCount>, ManifestError> {
        let Some(value) = self.map.remove("count") else {
            return Ok(None);
        };
        let Value::Object(mut map) = value else {
            return Err(self.error("count", "must be an object"));
        };
        let mut sentences = Fields::new(&mut map, self.path("count."));
        let count = ScanCount {
            missing: sentences.non_empty("missing")?,
            found: sentences.non_empty("found")?,
            none: sentences.non_empty("none")?,
        };
        sentences.finish()?;
        if !count.found.contains("{count}") {
            return Err(self.error("count.found", "must contain {count}"));
        }
        Ok(Some(count))
    }

    /// A list of text; absent is `None` unless `required`, which refuses an empty list as well.
    pub(crate) fn strings(
        &mut self,
        key: &str,
        required: bool,
    ) -> Result<Option<Vec<String>>, ManifestError> {
        if !required && !self.map.contains_key(key) {
            return Ok(None);
        }
        let Value::Array(items) = self.take(key)? else {
            return Err(self.error(key, "must be a list of text"));
        };
        let texts: Vec<String> = items
            .into_iter()
            .map(|item| match item {
                Value::String(text) => Ok(text),
                _ => Err(self.error(key, "must be a list of text")),
            })
            .collect::<Result<_, _>>()?;
        if required && texts.is_empty() {
            return Err(self.error(key, "must not be empty"));
        }
        Ok(Some(texts))
    }

    /// File name endings: lowercase, with the dot; none when absent.
    fn extensions(&mut self) -> Result<Vec<String>, ManifestError> {
        let extensions = self.strings("extensions", false)?.unwrap_or_default();
        for extension in &extensions {
            if extension.len() < 2
                || !extension.starts_with('.')
                || *extension != extension.to_lowercase()
                || extension.contains(['/', '\\'])
                || extension.contains(char::is_whitespace)
            {
                return Err(self.error(
                    "extensions",
                    format!(
                        "'{extension}' must be lowercase and start with a dot, such as .acme.jsonl"
                    ),
                ));
            }
        }
        Ok(extensions)
    }

    /// The `handlers` list: at least one, with ids that differ. A handler of a kind this build does not know is set
    /// aside unread, since its fields are not ours to judge, and so is one that refers to something missing (see
    /// `kinds::resolve`); the plugin fails only if none is left.
    pub(crate) fn handlers(
        &mut self,
        folder: &Path,
    ) -> Result<(Vec<Handler>, Vec<SkippedHandler>), ManifestError> {
        let Value::Array(items) = self.take("handlers")? else {
            return Err(self.error("handlers", "must be a list"));
        };
        if items.is_empty() {
            return Err(self.error("handlers", "must list at least one handler"));
        }
        let mut seen = HashSet::new();
        let mut handlers = Vec::with_capacity(items.len());
        let mut skipped = Vec::new();
        for (index, item) in items.into_iter().enumerate() {
            let at = self.path(&format!("handlers[{index}]"));
            let Value::Object(mut map) = item else {
                return Err(ManifestError::new(at, "must be an object"));
            };
            let mut read = Fields::new(&mut map, format!("{at}."));
            let handler = match read.string("kind")?.as_str() {
                "decoder" => Handler::Decoder(read.decoder()?),
                "playlist_resolver" => Handler::PlaylistResolver(read.resolver()?),
                "task" => Handler::Task(read.task()?),
                "view" => Handler::View(read.view()?),
                "command" => Handler::Command(read.command()?),
                other => {
                    skipped.push(SkippedHandler {
                        kind: other.to_owned(),
                        id: read
                            .map
                            .get("id")
                            .and_then(Value::as_str)
                            .map(str::to_owned),
                        reason: NEWER.to_owned(),
                    });
                    continue;
                }
            };
            read.finish()?;
            if !seen.insert(handler.id().to_owned()) {
                return Err(ManifestError::new(
                    format!("{at}.id"),
                    format!("'{}' is declared twice", handler.id()),
                ));
            }
            handlers.push(handler);
        }
        let (handlers, unresolved) = kinds::resolve(handlers, folder);
        skipped.extend(unresolved);
        if handlers.is_empty() {
            let reason = skipped.first().map_or(NEWER, |first| first.reason.as_str());
            return Err(self.error("handlers", reason));
        }
        Ok((handlers, skipped))
    }

    fn decoder(&mut self) -> Result<DecoderHandler, ManifestError> {
        let id = self.id("id")?;
        let name = self.non_empty("name")?;
        let input = match self.string("input")?.as_str() {
            "path" => DecoderInput::Path,
            "records" => DecoderInput::Records,
            "lanes" => DecoderInput::Lanes,
            other => {
                return Err(self.error(
                    "input",
                    format!("'{other}' is not \"path\", \"records\" or \"lanes\""),
                ));
            }
        };
        let description = self.optional_non_empty("description")?;
        let scene_version = self.scene_version()?;
        // A lanes decoder reads the host's bundle, never a file the user names, so it has no extensions.
        let (inputs, extensions) = if input == DecoderInput::Lanes {
            (self.lane_inputs()?, Vec::new())
        } else {
            (LaneInputs::default(), self.extensions()?)
        };
        let options = self.form("options", false)?;
        let (bases, default_basis) = self.bases()?;
        Ok(DecoderHandler {
            id,
            name,
            input,
            inputs,
            description,
            scene_version,
            extensions,
            options,
            bases,
            default_basis,
        })
    }

    fn resolver(&mut self) -> Result<ResolverHandler, ManifestError> {
        let id = self.id("id")?;
        if id == "help" {
            return Err(self.error("id", "'help' is taken by the command line's own help"));
        }
        Ok(ResolverHandler {
            id,
            name: self.non_empty("name")?,
            description: self.non_empty("description")?,
            settings: self.form("settings", true)?,
            watches: self.optional_bool("watches")?,
        })
    }

    fn scene_version(&mut self) -> Result<SceneVersion, ManifestError> {
        let text = self.string("scene_version")?;
        let number = |part: Option<&str>| part.and_then(|part| part.parse::<u16>().ok());
        let mut parts = text.split('.');
        let (Some(major), Some(minor), None) =
            (number(parts.next()), number(parts.next()), parts.next())
        else {
            return Err(self.error(
                "scene_version",
                format!("'{text}' is not a version such as {SCENE_VERSION}"),
            ));
        };
        let declared = SceneVersion { major, minor };
        if SCENE_VERSION.accepts(declared) {
            Ok(declared)
        } else {
            Err(self.error(
                "scene_version",
                format!(
                    "{declared} is not readable by this ax-devil (Scene version {SCENE_VERSION})"
                ),
            ))
        }
    }

    /// The bases and the default one: `["frame"]` and the first listed when absent.
    fn bases(&mut self) -> Result<(Vec<MatchBasis>, MatchBasis), ManifestError> {
        let basis = |this: &Self, key: &str, text: &str| match text {
            "frame" => Ok(MatchBasis::Frame),
            "time" => Ok(MatchBasis::Time),
            other => Err(this.error(key, format!("'{other}' is not frame or time"))),
        };
        let bases = match self.strings("bases", false)? {
            None => vec![MatchBasis::Frame],
            Some(texts) => texts
                .iter()
                .map(|text| basis(self, "bases", text))
                .collect::<Result<Vec<_>, _>>()?,
        };
        if bases.is_empty() || (1..bases.len()).any(|i| bases[..i].contains(&bases[i])) {
            return Err(self.error("bases", "must list frame, time or both, once each"));
        }
        let default = match self.map.remove("default_basis") {
            None => bases[0],
            Some(Value::String(text)) => basis(self, "default_basis", &text)?,
            Some(_) => return Err(self.error("default_basis", "must be text")),
        };
        if !bases.contains(&default) {
            return Err(self.error("default_basis", "must be one of bases"));
        }
        Ok((bases, default))
    }
}
