//! Reading the list of fields a decoder's options or a resolver's settings declare.

use std::collections::HashSet;

use handler_api::{Field, FieldKind, Form};
use serde_json::Value;

use super::{Fields, RESERVED_SETTING_KEYS};
use crate::manifest::ManifestError;

impl Fields<'_> {
    /// The list `key` of fields; a decoder's options are text, files or folders, a resolver's settings may be any
    /// kind (`all_kinds`).
    pub(crate) fn form(&mut self, key: &str, all_kinds: bool) -> Result<Form, ManifestError> {
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
            // The Load Playlist button sits beside a counted scan, so a second one would repeat it.
            let counted =
                |field: &Field| matches!(field.kind, FieldKind::Scan { count: Some(_), .. });
            if counted(&field)
                && let Some((_, first)) = fields.iter().find(|(_, other)| counted(other))
            {
                return Err(ManifestError::new(
                    format!("{at}.count"),
                    format!(
                        "'{}' is a second counting scan; '{}' is the only one allowed",
                        field.key, first.key
                    ),
                ));
            }
            fields.push((at, field));
        }
        for (at, field) in &fields {
            let named = |name: &str| fields.iter().find(|(_, other)| other.key == name);
            let broken = match &field.kind {
                FieldKind::Scan { depends_on, .. } => depends_on
                    .iter()
                    .find(|name| {
                        !named(name).is_some_and(|(_, other)| {
                            !matches!(
                                other.kind,
                                FieldKind::Scan { .. } | FieldKind::DecoderOptions { .. }
                            )
                        })
                    })
                    .map(|bad| {
                        (
                            "depends_on",
                            bad,
                            "a text, file, folder, integer or handler setting",
                        )
                    }),
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

    /// The `key` of a field or a lane input: letters, digits, '_' or '-', starting with a letter or digit.
    pub(super) fn key(&mut self) -> Result<String, ManifestError> {
        let key = self.non_empty("key")?;
        if !key.starts_with(|c: char| c.is_ascii_alphanumeric())
            || !key
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
        {
            return Err(self.error(
                "key",
                format!(
                    "'{key}' must be letters, digits, '_' or '-', starting with a letter or digit"
                ),
            ));
        }
        Ok(key)
    }

    /// One declared field. Its value is text, except for an integer, a scan's list and decoder options' map.
    fn field(&mut self, all_kinds: bool) -> Result<Field, ManifestError> {
        let key = self.key()?;
        // Only a resolver's settings become arguments of the command line.
        if all_kinds && RESERVED_SETTING_KEYS.contains(&key.replace('_', "-").as_str()) {
            return Err(self.error(
                "key",
                format!("'{key}' is an argument ax-devil already takes"),
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
                depends_on: self.strings("depends_on", false)?.unwrap_or_default(),
                count: self.scan_count()?,
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
        if required && matches!(kind, FieldKind::Scan { .. }) {
            return Err(self.error(
                "required",
                "is not allowed for a scan: its values are picked",
            ));
        }
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
                || default.is_some()
                || matches!(
                    kind,
                    FieldKind::Scan { .. } | FieldKind::DecoderOptions { .. }
                ))
        {
            return Err(self.error(
                "positional",
                "is for a required setting with a single value and no default",
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
