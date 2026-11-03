//! Typed reads of a manifest's JSON objects. Each read takes the field out, so what is left is unknown, and every
//! error names the field by its path from the top, such as `handlers[1].options[0].kind`.

use std::collections::HashSet;

use handler_api::{Field, FieldKind, Form};
use scene::{MatchBasis, SCENE_VERSION, SceneVersion};
use serde_json::{Map, Value};

use crate::manifest::{DecoderHandler, Handler, ManifestError, ResolverHandler, valid_id};

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
                "playlist_resolver" => Handler::PlaylistResolver(read.resolver()?),
                other => {
                    return Err(read.error(
                        "kind",
                        format!(
                            "'{other}' is not a handler kind this ax-devil reads (decoder, playlist_resolver)"
                        ),
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
        let options = self.form("options", false)?;
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

    fn resolver(&mut self) -> Result<ResolverHandler, ManifestError> {
        Ok(ResolverHandler {
            id: self.id("id")?,
            name: self.non_empty("name")?,
            description: self.non_empty("description")?,
            settings: self.form("settings", true)?,
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

    /// The list `key` of fields; a decoder's options are text, files or folders, a resolver's settings may be any
    /// kind (`all_kinds`).
    fn form(&mut self, key: &str, all_kinds: bool) -> Result<Form, ManifestError> {
        let Some(list) = self.map.remove(key) else {
            return Ok(Form::default());
        };
        let Value::Array(items) = list else {
            return Err(self.error(key, "must be a list"));
        };
        let mut flags = HashSet::new();
        let mut fields: Vec<(String, Field)> = Vec::with_capacity(items.len());
        for (index, item) in items.into_iter().enumerate() {
            let at = self.path(&format!("{key}[{index}]"));
            let Value::Object(mut map) = item else {
                return Err(ManifestError::new(at, "must be an object"));
            };
            let mut read = Fields::new(&mut map, format!("{at}."));
            let field = read.field(all_kinds)?;
            read.finish()?;
            // `--a_b` and `--a-b` are one flag.
            if !flags.insert(field.key.replace('_', "-")) {
                return Err(ManifestError::new(
                    format!("{at}.key"),
                    format!(
                        "'{}' is declared twice (a key's '_' and '-' make the same flag)",
                        field.key
                    ),
                ));
            }
            fields.push((at, field));
        }
        for (at, field) in &fields {
            let named = |name: &str| fields.iter().find(|(_, other)| other.key == name);
            let broken = match &field.kind {
                FieldKind::Scan { depends_on } => depends_on
                    .iter()
                    .find(|name| **name == field.key || named(name).is_none())
                    .map(|bad| ("depends_on", bad, "another setting")),
                FieldKind::DecoderOptions { handler_field }
                    if !named(handler_field)
                        .is_some_and(|(_, other)| other.kind == FieldKind::Handler) =>
                {
                    Some(("handler_field", handler_field, "a handler setting"))
                }
                _ => None,
            };
            if let Some((name, bad, target)) = broken {
                return Err(ManifestError::new(
                    format!("{at}.{name}"),
                    format!("'{bad}' must be the key of {target}"),
                ));
            }
        }
        Ok(Form {
            fields: fields.into_iter().map(|(_, field)| field).collect(),
        })
    }

    /// One declared field. Its value is text, except for an integer, a scan's list and decoder options' map.
    fn field(&mut self, all_kinds: bool) -> Result<Field, ManifestError> {
        let key = self.non_empty("key")?;
        if !key
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
            || key == "help"
        {
            return Err(self.error(
                "key",
                format!("'{key}' must be letters, digits, '_' or '-', and not help"),
            ));
        }
        let label = self.non_empty("label")?;
        let kind = match self.string("kind")?.as_str() {
            "text" => FieldKind::Text,
            "file" => FieldKind::File {
                extensions: self.extensions()?,
            },
            "folder" => FieldKind::Folder,
            "integer" if all_kinds => FieldKind::Integer {
                min: self.optional_integer("min")?.unwrap_or(0),
            },
            "handler" if all_kinds => FieldKind::Handler,
            "decoder_options" if all_kinds => FieldKind::DecoderOptions {
                handler_field: self.non_empty("handler_field")?,
            },
            "scan" if all_kinds => FieldKind::Scan {
                depends_on: self.strings("depends_on", true)?.unwrap_or_default(),
            },
            other => {
                let kinds = if all_kinds {
                    "text, file, folder, integer, handler, decoder_options or scan"
                } else {
                    "text, file or folder"
                };
                return Err(self.error("kind", format!("'{other}' is not {kinds}")));
            }
        };
        let required = self.optional_bool("required")?;
        let default = match (self.map.remove("default"), &kind) {
            (None | Some(Value::Null), _) => None,
            (
                Some(Value::String(text)),
                FieldKind::Text | FieldKind::File { .. } | FieldKind::Folder,
            ) => Some(Value::String(text)),
            (Some(number), FieldKind::Integer { min }) => match number.as_i64() {
                Some(value) if value >= *min => Some(number),
                _ => {
                    return Err(self.error(
                        "default",
                        format!("must be a whole number of at least {min}"),
                    ));
                }
            },
            (Some(_), FieldKind::Text | FieldKind::File { .. } | FieldKind::Folder) => {
                return Err(self.error("default", "must be text"));
            }
            (Some(_), _) => return Err(self.error("default", "is not allowed for this kind")),
        };
        let positional = self.optional_bool("positional")?;
        if positional
            && (!required
                || matches!(
                    kind,
                    FieldKind::Scan { .. } | FieldKind::DecoderOptions { .. }
                ))
        {
            return Err(self.error(
                "positional",
                "is for a required setting with a single value",
            ));
        }
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
