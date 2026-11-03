//! Typed reads of a manifest's JSON objects. Each read takes the field out, so what is left is unknown, and every
//! error names the field by its path from the top, such as `handlers[1].options[0].kind`.

use std::collections::HashSet;

use handler_api::{Field, FieldKind, Form};
use scene::{MatchBasis, SCENE_VERSION, SceneVersion};
use serde_json::{Map, Value};

use crate::manifest::{DecoderHandler, Handler, ManifestError, valid_id};

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

    fn optional_bool(&mut self, key: &str) -> Result<bool, ManifestError> {
        match self.map.remove(key) {
            None => Ok(false),
            Some(Value::Bool(flag)) => Ok(flag),
            Some(_) => Err(self.error(key, "must be true or false")),
        }
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

    /// The `handlers` list: at least one, with ids that differ.
    pub(crate) fn handlers(&mut self) -> Result<Vec<Handler>, ManifestError> {
        let Value::Array(items) = self.take("handlers")? else {
            return Err(self.error("handlers", "must be a list"));
        };
        if items.is_empty() {
            return Err(self.error("handlers", "must list at least one handler"));
        }
        let mut seen = HashSet::new();
        let mut handlers = Vec::with_capacity(items.len());
        for (index, item) in items.into_iter().enumerate() {
            let at = self.path(&format!("handlers[{index}]"));
            let Value::Object(mut map) = item else {
                return Err(ManifestError::new(at, "must be an object"));
            };
            let mut read = Fields::new(&mut map, format!("{at}."));
            let handler = match read.string("kind")?.as_str() {
                "decoder" => Handler::Decoder(read.decoder()?),
                other => {
                    return Err(read.error(
                        "kind",
                        format!("'{other}' is not a handler kind this ax-devil reads (decoder)"),
                    ));
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
        Ok(handlers)
    }

    fn decoder(&mut self) -> Result<DecoderHandler, ManifestError> {
        let id = self.id("id")?;
        let name = self.non_empty("name")?;
        match self.string("input")?.as_str() {
            "path" => {}
            other => {
                return Err(self.error(
                    "input",
                    format!("'{other}' is not supported; only \"path\" is"),
                ));
            }
        }
        let scene_version = self.scene_version()?;
        let extensions = self.extensions()?;
        let options = self.options()?;
        let (bases, default_basis) = self.bases()?;
        Ok(DecoderHandler {
            id,
            name,
            scene_version,
            extensions,
            options,
            bases,
            default_basis,
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

    fn options(&mut self) -> Result<Form, ManifestError> {
        let Some(options) = self.map.remove("options") else {
            return Ok(Form::default());
        };
        let Value::Array(items) = options else {
            return Err(self.error("options", "must be a list"));
        };
        let mut seen = HashSet::new();
        let mut fields = Vec::with_capacity(items.len());
        for (index, item) in items.into_iter().enumerate() {
            let at = self.path(&format!("options[{index}]"));
            let Value::Object(mut map) = item else {
                return Err(ManifestError::new(at, "must be an object"));
            };
            let mut read = Fields::new(&mut map, format!("{at}."));
            let field = read.option()?;
            read.finish()?;
            if !seen.insert(field.key.clone()) {
                return Err(ManifestError::new(
                    format!("{at}.key"),
                    format!("'{}' is declared twice", field.key),
                ));
            }
            fields.push(field);
        }
        Ok(Form { fields })
    }

    /// One declared option of a decoder: text, a file or a folder, whose value is always text.
    fn option(&mut self) -> Result<Field, ManifestError> {
        let key = self.non_empty("key")?;
        if !key
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
        {
            return Err(self.error(
                "key",
                format!("'{key}' must be letters, digits, '_' or '-'"),
            ));
        }
        let label = self.non_empty("label")?;
        let kind = match self.string("kind")?.as_str() {
            "text" => FieldKind::Text,
            "file" => FieldKind::File {
                extensions: self.extensions()?,
            },
            "folder" => FieldKind::Folder,
            other => {
                return Err(self.error("kind", format!("'{other}' is not text, file or folder")));
            }
        };
        let required = self.optional_bool("required")?;
        let default = match self.map.remove("default") {
            None | Some(Value::Null) => None,
            Some(Value::String(default)) => Some(Value::String(default)),
            Some(_) => return Err(self.error("default", "must be text")),
        };
        let positional = self.optional_bool("positional")?;
        let field = Field {
            required,
            default,
            ..Field::new(&key, &label, kind)
        };
        Ok(if positional {
            field.positional()
        } else {
            field
        })
    }
}
